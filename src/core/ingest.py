"""inbox ingest処理: Grok Imagine出力を取り込みSQLiteに登録する。"""
from __future__ import annotations

import json
import secrets
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from core import db
from core.analysis import analyze_asset
from core.config import Config
from core.events import log_event
from core.nsfw import NsfwClassifier

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}
SIDECAR_EXTENSIONS = {".yaml", ".yml", ".json"}
DEFAULT_CHANNELS = ["fanvue", "x"]


def _kind_for_extension(ext: str) -> str | None:
    ext = ext.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return None


def _generate_asset_id() -> str:
    date_part = datetime.now().strftime("%Y%m%d")
    random_part = secrets.token_hex(4)
    return f"{date_part}-{random_part}"


def _find_sidecar(media_path: Path) -> Path | None:
    for ext in SIDECAR_EXTENSIONS:
        candidate = media_path.with_suffix(ext)
        if candidate.exists():
            return candidate
    return None


def _load_sidecar(sidecar_path: Path) -> dict:
    text = sidecar_path.read_text(encoding="utf-8")
    if sidecar_path.suffix == ".json":
        return json.loads(text) or {}
    return yaml.safe_load(text) or {}


@dataclass
class IngestResult:
    asset_id: str
    kind: str
    dest_path: Path


def ingest_inbox(
    config: Config,
    conn: sqlite3.Connection,
    nsfw_classifier: NsfwClassifier | None = None,
    generator=None,
) -> list[IngestResult]:
    """inbox配下のメディアファイルをreadyへ移動し、SQLiteへ登録する。

    同名のsidecar(.yaml/.yml/.json)があればメタデータとしてマージし、
    取り込み後は削除する。sidecarがない場合はデフォルト値を使う。

    NSFW自動仕分け(nsfw_classifier)と内容説明取得(generator)の両方が成功した
    場合のみ`status="ready"`とする(ADR-0015)。いずれか一方でも未設定・失敗の
    場合は`status="analyzing"`のまま残し、`reelmilly analyze`で再試行できる。
    失敗の詳細は`events.jsonl`に`analysis_incomplete`として記録する。
    """
    results: list[IngestResult] = []
    inbox = config.paths.inbox

    for media_path in sorted(inbox.iterdir()):
        if not media_path.is_file():
            continue
        if media_path.suffix.lower() in SIDECAR_EXTENSIONS:
            continue  # sidecar自体は本体ファイル処理時にまとめて扱う

        kind = _kind_for_extension(media_path.suffix)
        if kind is None:
            continue

        sidecar_path = _find_sidecar(media_path)
        sidecar_data = _load_sidecar(sidecar_path) if sidecar_path else {}

        asset_id = _generate_asset_id()
        dest_dir = config.paths.ready / asset_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / media_path.name
        shutil.move(str(media_path), str(dest_path))

        if sidecar_path:
            sidecar_path.unlink()

        analysis = analyze_asset(conn, nsfw_classifier, generator, dest_path)

        now = datetime.now(timezone.utc).isoformat()
        asset = {
            "id": asset_id,
            "status": "ready" if analysis.success else "analyzing",
            "kind": kind,
            "file_path": str(dest_path),
            "caption": sidecar_data.get("caption"),
            "x_caption": sidecar_data.get("x_caption"),
            "fanvue_text": sidecar_data.get("fanvue_text"),
            "audience": sidecar_data.get("audience"),
            "price_cents": sidecar_data.get("price_cents"),
            "nsfw_auto_rating": analysis.nsfw_auto_rating,
            "nsfw_auto_confidence": analysis.nsfw_auto_confidence,
            "content_description": analysis.content_description,
            "created_at": now,
            "updated_at": now,
        }
        db.insert_asset(conn, asset)

        for channel in sidecar_data.get("channels", DEFAULT_CHANNELS):
            db.add_channel(conn, asset_id, channel)

        for tag in sidecar_data.get("tags", []):
            db.add_tag_to_asset(conn, asset_id, tag)

        log_event(
            config.paths.events_path,
            "ingest_ok",
            asset_id=asset_id,
            kind=kind,
            filename=media_path.name,
            status=asset["status"],
            nsfw_auto_rating=analysis.nsfw_auto_rating,
        )
        if not analysis.success:
            log_event(
                config.paths.events_path,
                "analysis_incomplete",
                asset_id=asset_id,
                filename=media_path.name,
                error=analysis.error,
            )

        results.append(IngestResult(asset_id=asset_id, kind=kind, dest_path=dest_path))

    return results
