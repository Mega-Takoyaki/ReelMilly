"""ingest後の解析(NSFW自動仕分け+内容説明取得)をまとめる(ADR-0015)。

NSFW自動仕分けと内容説明の両方が取得できて初めてアセットは`ready`になる。
`ingest_inbox`と`reelmilly analyze`(再試行)の両方から共通で使う。
"""
from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}


@dataclass
class AnalysisResult:
    success: bool
    nsfw_auto_rating: str | None = None
    nsfw_auto_confidence: float | None = None
    content_description: str | None = None
    error: str | None = None


def _extract_representative_frame(video_path: Path) -> Path:
    """動画の中間フレームを1枚抽出し、一時jpgファイルとして保存する。呼び出し元が削除すること。"""
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    try:
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if frame_count > 1:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_count // 2)
        ret, frame = cap.read()
        if not ret:
            raise RuntimeError(f"could not read a frame from {video_path}")
    finally:
        cap.release()

    fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
    import os

    os.close(fd)
    tmp = Path(tmp_path)
    cv2.imwrite(str(tmp), frame)
    return tmp


def analyze_asset(
    conn: sqlite3.Connection,
    nsfw_classifier,
    generator,
    media_path: Path,
) -> AnalysisResult:
    """NSFW自動仕分けと内容説明取得を試みる。両方成功した場合のみsuccess=Trueを返す。"""
    from core import settings as settings_module

    nsfw_auto_rating = None
    nsfw_auto_confidence = None
    content_description = None
    errors = []

    if nsfw_classifier is not None:
        try:
            nsfw_result = nsfw_classifier.classify(media_path)
            nsfw_auto_rating = nsfw_result.rating
            nsfw_auto_confidence = nsfw_result.confidence
        except Exception as exc:  # noqa: BLE001
            errors.append(f"nsfw: {exc}")
    else:
        errors.append("nsfw classifier unavailable")

    if generator is not None:
        frame_path = media_path
        cleanup_path = None
        try:
            if media_path.suffix.lower() in VIDEO_EXTENSIONS:
                frame_path = _extract_representative_frame(media_path)
                cleanup_path = frame_path
            system_prompt = settings_module.get_description_system_prompt(conn)
            content_description = generator.describe_image(frame_path, system_prompt)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"description: {exc}")
        finally:
            if cleanup_path is not None:
                cleanup_path.unlink(missing_ok=True)
    else:
        errors.append("generator unavailable")

    success = nsfw_auto_rating is not None and content_description is not None
    return AnalysisResult(
        success=success,
        nsfw_auto_rating=nsfw_auto_rating,
        nsfw_auto_confidence=nsfw_auto_confidence,
        content_description=content_description,
        error="; ".join(errors) if errors else None,
    )
