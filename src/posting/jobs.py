"""SNS投稿ジョブ(Phase 4)。

X投稿はADR-0014により開発者アプリ承認待ちで未実装のため、本モジュールは
現時点でFanvueへの投稿のみを扱う。CLAUDE_HANDOFF.md 4章の部分失敗ルールに
基づき、Fanvue投稿が成功したら即座に`status="posted"`とする(二重投稿防止)。
X紹介投稿は`x_ok`フラグで別途管理し、X投稿モジュール実装時にこのフラグを
見て追加投稿する設計とする。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core import db
from core.config import Config
from core.events import log_event
from core.policy import can_auto_post
from posting.fanvue import FanvueClient, build_post_url

FANVUE_CHANNEL = "fanvue"
FANVUE_POSTED_TAG = "fanvue投稿済み"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DropResult:
    executed: bool
    asset_id: str | None = None
    fanvue_url: str | None = None
    fanvue_uuid: str | None = None
    skipped_reason: str | None = None
    error: str | None = None


def run_fanvue_drop(
    config: Config,
    conn: sqlite3.Connection,
    fanvue_client: FanvueClient,
    fanvue_handle: str,
    post_url_template: str,
) -> DropResult:
    """readyかつfanvueチャンネル指定・承認済みの最古アセットを1件Fanvueへ投稿する。

    対象アセットが無い場合、またはポリシー(ADR-0006/0008/0009)で自動投稿
    不可と判定された場合は`executed=False`で理由を返し、何も投稿しない。
    投稿を試みた場合、成功/失敗いずれもDBに反映しevents.jsonlに記録する。
    失敗時は`status="failed_fanvue"`とし、自動リトライは行わない
    （CLAUDE_HANDOFF.md 4章）。
    """
    candidates = db.list_assets(
        conn,
        status="ready",
        channel=FANVUE_CHANNEL,
        confirmed_only=True,
        order="asc",
        limit=1,
    )
    if not candidates:
        return DropResult(
            executed=False,
            skipped_reason="ready状態でfanvue向けの承認済みアセットがありません",
        )

    asset = candidates[0]
    asset_id = asset["id"]

    decision = can_auto_post(
        asset, FANVUE_CHANNEL, config.platform_content_rules, config.platform_auto_post_ratings
    )
    if not decision.allowed:
        log_event(config.paths.events_path, "drop_skipped", asset_id=asset_id, reason=decision.reason)
        return DropResult(executed=False, asset_id=asset_id, skipped_reason=decision.reason)

    try:
        file_path = Path(asset["file_path"])
        media_uuid = fanvue_client.upload_media(file_path, media_type=asset["kind"])

        ready = fanvue_client.wait_for_media_ready(media_uuid)
        if not ready:
            raise TimeoutError(f"media {media_uuid} did not become ready in time")

        text = asset.get("fanvue_text") or asset.get("caption") or ""
        fanvue_client.create_post(
            audience=asset.get("audience") or "subscribers",
            text=text,
            media_uuids=[media_uuid],
            price_cents=asset.get("price_cents"),
        )
        fanvue_url = build_post_url(post_url_template, fanvue_handle, media_uuid)

        db.update_asset(
            conn,
            asset_id,
            status="posted",
            fanvue_url=fanvue_url,
            fanvue_uuid=media_uuid,
            updated_at=_now(),
        )
        db.add_tag_to_asset(conn, asset_id, FANVUE_POSTED_TAG)
        log_event(
            config.paths.events_path,
            "drop_ok",
            asset_id=asset_id,
            fanvue_url=fanvue_url,
            fanvue_uuid=media_uuid,
        )
        return DropResult(executed=True, asset_id=asset_id, fanvue_url=fanvue_url, fanvue_uuid=media_uuid)

    except Exception as exc:  # noqa: BLE001 - 失敗理由をそのままDB/ログに残すのが目的
        db.update_asset(conn, asset_id, status="failed_fanvue", updated_at=_now())
        log_event(config.paths.events_path, "fanvue_failed", asset_id=asset_id, error=str(exc))
        return DropResult(executed=False, asset_id=asset_id, error=str(exc))
