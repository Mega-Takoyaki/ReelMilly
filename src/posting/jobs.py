"""SNS投稿ジョブ(Phase 4)。

X投稿はADR-0014により開発者アプリ承認待ちで未実装のため、本モジュールは
現時点でFanvueへの投稿のみを扱う。CLAUDE_HANDOFF.md 4章の部分失敗ルールに
基づき、Fanvue投稿が成功したら即座に`posts`テーブルへ`posted`として記録する
(二重投稿防止)。投稿状態は作品の準備状態(`assets.status`)とは別の軸で、作品x投稿先
ごとに1行持つ。X紹介投稿もX投稿モジュール実装時に同じ`posts`テーブルへ記録する。
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


def _resolve_caption(config: Config, conn: sqlite3.Connection, asset: dict, generator) -> tuple[str, DropResult | None]:
    """投稿文を決定する。手動指定があればそれを使い、無ければgeneratorで生成する(ADR-0015)。

    draftモードで生成した場合は投稿せず保存のみ行うため、その旨のDropResultを
    2要素目で返す(呼び出し元はNoneでなければ即returnすること)。
    """
    caption_text = asset.get("fanvue_text") or asset.get("caption") or ""
    if caption_text or generator is None or not asset.get("content_description"):
        return caption_text, None

    from core import settings as settings_module

    asset_id = asset["id"]
    try:
        generated = generator.generate_caption(
            asset["content_description"], settings_module.get_caption_system_prompt(conn)
        )
    except Exception as exc:  # noqa: BLE001 - 生成失敗は投稿を止めず手動キャプション待ちにする
        log_event(config.paths.events_path, "caption_generation_failed", asset_id=asset_id, error=str(exc))
        return "", None

    if not generated:
        return "", None

    if settings_module.get_caption_mode(conn) == "draft":
        db.update_asset(conn, asset_id, fanvue_caption_draft=generated, updated_at=_now())
        log_event(config.paths.events_path, "caption_draft_saved", asset_id=asset_id)
        return "", DropResult(
            executed=False,
            asset_id=asset_id,
            skipped_reason="投稿文を下書きとして保存しました(本体UIで確認・採用してください)",
        )

    return generated, None


def run_fanvue_drop(
    config: Config,
    conn: sqlite3.Connection,
    fanvue_client: FanvueClient,
    fanvue_handle: str,
    post_url_template: str,
    kind: str | None = None,
    rating: str | None = None,
    generator=None,
) -> DropResult:
    """readyかつfanvueチャンネル指定・承認済みの最古アセットを1件Fanvueへ投稿する。

    対象アセットが無い場合、またはポリシー(ADR-0006/0008/0009)で自動投稿
    不可と判定された場合は`executed=False`で理由を返し、何も投稿しない。
    投稿を試みた場合、成功/失敗いずれもDBに反映しevents.jsonlに記録する。
    失敗時は`posts`に`failed`として記録し、自動リトライは行わない(失敗した作品は
    次回以降の対象から外れる。本体UIの詳細画面から再投稿の対象に戻せる)
    （CLAUDE_HANDOFF.md 4章）。
    `kind`/`rating`で対象アセットの種別・レーティングを絞り込める(ADR-0015)。
    `generator`を渡すと、投稿文が未指定の場合に内容説明から自動生成する
    （ADR-0015、`caption_mode`設定で自動投稿/下書き保存を切り替え）。
    """
    candidates = db.list_assets(
        conn,
        status="ready",
        channel=FANVUE_CHANNEL,
        post_channel=FANVUE_CHANNEL,
        post_status="none",  # Fanvueへ未投稿(投稿済み・失敗済みは対象外)
        confirmed_only=True,
        kind=kind,
        content_rating=rating,
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

    caption_text, draft_result = _resolve_caption(config, conn, asset, generator)
    if draft_result is not None:
        return draft_result

    try:
        # 透かしを入れた作品は、透かし入りのファイルを投稿する(元のファイルは変えない)
        wm = asset.get("wm_path")
        file_path = Path(wm) if wm and Path(wm).exists() else Path(asset["file_path"])
        media_uuid = fanvue_client.upload_media(file_path, media_type=asset["kind"])

        ready = fanvue_client.wait_for_media_ready(media_uuid)
        if not ready:
            raise TimeoutError(f"media {media_uuid} did not become ready in time")

        fanvue_client.create_post(
            audience=asset.get("audience") or "subscribers",
            text=caption_text,
            media_uuids=[media_uuid],
            price_cents=asset.get("price_cents"),
        )
        fanvue_url = build_post_url(post_url_template, fanvue_handle, media_uuid)

        db.set_post(conn, asset_id, FANVUE_CHANNEL, "posted", url=fanvue_url, external_id=media_uuid)
        db.update_asset(
            conn,
            asset_id,
            fanvue_url=fanvue_url,
            fanvue_uuid=media_uuid,
            fanvue_text=caption_text,
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
        db.set_post(conn, asset_id, FANVUE_CHANNEL, "failed", error=str(exc))
        db.update_asset(conn, asset_id, updated_at=_now())
        log_event(config.paths.events_path, "fanvue_failed", asset_id=asset_id, error=str(exc))
        return DropResult(executed=False, asset_id=asset_id, error=str(exc))


def run_fanvue_drop_batch(
    config: Config,
    conn: sqlite3.Connection,
    fanvue_client: FanvueClient,
    fanvue_handle: str,
    post_url_template: str,
    count: int = 1,
    kind: str | None = None,
    rating: str | None = None,
    generator=None,
) -> list[DropResult]:
    """`run_fanvue_drop`を最大`count`回繰り返す(ADR-0015)。

    候補が尽きた・ポリシーでスキップされた・投稿に失敗した場合はその時点で
    打ち切る(次の候補へのスキップは行わない)。
    """
    results: list[DropResult] = []
    for _ in range(max(count, 1)):
        result = run_fanvue_drop(
            config,
            conn,
            fanvue_client,
            fanvue_handle,
            post_url_template,
            kind=kind,
            rating=rating,
            generator=generator,
        )
        results.append(result)
        if not result.executed:
            break
    return results
