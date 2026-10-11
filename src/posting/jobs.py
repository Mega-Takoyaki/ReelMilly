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

from core import db, notifications
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
    if caption_text:
        return caption_text, None

    from core import captions
    from core import settings as settings_module

    asset_id = asset["id"]
    # 投稿文が未設定なら、生成する。形式の検査(英語/---/日本語)に通る文だけを使い、作れなければ、
    # 空の文や、形式に合わない文を公開せず、この作品の投稿を見送って通知する(手で書いてもらう)
    try:
        generated = captions.generate(conn, [asset], "fanvue", generator=generator).text
    except captions.CaptionError as exc:
        log_event(config.paths.events_path, "caption_generation_failed", asset_id=asset_id, error=str(exc))
        notifications.add(
            conn, "post", "投稿文を作れず、自動投稿を見送りました", f"{asset_id}: {exc}", "warning", asset_id=asset_id
        )
        return "", DropResult(
            executed=False, asset_id=asset_id, skipped_reason=f"投稿文を作れませんでした（手で書くか、もう一度試してください）: {exc}"
        )

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
    asset_id: str | None = None,
) -> DropResult:
    """readyかつfanvueチャンネル指定・承認済みの最古アセットを1件Fanvueへ投稿する。

    `asset_id`を指定すると、最古ではなく、その作品を投稿する(承認済み・Fanvueが投稿予定・Fanvue未投稿のものに限る)。

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
        limit=1 if asset_id is None else 100000,
    )
    if asset_id is not None:
        candidates = [a for a in candidates if a["id"] == asset_id]  # 指定した作品が、投稿できる条件を満たすときだけ
    if not candidates:
        return DropResult(
            executed=False,
            asset_id=asset_id,
            skipped_reason=(
                f"{asset_id}は、投稿できる状態ではありません(承認済み・Fanvueが投稿予定・Fanvue未投稿のものだけです)"
                if asset_id
                else "ready状態でfanvue向けの承認済みアセットがありません"
            ),
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

    # ストレージが外れている等でファイルが見えないときは、投稿に失敗した記録を残さず、見送る
    # (あとでつながれば、次回の投稿の対象にそのまま残る)
    primary = Path(asset["wm_path"]) if asset.get("wm_path") and Path(asset["wm_path"]).exists() else Path(asset["file_path"])
    if not primary.exists():
        reason = f"ファイルが見つかりません（ストレージが接続されていない可能性があります）: {primary}"
        log_event(config.paths.events_path, "drop_skipped", asset_id=asset_id, reason=reason)
        return DropResult(executed=False, asset_id=asset_id, skipped_reason=reason)

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
        notifications.add(
            conn, "post", "Fanvueへ投稿しました", fanvue_url, "success", asset_id=asset_id, url=fanvue_url
        )
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
        notifications.add(conn, "post", "Fanvueへの投稿に失敗しました", str(exc), "error", asset_id=asset_id)
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
    asset_id: str | None = None,
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
            asset_id=asset_id,
        )
        results.append(result)
        if not result.executed:
            break
    return results


MAX_POST_NOW_ITEMS = 10  # 1つの投稿にまとめられる作品(メディア)の上限
MIN_PRICE_CENTS = 300  # Fanvueの有料投稿の最低価格(3ドル)
FANVUE_AUDIENCES = {"subscribers": "購読者のみ", "followers-and-subscribers": "フォロワーと購読者"}


@dataclass
class PostNowResult:
    ok: bool
    asset_ids: list[str]
    fanvue_url: str | None = None
    error: str | None = None


def run_fanvue_post_now(
    config: Config,
    conn: sqlite3.Connection,
    fanvue_client: FanvueClient,
    fanvue_handle: str,
    post_url_template: str,
    items: list[tuple[dict, Path, str]],
    text: str,
    audience: str,
    price_cents: int | None = None,
) -> PostNowResult:
    """作品とバージョン(ファイル)を指定して、いますぐ1つの投稿としてFanvueへ投稿する。

    `items`は[(作品, 投稿するファイル, "image"|"video")]。複数ならば、複数のメディアを1つの投稿にまとめる。
    成功したら、すべての作品を「投稿済み」として記録し(二重投稿の防止)、失敗したら、すべて「失敗」として記録する
    (自動投稿の対象から外れる。詳細画面から、再投稿の対象に戻せる)。結果は通知に残す。
    """
    asset_ids = [asset["id"] for asset, _path, _type in items]
    try:
        media_uuids = []
        for _asset, path, media_type in items:
            media_uuid = fanvue_client.upload_media(path, media_type=media_type)
            if not fanvue_client.wait_for_media_ready(media_uuid):
                raise TimeoutError(f"media {media_uuid} did not become ready in time")
            media_uuids.append(media_uuid)
        fanvue_client.create_post(audience=audience, text=text, media_uuids=media_uuids, price_cents=price_cents)
    except Exception as exc:  # noqa: BLE001 - 失敗理由をそのまま記録・通知するのが目的
        for asset_id in asset_ids:
            db.set_post(conn, asset_id, FANVUE_CHANNEL, "failed", error=str(exc))
        notifications.add(
            conn, "post", "Fanvueへの投稿に失敗しました", f"{len(asset_ids)}件: {exc}", "error",
            asset_id=asset_ids[0] if len(asset_ids) == 1 else None,
        )
        log_event(config.paths.events_path, "post_now_failed", asset_ids=asset_ids, error=str(exc))
        return PostNowResult(ok=False, asset_ids=asset_ids, error=str(exc))

    fanvue_url = build_post_url(post_url_template, fanvue_handle, media_uuids[0])
    for (asset, _path, _type), media_uuid in zip(items, media_uuids):
        db.set_post(conn, asset["id"], FANVUE_CHANNEL, "posted", url=fanvue_url, external_id=media_uuid)
        # 「今すぐ投稿」は人の操作なので、AI処理中(analyzing)や承認待ちの作品でも投稿でき、投稿した事実をもって、
        # 準備状態をreadyにする。区分(content_rating)の承認は変えない(自動投稿は、承認済みの作品だけが対象のまま)
        db.update_asset(
            conn, asset["id"], fanvue_url=fanvue_url, fanvue_uuid=media_uuid, fanvue_text=text,
            audience=audience, price_cents=price_cents, status="ready", updated_at=_now(),
        )
        db.add_tag_to_asset(conn, asset["id"], FANVUE_POSTED_TAG)
    label = FANVUE_AUDIENCES.get(audience, audience)
    notifications.add(
        conn, "post", "Fanvueへ投稿しました", f"{len(asset_ids)}件（公開範囲: {label}）", "success",
        asset_id=asset_ids[0] if len(asset_ids) == 1 else None, url=fanvue_url,
    )
    log_event(config.paths.events_path, "post_now_ok", asset_ids=asset_ids, audience=audience, fanvue_url=fanvue_url)
    return PostNowResult(ok=True, asset_ids=asset_ids, fanvue_url=fanvue_url)


X_CHANNEL = "x"


def run_x_post_now(
    config: Config,
    conn: sqlite3.Connection,
    x_client,
    items: list[tuple[dict, Path, str]],
    text: str,
    sensitive: bool = False,
    made_with_ai: bool = True,
) -> PostNowResult:
    """作品とバージョン(ファイル)を指定して、いますぐ1つの投稿としてXへ投稿する(画像は4枚まで、動画は1本)。

    `sensitive`なら、すべてのメディアを、成人向けのセンシティブなメディアとして指定してから投稿する。
    `made_with_ai`なら、AI生成のメディアを含むことを、投稿に表示する。成功したら、作品を「Xに投稿済み」として記録する。
    """
    from posting.x import build_post_url as build_x_post_url

    asset_ids = [asset["id"] for asset, _path, _type in items]
    try:
        media_ids = [x_client.upload_media(path, media_type=media_type, sensitive=sensitive) for _asset, path, media_type in items]
        created = x_client.create_post(text, media_ids, made_with_ai=made_with_ai)
        post_id = created.get("id") or ""
    except Exception as exc:  # noqa: BLE001 - 失敗理由をそのまま記録・通知するのが目的
        for asset_id in asset_ids:
            db.set_post(conn, asset_id, X_CHANNEL, "failed", error=str(exc))
        notifications.add(
            conn, "post", "Xへの投稿に失敗しました", f"{len(asset_ids)}件: {exc}", "error",
            asset_id=asset_ids[0] if len(asset_ids) == 1 else None,
        )
        log_event(config.paths.events_path, "x_post_now_failed", asset_ids=asset_ids, error=str(exc))
        return PostNowResult(ok=False, asset_ids=asset_ids, error=str(exc))

    url = build_x_post_url(post_id) if post_id else None
    for asset, _path, _type in items:
        db.set_post(conn, asset["id"], X_CHANNEL, "posted", url=url, external_id=post_id)
        db.update_asset(conn, asset["id"], status="ready", updated_at=_now())
    notifications.add(
        conn, "post", "Xへ投稿しました", f"{len(asset_ids)}件{'（センシティブ指定）' if sensitive else ''}", "success",
        asset_id=asset_ids[0] if len(asset_ids) == 1 else None, url=url,
    )
    log_event(config.paths.events_path, "x_post_now_ok", asset_ids=asset_ids, sensitive=sensitive, url=url)
    return PostNowResult(ok=True, asset_ids=asset_ids, fanvue_url=url)
