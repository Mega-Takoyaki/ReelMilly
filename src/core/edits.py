"""動画の編集(切り出しなど)。編集結果は元の動画とは別のファイル(サブ動画)として、作品に複数登録できる。

- 元の動画は変更しない。結果は作品のフォルダの`edits/`に置く
- 処理(ffmpeg)は時間がかかるため、まず「待機中」で登録し、ワーカー(`reelmilly watch`)が非同期で行う。完了は通知に残る
- 1作品に登録できる数には上限がある(失敗したものは数えない)
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from core import ffmpeg, mask, overlay

MAX_EDITS_PER_ASSET = 10
KINDS = ("trim", "mask", "frame", "overlay")
FRAME_FORMATS = {"jpg": "JPG", "png": "PNG"}
FRAME_SCALES = (1, 2, 3, 4)
TRIM_MODES = {"accurate": "正確", "fast": "高速"}


class EditError(ValueError):
    """入力が正しくない・上限に達したなど、利用者に伝える理由つきの失敗。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fmt_time(seconds: float) -> str:
    """秒を `m:ss.s` の形にする(例: 2.0 → 0:02.0)。"""
    tenths = int(round(seconds * 10))
    m, rest = divmod(tenths, 600)
    return f"{m}:{rest // 10:02d}.{rest % 10}"


def summarize(kind: str, params: dict) -> str:
    if kind == "trim":
        text = f"トリム {fmt_time(params['start'])}～{fmt_time(params['end'])}"
        return text + ("（高速）" if params.get("mode") == "fast" else "")
    if kind == "mask":
        return mask.summarize(params)
    if kind == "overlay":
        return overlay.summarize(params)
    if kind == "frame":
        text = f"静止画 {fmt_time(params['time'])} {FRAME_FORMATS.get(params['format'], params['format'])}"
        return text + (f"（{params['scale']}倍に拡大）" if params.get("scale", 1) > 1 else "")
    return kind


def kind_label(edit: dict) -> str:
    """一覧に出す、加工の種類の名前。"""
    if edit["kind"] == "mask":
        return mask.STYLES.get(edit["params"].get("style"), "ぼかし・モザイク")
    return {"frame": "静止画", "overlay": "テロップ・スタンプ"}.get(edit["kind"], "切り出し")


def source_info(conn: sqlite3.Connection, asset: dict, source: str | None) -> tuple[Path, str]:
    """加工のもとにするファイル(`original`/`wm`/`edit:<ID>`)の、パスとメディアの種類。使えないときは`EditError`。

    原本だけでなく、完成した加工版や透かし入りも、もとにできる(例: 動画から取り出した静止画に、スタンプをつける)。
    """
    from core import versions

    try:
        return versions.resolve(conn, asset, source or "original")
    except versions.VersionError as exc:
        raise EditError(str(exc)) from exc


def media_type(asset: dict, edit: dict) -> str:
    """加工版の、メディアの種類(image|video)。切り出しは動画、静止画の取り出しは画像、ぼかし・テロップは、加工のもとと同じ。"""
    recorded = (edit.get("params") or {}).get("media")
    if recorded in ("image", "video"):
        return recorded
    if edit["kind"] == "frame":
        return "image"
    return asset["kind"] if edit["kind"] in ("mask", "overlay") else "video"


def edits_dir(asset: dict) -> Path:
    return Path(asset["file_path"]).parent / "edits"


def edit_path(asset: dict, edit: dict) -> Path | None:
    return edits_dir(asset) / edit["filename"] if edit.get("filename") else None


