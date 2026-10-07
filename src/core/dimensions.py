"""作品の画像・動画の幅と高さ(縦横比)。一覧の「フル」表示で、読み込み前から正しい形の枠を確保するために持つ。"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def read_dimensions(path: Path, kind: str) -> tuple[int, int] | None:
    """(幅, 高さ)を返す。読めなければNone。画像はEXIFの回転(スマホの縦写真など)を反映する。"""
    try:
        if kind == "image":
            from PIL import Image

            with Image.open(path) as img:
                width, height = img.size
                if img.getexif().get(274) in (5, 6, 7, 8):  # 90度・270度の回転: ブラウザは回転して表示する
                    width, height = height, width
                return width, height
        import cv2

        cap = cv2.VideoCapture(str(path))
        try:
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        finally:
            cap.release()
        return (width, height) if width > 0 and height > 0 else None
    except Exception:  # noqa: BLE001 - 壊れたファイルでも、一覧を止めない
        return None


def backfill_dimensions(conn: sqlite3.Connection) -> int:
    """幅・高さが未記録の作品について、ファイルから読んで記録する(ファイルが見えない作品は、あとで再試行)。"""
    updated = 0
    rows = conn.execute("SELECT id, kind, file_path FROM assets WHERE width IS NULL OR height IS NULL").fetchall()
    for row in rows:
        size = read_dimensions(Path(row["file_path"]), row["kind"])
        if size:
            conn.execute("UPDATE assets SET width = ?, height = ? WHERE id = ?", (size[0], size[1], row["id"]))
            updated += 1
    conn.commit()
    return updated
