"""ingest後の解析(NSFW自動仕分け+内容説明取得)をまとめる(ADR-0015/ADR-0018)。

NSFW自動仕分けと内容説明の両方が取得できて初めてアセットは`ready`になる。
`ingest_inbox`と`reelmilly analyze`(再試行)の両方から共通で使う。
内容説明生成時にAIが提案したタグ(`suggested_tags`)も呼び出し元に返す。
実際にタグ・NSFW判定タグをアセットへ付与するのは呼び出し元(ingest.py/cli.py)の
責務とする(ADR-0018)。
"""
from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}


@dataclass
class AnalysisResult:
    success: bool
    nsfw_auto_rating: str | None = None
    nsfw_auto_confidence: float | None = None
    content_description: str | None = None
    suggested_tags: list[str] = field(default_factory=list)
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


def apply_auto_tags(conn: sqlite3.Connection, asset_id: str, result: AnalysisResult) -> None:
    """分析結果からタグを自動付与する(ADR-0018)。

    内容説明生成時にAIが提案したタグ(`suggested_tags`)に加え、NSFW自動仕分けの
    結果(`sfw`/`nsfw`)もタグとして付与し、一覧画面の既存のタグ絞り込みから
    確認できるようにする。取得できた情報だけを反映するため、`result.success`が
    Falseの場合(片方のみ成功)でも取得できた分は付与する。
    """
    from core import db

    for tag in result.suggested_tags:
        db.add_tag_to_asset(conn, asset_id, tag)
    if result.nsfw_auto_rating:
        db.add_tag_to_asset(conn, asset_id, result.nsfw_auto_rating)


def run_nsfw(nsfw_classifier, media_path: Path):
    """sfw/nsfw判定を実行する。失敗時は例外を送出する。戻り値は`NsfwResult`(rating, confidence)。"""
    if nsfw_classifier is None:
        raise RuntimeError('NSFW判定モデルが利用できません（pip install -e ".[nsfw]" が必要です）')
    return nsfw_classifier.classify(media_path)


def run_describe(conn: sqlite3.Connection, generator, media_path: Path):
    """説明文生成・タグ提案を実行する。失敗時は例外を送出する。戻り値は`DescriptionResult`。

    動画は代表フレーム1枚を使う。設定画面のタグカテゴリがあれば、システムプロンプトへ
    カテゴリごとにタグを付けるよう指示を追記する。
    """
    from core import settings as settings_module

    if generator is None:
        raise RuntimeError("生成AIが利用できません（設定画面でプロバイダー・APIキー/モデルを確認してください）")

    frame_path = media_path
    cleanup_path = None
    try:
        if media_path.suffix.lower() in VIDEO_EXTENSIONS:
            frame_path = _extract_representative_frame(media_path)
            cleanup_path = frame_path
        system_prompt = settings_module.get_description_system_prompt(conn)
        system_prompt += settings_module.tag_category_instructions(conn)
        return generator.describe_image(frame_path, system_prompt)
    finally:
        if cleanup_path is not None:
            cleanup_path.unlink(missing_ok=True)


def analyze_asset(
    conn: sqlite3.Connection,
    nsfw_classifier,
    generator,
    media_path: Path,
) -> AnalysisResult:
    """NSFW自動仕分けと内容説明取得を試みる。両方成功した場合のみsuccess=Trueを返す。"""
    nsfw_auto_rating = None
    nsfw_auto_confidence = None
    content_description = None
    suggested_tags: list[str] = []
    errors = []

    try:
        nsfw_result = run_nsfw(nsfw_classifier, media_path)
        nsfw_auto_rating = nsfw_result.rating
        nsfw_auto_confidence = nsfw_result.confidence
    except Exception as exc:  # noqa: BLE001
        errors.append(f"nsfw: {exc}")

    try:
        description_result = run_describe(conn, generator, media_path)
        content_description = description_result.description
        suggested_tags = description_result.suggested_tags
    except Exception as exc:  # noqa: BLE001
        errors.append(f"description: {exc}")

    success = nsfw_auto_rating is not None and content_description is not None
    return AnalysisResult(
        success=success,
        nsfw_auto_rating=nsfw_auto_rating,
        nsfw_auto_confidence=nsfw_auto_confidence,
        content_description=content_description,
        suggested_tags=suggested_tags,
        error="; ".join(errors) if errors else None,
    )
