"""一覧用のサムネイル(小さな静止画)。

一覧のカードに、原本(画像は数百KB、動画は数MB)をそのまま読み込むと、スマホ回線では重く、
iPhoneは動画の最初のコマも出さない。そこで、長辺`MAX_EDGE`pxのJPEGを`data/state/thumbs/<作品ID>.jpg`に作って配る。
作り直せる補助データなので、消してもよい(見られたときか、背景の補充で、また作る)。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from PIL import Image, ImageOps

MAX_EDGE = 480
QUALITY = 80
VIDEO_FRAME_POSITION = 0.1  # 動画の長さのうち、この位置のコマ(冒頭の暗転を避ける)


def thumb_dir(state_dir: Path) -> Path:
    return state_dir / "thumbs"


def thumb_path(state_dir: Path, asset_id: str) -> Path:
    return thumb_dir(state_dir) / f"{asset_id}.jpg"


def _video_frame(path: Path) -> Image.Image:
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total > 1:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * VIDEO_FRAME_POSITION))
        ok, frame = cap.read()
        if not ok:  # 位置指定が効かない動画は、先頭から読む
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
        if not ok:
            raise RuntimeError(f"動画のコマを読めません: {path}")
    finally:
        cap.release()
    return Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def is_fresh(thumb: Path, source: Path) -> bool:
    try:
        return thumb.stat().st_mtime >= source.stat().st_mtime
    except OSError:
        return False


def ensure(state_dir: Path, asset_id: str, kind: str, source: Path) -> Path | None:
    """サムネイルのパス(無い・原本より古いときは作る)。原本が無い・読めないときはNone。"""
    thumb = thumb_path(state_dir, asset_id)
    if is_fresh(thumb, source):
        return thumb
    if not source.exists():
        return None
    try:
        img = _video_frame(source) if kind == "video" else ImageOps.exif_transpose(Image.open(source))
        if img.mode != "RGB":  # 透過PNGなどは、白地に
            rgba = img.convert("RGBA")
            img = Image.new("RGB", rgba.size, (255, 255, 255))
            img.paste(rgba, mask=rgba.getchannel("A"))
        img.thumbnail((MAX_EDGE, MAX_EDGE))
        thumb.parent.mkdir(parents=True, exist_ok=True)
        tmp = thumb.with_suffix(f".{os.getpid()}.{id(img)}.tmp")  # 同時に作られても、壊れたファイルを配らない
        img.save(tmp, "JPEG", quality=QUALITY, optimize=True)
        os.replace(tmp, thumb)
        return thumb
    except Exception:  # noqa: BLE001  壊れた・未対応のファイルは、サムネイル無し(原本の表示は別)
        return None


def backfill(conn: sqlite3.Connection, state_dir: Path, limit: int | None = None) -> dict:
    """まだ無いサムネイルを、新しい作品から順に作る。`{"made": 作った数, "failed": 作れなかった数, "total": 対象の数}`"""
    rows = conn.execute(
        "SELECT id, kind, file_path FROM assets WHERE deleted_at IS NULL ORDER BY created_at DESC, id DESC"
    ).fetchall()
    made = failed = 0
    for row in rows:
        source = Path(row["file_path"])
        if is_fresh(thumb_path(state_dir, row["id"]), source):
            continue
        if not source.exists():
            continue  # ストレージが外れているときなど。あとで補う
        if ensure(state_dir, row["id"], row["kind"], source) is None:
            failed += 1
        else:
            made += 1
        if limit is not None and made + failed >= limit:
            break
    return {"made": made, "failed": failed, "total": len(rows)}
