"""Fanvue公式APIクライアント(ADR-0003)。

CLAUDE_HANDOFF.md 7章の仕様に基づく実装。実際のレスポンス形式の詳細
(フィールド名等)は一次情報での検証を行っておらず、実機でのFanvue API
疎通確認(TODO.md参照)で調整が必要になる可能性がある。
"""
from __future__ import annotations

import time
from pathlib import Path

import requests

DEFAULT_API_BASE_URL = "https://api.fanvue.com"
DEFAULT_API_VERSION = "2025-06-26"
PART_SIZE_BYTES = 5 * 1024 * 1024  # 5MB単位でチャンク分割


class FanvueApiError(Exception):
    """Fanvue APIがエラーレスポンスを返した場合に送出する。"""


class FanvueClient:
    def __init__(
        self,
        api_token: str,
        base_url: str = DEFAULT_API_BASE_URL,
        api_version: str = DEFAULT_API_VERSION,
        session: requests.Session | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {api_token}",
                "X-Fanvue-API-Version": api_version,
            }
        )

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def _request(self, method: str, path: str, **kwargs) -> dict:
        response = self._session.request(method, self._url(path), **kwargs)
        if not response.ok:
            raise FanvueApiError(f"{method} {path} failed: {response.status_code} {response.text}")
        if response.content:
            return response.json()
        return {}

    def get_me(self) -> dict:
        """疎通確認用。`GET /users/me` を叩く(TODO.md参照)。"""
        return self._request("GET", "/users/me")

    def upload_media(self, file_path: Path, media_type: str) -> str:
        """ファイルをmultipart uploadし、media uuidを返す。

        手順(CLAUDE_HANDOFF.md 7章):
        1. POST /media/uploads でアップロードセッションを作成
        2. 各パートごとに署名URLを取得しPUTでアップロード
        3. PATCH /media/uploads/{id} でパート情報を送りファイナライズ
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

        parts = []
        with file_path.open("rb") as f:
            part_number = 1
            while True:
                chunk = f.read(PART_SIZE_BYTES)
                if not chunk:
                    break
                part_url_info = self._request(
                    "GET", f"/media/uploads/{upload_id}/parts/{part_number}/url"
                )
                put_response = requests.put(part_url_info["url"], data=chunk)
                if not put_response.ok:
                    raise FanvueApiError(
                        f"part upload failed for part {part_number}: {put_response.status_code}"
                    )
                etag = put_response.headers.get("ETag", "")
                parts.append({"ETag": etag, "PartNumber": part_number})
                part_number += 1

        finalize = self._request("PATCH", f"/media/uploads/{upload_id}", json={"parts": parts})
        return finalize.get("mediaUuid") or init.get("mediaUuid") or upload_id

    def wait_for_media_ready(
        self,
        media_uuid: str,
        timeout_seconds: float = 90,
        poll_interval_seconds: float = 2.0,
    ) -> bool:
        """メディア処理が完了する(ready/finalised)までpollする。timeoutで諦めFalseを返す。"""
        deadline = time.monotonic() + timeout_seconds
        while True:
            media = self._request("GET", f"/media/{media_uuid}")
            if media.get("status") in ("ready", "finalised"):
                return True
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
