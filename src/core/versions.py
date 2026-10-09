"""作品の「バージョン」(投稿に使えるファイルの種類)。

1つの作品には、元のファイルのほかに、透かし入りの画像や、編集した動画(サブ動画)がある。
「今すぐ投稿」では、作品ごとに、このどれを投稿するかを選ぶ。

バージョンのキー: `original`(元のファイル)/ `wm`(透かし入りの画像)/ `edit:<編集ID>`(編集した動画)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from core import edits


class VersionError(ValueError):
    """指定したバージョンが無い・ファイルが見つからないとき。"""


def list_versions(conn: sqlite3.Connection, asset: dict) -> list[dict]:
    """投稿に使えるバージョンの一覧(使えるものだけ)。各要素: key / label / media_type(image|video)。"""
    versions = [{
        "key": "original",
        "label": "元の動画" if asset["kind"] == "video" else "元の画像",
        "media_type": asset["kind"],
    }]
    if asset.get("wm_path") and Path(asset["wm_path"]).exists():
        versions.append({"key": "wm", "label": f"透かし入り（「{asset.get('wm_text') or ''}」）", "media_type": "image"})
    for edit in edits.list_edits(conn, asset["id"]):
        path = edits.edit_path(asset, edit)
        if edit["status"] == "done" and path is not None and path.exists():
            versions.append({"key": f"edit:{edit['id']}", "label": f"{edits.kind_label(edit)}: {edit['summary']}", "media_type": edits.media_type(asset, edit)})
    return versions


def default_version(versions: list[dict]) -> str:
    """既定のバージョン。透かし入りがあれば、それ(これまでの投稿と同じ扱い)。無ければ、元のファイル。"""
    keys = [v["key"] for v in versions]
    return "wm" if "wm" in keys else "original"


def resolve(conn: sqlite3.Connection, asset: dict, key: str | None) -> tuple[Path, str]:
    """バージョンのキーから、ファイルのパスと、メディアの種類(image|video)を返す。無ければ`VersionError`。"""
    key = key or default_version(list_versions(conn, asset))
    if key == "original":
        path, media_type = Path(asset["file_path"]), asset["kind"]
    elif key == "wm":
        if not asset.get("wm_path"):
            raise VersionError("この作品には、透かし入りがありません")
        path, media_type = Path(asset["wm_path"]), "image"
    elif key.startswith("edit:") and key[5:].isdigit():
        edit = edits.get_edit(conn, asset["id"], int(key[5:]))
        if edit is None or edit["status"] != "done":
            raise VersionError("その編集した動画は、使えません(処理中・失敗・削除済み)")
        path, media_type = edits.edit_path(asset, edit), edits.media_type(asset, edit)
    else:
        raise VersionError(f"不明なバージョンです: {key}")
    if path is None or not path.exists():
        raise VersionError(f"ファイルが見つかりません(ストレージを確認してください): {path.name if path else key}")
    return path, media_type


# ---------------------------------------------------------------- 区分(sfw/suggestive/explicit)と、ライブラリ
# 用語: 「原本」=取り込んだ元の作品のファイル。「加工版」=原本から作ったファイル(透かし入り・切り出し・今後のぼかし等)。
# 区分の方針:
#  - 加工版は、作った直後は「原本の区分を引き継ぐ」(原本が未承認なら、未承認)。安全側の既定
#  - 加工版ごとに、AI判定(sfw/nsfw)を実行でき、人が区分を承認すると、その加工版の区分が確定する(原本とは独立)
#    例: 原本はexplicitでも、見せたくない部分を切り落とした加工版は、sfwにできる
#  - 投稿では、選んだファイル(原本または加工版)の区分を使う
RATINGS = ("sfw", "suggestive", "explicit")
_RESTRICTIVENESS = {"sfw": 0, "suggestive": 1, "explicit": 2}


def _own_fields(conn: sqlite3.Connection, asset: dict, key: str) -> dict:
    """そのファイル自身の区分・AI判定。"""
    if key == "original":
        return {"rating": asset.get("content_rating"), "confirmed": bool(asset.get("content_rating_confirmed")),
                "auto": asset.get("nsfw_auto_rating"), "confidence": asset.get("nsfw_auto_confidence")}
    if key == "wm":
        return {"rating": asset.get("wm_content_rating"), "confirmed": bool(asset.get("wm_content_rating_confirmed")),
                "auto": asset.get("wm_nsfw_auto_rating"), "confidence": asset.get("wm_nsfw_auto_confidence")}
    if key.startswith("edit:") and key[5:].isdigit():
        row = conn.execute("SELECT * FROM asset_edits WHERE id = ? AND asset_id = ?", (int(key[5:]), asset["id"])).fetchone()
        if row:
            return {"rating": row["content_rating"], "confirmed": bool(row["content_rating_confirmed"]),
                    "auto": row["nsfw_auto_rating"], "confidence": row["nsfw_auto_confidence"]}
    raise VersionError(f"不明なファイルです: {key}")


def rating_info(conn: sqlite3.Connection, asset: dict, key: str) -> dict:
    """そのファイルの、いま使う区分。`source`: own(そのファイルで承認)/inherited(原本から引き継ぎ)/none(未承認)。"""
    own = _own_fields(conn, asset, key)
    original = asset.get("content_rating") if asset.get("content_rating_confirmed") else None
    if key == "original":
        rating, source = (original, "own") if original else (None, "none")
    elif own["confirmed"] and own["rating"]:
        rating, source = own["rating"], "own"
    elif original:
        rating, source = original, "inherited"
    else:
        rating, source = None, "none"
    return {"rating": rating, "source": source, "auto": own["auto"], "auto_confidence": own["confidence"], "original_rating": original}


def is_less_restrictive(new: str | None, reference: str | None) -> bool:
    """`new`が、`reference`より、ゆるい区分か(sfwがいちばんゆるい)。"""
    if not new or not reference:
        return False
    return _RESTRICTIVENESS.get(new, 0) < _RESTRICTIVENESS.get(reference, 0)


def set_rating(conn: sqlite3.Connection, asset: dict, key: str, rating: str | None) -> None:
    """加工版の区分を、人が決める(`None`で、原本の区分の引き継ぎに戻す)。原本の区分は、既存の「区分の承認」で決める。"""
    if key == "original":
        raise VersionError("原本の区分は、「区分の承認」で決めます")
    if rating is not None and rating not in RATINGS:
        raise VersionError("区分は、sfw・suggestive・explicitのどれかです")
    _own_fields(conn, asset, key)  # 存在の確認
    confirmed = 1 if rating else 0
    if key == "wm":
        conn.execute("UPDATE assets SET wm_content_rating = ?, wm_content_rating_confirmed = ? WHERE id = ?", (rating, confirmed, asset["id"]))
    else:
        conn.execute("UPDATE asset_edits SET content_rating = ?, content_rating_confirmed = ? WHERE id = ? AND asset_id = ?", (rating, confirmed, int(key[5:]), asset["id"]))
    conn.commit()


def set_auto(conn: sqlite3.Connection, asset: dict, key: str, rating: str, confidence: float | None) -> None:
    """加工版のAI判定(sfw/nsfw)の結果を、記録する。"""
    if key == "original":
        conn.execute("UPDATE assets SET nsfw_auto_rating = ?, nsfw_auto_confidence = ? WHERE id = ?", (rating, confidence, asset["id"]))
    elif key == "wm":
        conn.execute("UPDATE assets SET wm_nsfw_auto_rating = ?, wm_nsfw_auto_confidence = ? WHERE id = ?", (rating, confidence, asset["id"]))
    else:
        conn.execute("UPDATE asset_edits SET nsfw_auto_rating = ?, nsfw_auto_confidence = ? WHERE id = ? AND asset_id = ?", (rating, confidence, int(key[5:]), asset["id"]))
    conn.commit()


def _from_suffix(conn: sqlite3.Connection, asset: dict, edit: dict) -> str:
    """加工版を、原本ではなく、ほかのファイルをもとに作ったときの、もとの説明(例: 「（「静止画 0:02.0 JPG」から）」)。"""
    source = (edit.get("params") or {}).get("source") or "original"
    if source == "original":
        return ""
    if source == "wm":
        return "（透かし入りから）"
    if source.startswith("edit:") and source[5:].isdigit():
        other = edits.get_edit(conn, asset["id"], int(source[5:]))
        return f"（「{other['summary']}」から）" if other else "（削除された加工版から）"
    return ""


def library_rows(conn: sqlite3.Connection, asset: dict) -> list[dict]:
    """詳細画面の「ファイル(原本と加工版)」の一覧。原本を先頭に、加工版を新しい順に。処理中・失敗の加工版も含む。

    各行: key / role(original|wm|edit) / label / detail / media_type / state(done|queued|running|failed) / error /
          size_bytes / duration / edit_id / rating情報 / ai_busy(その加工版のAI判定が、待機中・実行中)
    """
    import json as _json

    busy = set()
    for row in conn.execute(
        "SELECT params FROM ai_tasks WHERE asset_id = ? AND kind = 'version_nsfw' AND status IN ('queued', 'running')", (asset["id"],)
    ):
        try:
            busy.add((_json.loads(row["params"] or "{}")).get("version"))
        except ValueError:
            pass

    def with_rating(key: str, row: dict) -> dict:
        return {**row, "key": key, **rating_info(conn, asset, key), "ai_busy": key in busy}

    rows = [with_rating("original", {
        "role": "original", "label": "原本", "detail": asset.get("original_name") or asset["id"], "media_type": asset["kind"],
        "state": "done", "error": None, "size_bytes": None, "duration": None, "edit_id": None, "exists": Path(asset["file_path"]).exists(),
    })]
    if asset.get("wm_path"):
        wm = Path(asset["wm_path"])
        rows.append(with_rating("wm", {
            "role": "wm", "label": "透かし入り", "detail": f"「{asset.get('wm_text') or ''}」", "media_type": "image", "state": "done",
            "error": None, "size_bytes": wm.stat().st_size if wm.exists() else None, "duration": None, "edit_id": None, "exists": wm.exists(),
        }))
    for edit in edits.list_edits(conn, asset["id"]):
        path = edits.edit_path(asset, edit)
        rows.append(with_rating(f"edit:{edit['id']}", {
            "role": "edit", "label": edits.kind_label(edit), "detail": edit["summary"] + _from_suffix(conn, asset, edit), "media_type": edits.media_type(asset, edit), "state": edit["status"] if edit["status"] != "done" else "done",
            "error": edit["error"], "size_bytes": edit["size_bytes"], "duration": edit["duration"], "edit_id": edit["id"],
            "exists": bool(path and path.exists()),
        }))
    return rows
