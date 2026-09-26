"""メディアファイルのプロパティ(解像度・サイズ・長さ等)を取得する。

詳細画面での表示に使う補助情報であり、取得に失敗しても致命的ではないため、
個々の抽出処理は例外を握りつぶして None のまま返す。
"""
from __future__ import annotations

from pathlib import Path


def human_readable_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def _image_dimensions(file_path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image

        with Image.open(file_path) as img:
            return img.size
    except Exception:  # noqa: BLE001 - 補助情報の取得失敗は致命的ではない
        return None, None


def _video_properties(file_path: Path) -> dict:
    try:
        import cv2

        cap = cv2.VideoCapture(str(file_path))
        try:
            width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
            height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        finally:
            cap.release()

        duration = round(frame_count / fps, 1) if fps and frame_count else None
        return {
            "width": int(width) if width else None,
            "height": int(height) if height else None,
            "duration_seconds": duration,
        }
    except Exception:  # noqa: BLE001 - 補助情報の取得失敗は致命的ではない
        return {"width": None, "height": None, "duration_seconds": None}


def get_media_properties(file_path: Path, kind: str) -> dict:
    """ファイルサイズ・解像度・(動画なら)長さを取得する。取得できない項目はNoneのまま返す。"""
    properties = {
        "file_size_bytes": None,
        "file_size_human": None,
        "width": None,
        "height": None,
        "duration_seconds": None,
    }

    if not file_path.exists():
        return properties

    size_bytes = file_path.stat().st_size
    properties["file_size_bytes"] = size_bytes
    properties["file_size_human"] = human_readable_size(size_bytes)

    if kind == "image":
        properties["width"], properties["height"] = _image_dimensions(file_path)
    elif kind == "video":
        properties.update(_video_properties(file_path))

    return properties
