"""「今すぐ投稿」と「予約投稿」が共用する、投稿の準備(検査)と実行。

- `prepare`: 投稿の依頼(作品・バージョン・投稿文・公開範囲など)を検査して、投稿できる形(`Prepared`)にする。
  投稿先に連携していない・作品やファイルが無い・Xの仕様(画像4枚/動画1本・文字数)に合わない等は、`PostRequestError`
- `execute`: `Prepared`を実際に投稿する(結果は、投稿の記録・通知に残る)

画面(今すぐ投稿)は、`prepare`→別スレッドで`execute`。予約投稿は、予約するときに`prepare`で検査し、
時刻が来たら、もう一度`prepare`(その時点の状態で)→`execute`。
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from core import captions, db
from core import versions as versions_module
from core.config import Config
from posting import jobs
from posting import x as x_module


class PostRequestError(Exception):
    """投稿できない依頼(理由は、利用者に見せられる文)。`status`は、画面のAPIが返すHTTPの状態。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class Prepared:
    channel: str
    items: list[tuple[dict, Path, str]]
    text: str
    audience: str = "subscribers"
    price_cents: int | None = None
    sensitive: bool = False
    made_with_ai: bool = True
    client: object = None
    fanvue_handle: str = ""
    fanvue_url_template: str = "https://www.fanvue.com/{handle}"

    @property
    def asset_ids(self) -> list[str]:
        return [asset["id"] for asset, _path, _type in self.items]


def prepare(config: Config, conn: sqlite3.Connection, payload: dict) -> Prepared:
    """依頼を検査して、投稿できる形にする。問題があれば`PostRequestError`。"""
    channel = payload.get("channel", "fanvue")
    if channel not in ("fanvue", "x"):
        raise PostRequestError("不明な投稿先です")
    raw_items = payload.get("items") or []
    if not raw_items:
        raise PostRequestError("投稿する作品を選んでください")
    if len(raw_items) > jobs.MAX_POST_NOW_ITEMS:
        raise PostRequestError(f"1つの投稿にまとめられるのは{jobs.MAX_POST_NOW_ITEMS}件までです")
    text = str(payload.get("text") or "").strip()

    audience, price = "subscribers", None
    if channel == "fanvue":
        audience = payload.get("audience") or "subscribers"
        if audience not in jobs.FANVUE_AUDIENCES:
            raise PostRequestError("公開範囲が正しくありません")
        price = payload.get("price_cents")
        if price in ("", None):
            price = None
        else:
            try:
                price = int(price)
            except (TypeError, ValueError):
                raise PostRequestError("価格が正しくありません") from None
            if price < jobs.MIN_PRICE_CENTS:
                raise PostRequestError(f"有料投稿の価格は、{jobs.MIN_PRICE_CENTS / 100:g}ドル以上にしてください")
    elif captions.x_weight(text) > captions.X_MAX_WEIGHT:
        raise PostRequestError(
            f"Xの文字数の上限を超えています（全角は2文字として数え、{captions.X_MAX_WEIGHT}まで。いま{captions.x_weight(text)}）"
        )

    from core.cli import _try_create_fanvue_client, _try_create_x_client

    client, reason = (_try_create_x_client if channel == "x" else _try_create_fanvue_client)(config)
    if client is None:
        raise PostRequestError(reason, 503)

    items, seen = [], set()
    for raw in raw_items:
        asset_id = str(raw.get("asset_id") or "")
        asset = db.get_asset(conn, asset_id)
        if asset is None or asset.get("deleted_at") or asset_id in seen:
            raise PostRequestError(f"投稿できない作品が含まれています: {asset_id}")
        seen.add(asset_id)
        try:
            path, media_type = versions_module.resolve(conn, asset, raw.get("version"))
        except versions_module.VersionError as exc:
            raise PostRequestError(f"{asset_id}: {exc}") from None
        items.append((asset, path, media_type))

    if channel == "x":
        problem = x_module.check_media_set([media_type for _a, _p, media_type in items])
        if problem:
            raise PostRequestError(problem)

    return Prepared(
        channel=channel, items=items, text=text, audience=audience, price_cents=price,
        sensitive=bool(payload.get("sensitive")), made_with_ai=bool(payload.get("made_with_ai", True)),
        client=client, fanvue_handle=os.environ.get("FANVUE_HANDLE", ""),
        fanvue_url_template=os.environ.get("FANVUE_POST_URL_TEMPLATE", "https://www.fanvue.com/{handle}"),
    )


def execute(config: Config, conn: sqlite3.Connection, prepared: Prepared) -> jobs.PostNowResult:
    """準備した投稿を、実際に投稿する(成功・失敗は、投稿の記録と通知に残る)。"""
    if prepared.channel == "x":
        return jobs.run_x_post_now(
            config, conn, prepared.client, prepared.items, prepared.text,
            sensitive=prepared.sensitive, made_with_ai=prepared.made_with_ai,
        )
    return jobs.run_fanvue_post_now(
        config, conn, prepared.client, prepared.fanvue_handle, prepared.fanvue_url_template,
        prepared.items, prepared.text, prepared.audience, prepared.price_cents,
    )
