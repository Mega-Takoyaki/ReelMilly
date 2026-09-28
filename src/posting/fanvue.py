"""Fanvue公式APIクライアント(ADR-0003、認証方式はADR-0021)。

エンドポイント・リクエスト/レスポンス形式はFanvue公式OpenAPI仕様
(`https://api.fanvue.com/docs/openapi.json`、2026-09-28に一次情報で確認)
で照合済み。ただし認証方式はOAuth 2.0(ADR-0021、`posting.fanvue_oauth`)
専用であり、本クライアント自体は「有効なアクセストークンを返す関数」を
受け取るだけで、トークンの取得・更新方法には関知しない。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import requests

DEFAULT_API_BASE_URL = "https://api.fanvue.com"
DEFAULT_API_VERSION = "2025-06-26"
PART_SIZE_BYTES = 5 * 1024 * 1024  # サーバーがpartSizeを返さない場合のフォールバック値


class FanvueApiError(Exception):
    """Fanvue APIがエラーレスポンスを返した場合に送出する。"""


class FanvueClient:
    def __init__(
        self,
        api_token: str | Callable[[], str],
        base_url: str = DEFAULT_API_BASE_URL,
        api_version: str = DEFAULT_API_VERSION,
        session: requests.Session | None = None,
    ):
        """`api_token`は固定の文字列か、呼び出すたびに有効なトークンを返す関数。

        OAuth 2.0(ADR-0021)ではアクセストークンが短命でリフレッシュされ続ける
        ため、`FanvueTokenStore.get_access_token`のような関数を渡すことで、
        リクエストのたびに最新の有効なトークンを取得できるようにしている。
        """
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token
        self._session = session or requests.Session()
        self._session.headers.update({"X-Fanvue-API-Version": api_version})

    def _bearer_token(self) -> str:
        return self._api_token() if callable(self._api_token) else self._api_token

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def _request(self, method: str, path: str, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._bearer_token()}"
        response = self._session.request(method, self._url(path), headers=headers, **kwargs)
        if not response.ok:
            raise FanvueApiError(f"{method} {path} failed: {response.status_code} {response.text}")
        if response.content:
            return response.json()
        return {}

    def get_me(self) -> dict:
        """疎通確認用。`GET /users/me` を叩く。"""
        return self._request("GET", "/users/me")

    def upload_media(self, file_path: Path, media_type: str) -> str:
        """ファイルをmultipart uploadし、media uuidを返す。

        手順(公式OpenAPI仕様で確認済み):
        1. POST /media/uploads でアップロードセッションを作成（レスポンスに
           `mediaUuid`・`uploadId`・パートサイズ`partSize`が含まれる）
        2. 各パートごとに署名URL（レスポンスはURLそのものを表す文字列）を
           取得しPUTでアップロード
        3. PATCH /media/uploads/{id} でパート情報を送りファイナライズ
           （レスポンスは`status`のみで`mediaUuid`は含まれないため、1.の
           `mediaUuid`をそのまま返す）
        """
        size_bytes = file_path.stat().st_size

        init = self._request(
            "POST",
            "/media/uploads",
            json={
                "name": file_path.name,
                "filename": file_path.name,
                "mediaType": media_type,
                "sizeBytes": size_bytes,
            },
        )
        upload_id = init["uploadId"]
        part_size = init.get("partSize") or PART_SIZE_BYTES

        parts = []
        with file_path.open("rb") as f:
            part_number = 1
            while True:
                chunk = f.read(part_size)
                if not chunk:
                    break
                part_url = self._request(
                    "GET", f"/media/uploads/{upload_id}/parts/{part_number}/url"
                )
                put_response = requests.put(part_url, data=chunk)
                if not put_response.ok:
                    raise FanvueApiError(
                        f"part upload failed for part {part_number}: {put_response.status_code}"
                    )
                etag = put_response.headers.get("ETag", "")
                parts.append({"ETag": etag, "PartNumber": part_number})
                part_number += 1

        self._request("PATCH", f"/media/uploads/{upload_id}", json={"parts": parts})
        return init["mediaUuid"]

    def wait_for_media_ready(
        self,
        media_uuid: str,
        timeout_seconds: float = 90,
        poll_interval_seconds: float = 2.0,
    ) -> bool:
        """メディア処理が完了する(status=ready)までpollする。

        `status=error`の場合は即座に`FanvueApiError`を送出する。timeoutで
        諦めた場合はFalseを返す。
        """
        deadline = time.monotonic() + timeout_seconds
        while True:
            media = self._request("GET", f"/media/{media_uuid}")
            status = media.get("status")
            if status == "ready":
                return True
            if status == "error":
                raise FanvueApiError(f"media {media_uuid} processing failed (status=error)")
            if time.monotonic() >= deadline:
                return False
            time.sleep(poll_interval_seconds)

    def create_post(
        self,
        audience: str,
        text: str,
        media_uuids: list[str],
        price_cents: int | None = None,
        media_preview_uuid: str | None = None,
    ) -> dict:
        body: dict = {"audience": audience, "text": text, "mediaUuids": media_uuids}
        if price_cents is not None:
            body["price"] = price_cents
        if media_preview_uuid is not None:
            body["mediaPreviewUuid"] = media_preview_uuid
        return self._request("POST", "/posts", json=body)


def build_post_url(url_template: str, handle: str, uuid: str) -> str:
    """`.env`の`FANVUE_POST_URL_TEMPLATE`から公開URLを組み立てる(ADR-0003)。"""
    return url_template.format(handle=handle, uuid=uuid)
