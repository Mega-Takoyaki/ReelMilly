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
) -> list[IngestResult]:
    """inbox配下のメディアファイルをreadyへ移動し、SQLiteへ登録する。

    同名のsidecar(.yaml/.yml/.json)があればメタデータとしてマージし、
    取り込み後は削除する。sidecarがない場合はデフォルト値を使う。

    nsfw_classifierを渡した場合、取り込んだ各アセットに対してNSFW自動仕分け
    を実行し、`nsfw_auto_rating`/`nsfw_auto_confidence`をあわせて記録する
    （ADR-0008/0009。あくまで参考値で、確定にはcontent_rating_confirmedが必要）。
    分類に失敗した場合（破損ファイル等）はingest自体は継続し、当該アセットの
    `nsfw_auto_rating`はNoneのまま`events.jsonl`に`nsfw_classify_failed`を記録する。
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

        nsfw_auto_rating = None
        nsfw_auto_confidence = None
        if nsfw_classifier is not None:
            try:
                nsfw_result = nsfw_classifier.classify(dest_path)
                nsfw_auto_rating = nsfw_result.rating
                nsfw_auto_confidence = nsfw_result.confidence
            except Exception as exc:  # noqa: BLE001 - 破損ファイル等でingest全体を止めない
                log_event(
                    config.paths.events_path,
                    "nsfw_classify_failed",
                    asset_id=asset_id,
                    filename=media_path.name,
                    error=str(exc),
                )

        now = datetime.now(timezone.utc).isoformat()
        asset = {
            "id": asset_id,
            "status": "ready",
            "kind": kind,
            "file_path": str(dest_path),
            "caption": sidecar_data.get("caption"),
            "x_caption": sidecar_data.get("x_caption"),
            "fanvue_text": sidecar_data.get("fanvue_text"),
            "audience": sidecar_data.get("audience"),
            "price_cents": sidecar_data.get("price_cents"),
            "nsfw_auto_rating": nsfw_auto_rating,
            "nsfw_auto_confidence": nsfw_auto_confidence,
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
            nsfw_auto_rating=nsfw_auto_rating,
        )

        results.append(IngestResult(asset_id=asset_id, kind=kind, dest_path=dest_path))

    return results