def _row(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["params"] = json.loads(data["params"] or "{}")
    return data


def list_edits(conn: sqlite3.Connection, asset_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM asset_edits WHERE asset_id = ? ORDER BY id DESC", (asset_id,)).fetchall()
    return [_row(r) for r in rows]


def get_edit(conn: sqlite3.Connection, asset_id: str, edit_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM asset_edits WHERE id = ? AND asset_id = ?", (edit_id, asset_id)).fetchone()
    return _row(row) if row else None


def active_count(conn: sqlite3.Connection, asset_id: str) -> int:
    """上限に数える件数(失敗したものは数えない)。"""
    return conn.execute(
        "SELECT COUNT(*) FROM asset_edits WHERE asset_id = ? AND status != 'failed'", (asset_id,)
    ).fetchone()[0]


def pending_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM asset_edits WHERE status IN ('queued', 'running')").fetchone()[0]


def enqueue_trim(conn: sqlite3.Connection, asset: dict, start, end, mode: str = "accurate", source: str = "original") -> int:
    """切り出しを待機中として登録する。登録したIDを返す。入力が不正・上限なら`EditError`。"""
    path, media = source_info(conn, asset, source)
    if media != "video":
        raise EditError("動画だけが対象です")
    try:
        start, end = round(float(start), 3), round(float(end), 3)
    except (TypeError, ValueError) as exc:
        raise EditError("開始・終了の位置を、秒の数値で指定してください") from exc
    if mode not in TRIM_MODES:
        mode = "accurate"
    if start < 0 or end <= start:
        raise EditError("終了は、開始より後にしてください")
    if end - start < ffmpeg.MIN_CLIP_SECONDS:
        raise EditError("切り出す範囲が短すぎます(0.1秒以上にしてください)")
    duration = ffmpeg.probe_duration(path)
    if duration is not None and start >= duration:
        raise EditError(f"開始が動画の長さ({duration:.1f}秒)を超えています")
    if duration is not None and end > duration:
        end = round(duration, 3)  # 少し超える分は、最後までとして扱う
    if active_count(conn, asset["id"]) >= MAX_EDITS_PER_ASSET:
        raise EditError(f"1つの作品に登録できる編集動画は{MAX_EDITS_PER_ASSET}件までです。不要なものを削除してください")
    params = {"start": start, "end": end, "mode": mode, "source": source or "original", "media": "video"}
    # 失敗の記録は、次の登録で片付ける
    conn.execute("DELETE FROM asset_edits WHERE asset_id = ? AND status = 'failed'", (asset["id"],))
    cur = conn.execute(
        "INSERT INTO asset_edits (asset_id, kind, params, summary, status, created_at) VALUES (?, 'trim', ?, ?, 'queued', ?)",
        (asset["id"], json.dumps(params), summarize("trim", params), _now()),
    )
    conn.commit()
    return cur.lastrowid


def enqueue_mask(conn: sqlite3.Connection, asset: dict, regions, style: str, strength, source: str = "original") -> int:
    """ぼかし・モザイクを待機中として登録する(静止画・動画とも)。登録したIDを返す。入力が不正・上限なら`EditError`。"""
    _path, media = source_info(conn, asset, source)
    try:
        params = mask.clean_params(regions, style, strength)
    except mask.MaskError as exc:
        raise EditError(str(exc)) from exc
    params.update(source=source or "original", media=media)
    if active_count(conn, asset["id"]) >= MAX_EDITS_PER_ASSET:
        raise EditError(f"1つの作品に登録できる加工版は{MAX_EDITS_PER_ASSET}件までです。不要なものを削除してください")
    conn.execute("DELETE FROM asset_edits WHERE asset_id = ? AND status = 'failed'", (asset["id"],))
    cur = conn.execute(
        "INSERT INTO asset_edits (asset_id, kind, params, summary, status, created_at) VALUES (?, 'mask', ?, ?, 'queued', ?)",
        (asset["id"], json.dumps(params), summarize("mask", params), _now()),
    )
    conn.commit()
    return cur.lastrowid


def enqueue_overlay(conn: sqlite3.Connection, asset: dict, layers, source: str = "original") -> int:
    """テロップ・スタンプの挿入を待機中として登録する(静止画・動画とも)。登録したIDを返す。"""
    _path, media = source_info(conn, asset, source)
    try:
        params = overlay.clean_params(conn, layers)
    except overlay.OverlayError as exc:
        raise EditError(str(exc)) from exc
    params.update(source=source or "original", media=media)
    if active_count(conn, asset["id"]) >= MAX_EDITS_PER_ASSET:
        raise EditError(f"1つの作品に登録できる加工版は{MAX_EDITS_PER_ASSET}件までです。不要なものを削除してください")
    conn.execute("DELETE FROM asset_edits WHERE asset_id = ? AND status = 'failed'", (asset["id"],))
    cur = conn.execute(
        "INSERT INTO asset_edits (asset_id, kind, params, summary, status, created_at) VALUES (?, 'overlay', ?, ?, 'queued', ?)",
        (asset["id"], json.dumps(params), summarize("overlay", params), _now()),
    )
    conn.commit()
    return cur.lastrowid


def enqueue_frame(conn: sqlite3.Connection, asset: dict, at, fmt: str = "jpg", scale=1, source: str = "original") -> int:
    """動画の1コマを、静止画の加工版として待機中に登録する。登録したIDを返す。入力が不正・上限なら`EditError`。"""
    path, media = source_info(conn, asset, source)
    if media != "video":
        raise EditError("動画だけが対象です")
    try:
        at, scale = round(float(at), 3), int(scale or 1)
    except (TypeError, ValueError) as exc:
        raise EditError("位置は秒の数値、倍率は整数で指定してください") from exc
    if fmt not in FRAME_FORMATS:
        raise EditError("形式は、jpgかpngです")
    if scale not in FRAME_SCALES:
        raise EditError("拡大の倍率は、1〜4倍です")
    if at < 0:
        raise EditError("位置は、0秒以上にしてください")
    duration = ffmpeg.probe_duration(path)
    if duration is not None and at >= duration:
        at = round(max(duration - 0.05, 0.0), 3)  # 最後のコマ
    if active_count(conn, asset["id"]) >= MAX_EDITS_PER_ASSET:
        raise EditError(f"1つの作品に登録できる加工版は{MAX_EDITS_PER_ASSET}件までです。不要なものを削除してください")
    params = {"time": at, "format": fmt, "scale": scale, "source": source or "original", "media": "image"}
    conn.execute("DELETE FROM asset_edits WHERE asset_id = ? AND status = 'failed'", (asset["id"],))
    cur = conn.execute(
        "INSERT INTO asset_edits (asset_id, kind, params, summary, status, created_at) VALUES (?, 'frame', ?, ?, 'queued', ?)",
        (asset["id"], json.dumps(params), summarize("frame", params), _now()),
    )
    conn.commit()
    return cur.lastrowid


def delete_edit(conn: sqlite3.Connection, asset: dict, edit_id: int) -> bool:
    """編集動画を、ファイルごと削除する。処理中のものは削除できない(`EditError`)。"""
    edit = get_edit(conn, asset["id"], edit_id)
    if edit is None:
        return False
    if edit["status"] == "running":
        raise EditError("処理中のため削除できません。終わってからやり直してください")
    for other in list_edits(conn, asset["id"]):
        if other["status"] in ("queued", "running") and other["params"].get("source") == f"edit:{edit_id}":
            raise EditError("この加工版をもとに、加工中のものがあります。終わってから削除してください")
    path = edit_path(asset, edit)
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:  # Windowsでは、再生・ダウンロード中のファイルは消せない
            raise EditError("ファイルを削除できませんでした(再生中などで使用中の可能性があります。少し待って、やり直してください)") from exc
    conn.execute("DELETE FROM asset_edits WHERE id = ?", (edit_id,))
    conn.commit()
    return True


def remove_files(conn: sqlite3.Connection, asset: dict) -> None:
    """作品の完全削除のときに、編集動画のファイルを消す(作品専用のフォルダでない古い配置でも残さない)。"""
    for edit in list_edits(conn, asset["id"]):
        path = edit_path(asset, edit)
        if path is not None:
            path.unlink(missing_ok=True)  # 失敗したら、呼び出し元(完全削除)が、その作品を削除せずに理由を返す


def requeue_running(conn: sqlite3.Connection) -> None:
    """ワーカーが処理の途中で止まったとき、処理中のまま残った編集を、待機中に戻す。"""
    conn.execute("UPDATE asset_edits SET status = 'queued' WHERE status = 'running'")
    conn.commit()


def claim_next(conn: sqlite3.Connection) -> dict | None:
    """待機中の編集を1件、処理中にして返す(他のプロセスと取り合っても、1件は1回だけ処理されるようにする)。"""
    while True:
        row = conn.execute("SELECT * FROM asset_edits WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return None
        cur = conn.execute("UPDATE asset_edits SET status = 'running' WHERE id = ? AND status = 'queued'", (row["id"],))
        conn.commit()
        if cur.rowcount == 1:
            return _row(row)


def run_edit(conn: sqlite3.Connection, edit: dict, asset: dict | None) -> str | None:
    """1件の編集を実行して、結果を記録する。失敗時は理由、成功時はNoneを返す。"""
    error = None
    filename = size = duration = None
    try:
        if asset is None:
            raise ffmpeg.FfmpegError("作品が見つかりません")
        if asset.get("deleted_at"):
            raise ffmpeg.FfmpegError("ごみ箱に入っている作品のため処理しませんでした")
        p = edit["params"]
        from core import versions

        try:
            src, src_media = versions.resolve(conn, asset, p.get("source") or "original")  # 加工のもと(原本・透かし入り・ほかの加工版)
        except versions.VersionError as exc:
            raise ffmpeg.FfmpegError(f"加工のもとのファイルを使えません: {exc}") from exc
        if edit["kind"] == "trim":
            suffix = src.suffix if p.get("mode") == "fast" else ".mp4"
        elif edit["kind"] == "frame":
            suffix = ".png" if p.get("format") == "png" else ".jpg"
        elif edit["kind"] in ("mask", "overlay"):
            suffix = ".mp4" if src_media == "video" else src.suffix
        else:
            raise ffmpeg.FfmpegError(f"未対応の編集です: {edit['kind']}")
        filename = f"{asset['id']}-edit-{edit['id']}{suffix}"
        dest = edits_dir(asset) / filename
        if edit["kind"] == "trim":
            ffmpeg.trim(src, dest, p["start"], p["end"], p.get("mode", "accurate"))
        elif edit["kind"] == "frame":
            ffmpeg.extract_frame(src, dest, p["time"], p.get("scale", 1))
        elif edit["kind"] == "overlay":
            if src_media == "video":
                ffmpeg.overlay_video(conn, src, dest, p)
            else:
                overlay.apply_image(conn, src, dest, p)
        elif src_media == "video":
            ffmpeg.mask_video(src, dest, p)
        else:
            mask.apply_image(src, dest, p)
        size = dest.stat().st_size
        duration = ffmpeg.probe_duration(dest) if media_type(asset, edit) == "video" else None
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
        filename = None
    conn.execute(
        "UPDATE asset_edits SET status = ?, error = ?, filename = ?, size_bytes = ?, duration = ?, finished_at = ? WHERE id = ?",
        ("failed" if error else "done", error, filename, size, duration, _now(), edit["id"]),
    )
    conn.commit()
    return error


def run_pending(conn: sqlite3.Connection, config, log=print) -> int:
    """待機中の編集を、すべて処理する(完了・失敗は、1件ずつ通知に残す)。処理した件数を返す。

    ストレージが見つからない間は、何もせず待機中のまま残す(つながったら処理する)。
    AI処理(数分かかる)とは別に、すぐ動かすためのもの(`reelmilly watch`の編集用スレッドが、数秒おきに呼ぶ)。
    """
    from core import notifications, storage
    from core.events import log_event

    storage.apply_override(config, conn)
    if not storage.check(config, conn).available:
        return 0
    count = 0
    while True:
        edit = claim_next(conn)
        if edit is None:
            return count
        asset = conn.execute("SELECT * FROM assets WHERE id = ?", (edit["asset_id"],)).fetchone()
        error = run_edit(conn, edit, dict(asset) if asset else None)
        count += 1
        if error:
            log(f"[edit] {edit['asset_id']}: {edit['summary']} 失敗 ({error})")
            notifications.add(conn, "edit", "加工版の作成に失敗しました", f"{edit['summary']}: {error}", "error", asset_id=edit["asset_id"])
            log_event(config.paths.events_path, "edit_failed", asset_id=edit["asset_id"], error=error)
        else:
            log(f"[edit] {edit['asset_id']}: {edit['summary']} 完了")
            notifications.add(conn, "edit", "加工版の作成が完了しました", edit["summary"], "success", asset_id=edit["asset_id"])
