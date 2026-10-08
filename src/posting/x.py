"""X(旧Twitter)の公式APIクライアント(OAuth 2.0のユーザートークンで呼ぶ)。

エンドポイント・リクエスト/レスポンス形式は、Xの公式OpenAPI(`https://api.x.com/2/openapi.json`)で照合した:
- `POST /2/media/upload`(画像: 1回で)、`POST /2/media/upload/initialize`→`/{id}/append`→`/{id}/finalize`(動画: 分割)、
  `GET /2/media/upload?command=STATUS&media_id=`(動画の処理状況)、`POST /2/media/metadata`(センシティブ等の指定)
- `POST /2/tweets`(`media.media_ids`・`made_with_ai`)

X APIは従量課金(Pay Per Use)のため、呼ぶたびに料金がかかる。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import requests

DEFAULT_API_BASE_URL = "https://api.x.com"
CHUNK_BYTES = 4 * 1024 * 1024
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
RETRY_WAITS = [2, 5, 10, 20]  # 再試行の前に待つ秒数(順に)
UPLOAD_RETRIES = 3
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # Xの画像の上限(5MB)
MAX_VIDEO_BYTES = 512 * 1024 * 1024
MAX_IMAGES_PER_POST = 4  # 1つの投稿に付けられる画像は4枚まで。動画は1本だけ(画像との混在は不可)
_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
    ".gif": "image/gif", ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
}


class XApiError(Exception):
    """X APIがエラーを返した場合。"""


def check_media_set(kinds: list[str]) -> str | None:
    """1つの投稿に付けるメディア(image/video)の組み合わせの検査。問題が無ければNone、あれば理由。"""
    if not kinds:
        return "メディアがありません"
    if "video" in kinds:
        return None if kinds == ["video"] else "Xでは、動画は1本だけで、画像とは一緒に投稿できません"
    if len(kinds) > MAX_IMAGES_PER_POST:
        return f"Xでは、1つの投稿に付けられる画像は{MAX_IMAGES_PER_POST}枚までです"
    return None


class XClient:
    def __init__(self, access_token: str | Callable[[], str], base_url: str = DEFAULT_API_BASE_URL, session: requests.Session | None = None):
        self._base_url = base_url.rstrip("/")
        self._token = access_token
        self._session = session or requests.Session()

    def _bearer(self) -> str:
        return self._token() if callable(self._token) else self._token

    def _request(self, method: str, path: str, retries: int = 0, **kwargs):
        """APIを呼ぶ。`retries`は、Xのサーバーの一時的な不調(429・5xx)のときの、再試行の回数。

        再試行するのは、やり直しても二重にならない呼び出し(アップロード・メタデータ・状態の確認)だけ。
        投稿そのもの(`create_post`)は、成功していて応答だけ失敗した場合に、二重投稿になるため、再試行しない。
        """
        attempt = 0
        while True:
            headers = dict(kwargs.get("headers", {}))
            headers["Authorization"] = f"Bearer {self._bearer()}"
            call_kwargs = {k: v for k, v in kwargs.items() if k != "headers"}
            # ファイルの読み取り位置は、再試行のたびに、先頭に戻す
            for _name, value in (call_kwargs.get("files") or {}).items():
                stream = value[1] if isinstance(value, tuple) and len(value) > 1 else None
                if hasattr(stream, "seek"):
                    stream.seek(0)
            response = self._session.request(method, f"{self._base_url}{path}", headers=headers, **call_kwargs)
            if response.ok:
                return response.json() if response.content else {}
            if response.status_code in RETRYABLE_STATUS and attempt < retries:
                time.sleep(RETRY_WAITS[min(attempt, len(RETRY_WAITS) - 1)])
                attempt += 1
                continue
            hint = "（Xのサーバーが一時的に使えません。少し待って、もう一度試してください）" if response.status_code in RETRYABLE_STATUS else ""
            raise XApiError(f"{method} {path} failed: {response.status_code} {response.text}{hint}")

    def get_me(self) -> dict:
        """疎通確認(連携したアカウントの情報)。"""
        return self._request("GET", "/2/users/me").get("data", {})

    def upload_media(self, path: Path, media_type: str, sensitive: bool = False) -> str:
        """画像・動画をアップロードして、media_idを返す。`sensitive`なら、成人向けのセンシティブなメディアとして指定する。"""
        size = path.stat().st_size
        mime = _MIME.get(path.suffix.lower())
        if mime is None:
            raise XApiError(f"Xにアップロードできない形式です: {path.suffix}")
        if media_type == "image":
            if size > MAX_IMAGE_BYTES:
                raise XApiError(f"画像が大きすぎます({size / 1048576:.1f}MB。Xの上限は5MB)")
            with path.open("rb") as f:
                data = self._request(
                    "POST", "/2/media/upload", retries=UPLOAD_RETRIES,
                    data={"media_category": "tweet_gif" if mime == "image/gif" else "tweet_image"},
                    files={"media": (path.name, f, mime)},
                )
            media_id = data["data"]["id"]
        else:
            media_id = self._upload_video(path, size, mime)
        if sensitive:
            # 成人向けのセンシティブなメディアとして、投稿前に指定する。
            # 形は、配列ではなく、真偽値の3項目のオブジェクト(実機で、配列は400になった。公式ドキュメントで確認)
            result = self._request(
                "POST", "/2/media/metadata", retries=UPLOAD_RETRIES,
                json={"id": media_id, "metadata": {"sensitive_media_warning": {"adult_content": True, "graphic_violence": False, "other": False}}},
            )
            # Xが、指定を記録したかを、応答で確かめる(つけたつもりで、つかずに投稿してしまわないため)
            recorded = (result.get("data", {}).get("associated_metadata", {}) or {}).get("sensitive_media_warning") or {}
            if not recorded.get("adult_content"):
                raise XApiError(f"センシティブ指定が、Xに記録されませんでした: {result}")
        return media_id

    def _upload_video(self, path: Path, size: int, mime: str) -> str:
        if size > MAX_VIDEO_BYTES:
            raise XApiError(f"動画が大きすぎます({size / 1048576:.0f}MB。Xの上限は512MB)")
        init = self._request(
            "POST", "/2/media/upload/initialize", retries=UPLOAD_RETRIES,
            json={"media_category": "tweet_video", "media_type": mime, "total_bytes": size},
        )
        media_id = init["data"]["id"]
        with path.open("rb") as f:
            index = 0
            while True:
                chunk = f.read(CHUNK_BYTES)
                if not chunk:
                    break
                self._request(
                    "POST", f"/2/media/upload/{media_id}/append", retries=UPLOAD_RETRIES,
                    data={"segment_index": index}, files={"media": (path.name, chunk, mime)},
                )
                index += 1
        done = self._request("POST", f"/2/media/upload/{media_id}/finalize", retries=UPLOAD_RETRIES).get("data", {})
        self._wait_processing(media_id, done.get("processing_info"))
        return media_id

    def _wait_processing(self, media_id: str, info: dict | None, timeout_seconds: float = 300) -> None:
        """動画の処理が終わる(succeeded)まで待つ。failedなら、エラー。"""
        deadline = time.monotonic() + timeout_seconds
        while info and info.get("state") not in (None, "succeeded"):
            if info.get("state") == "failed":
                raise XApiError(f"動画の処理に失敗しました: {info}")
            if time.monotonic() > deadline:
                raise XApiError("動画の処理が時間切れになりました")
            time.sleep(min(max(int(info.get("check_after_secs") or 2), 1), 30))
            info = self._request("GET", "/2/media/upload", params={"command": "STATUS", "media_id": media_id}, retries=UPLOAD_RETRIES).get("data", {}).get("processing_info")

    def create_post(self, text: str, media_ids: list[str], made_with_ai: bool = False) -> dict:
        """投稿する。`made_with_ai`は、AI生成のメディアを含むことを、Xの投稿に表示する申告。"""
        body: dict = {"text": text, "media": {"media_ids": media_ids}}
        if made_with_ai:
            body["made_with_ai"] = True
        return self._request("POST", "/2/tweets", json=body).get("data", {})


def build_post_url(post_id: str) -> str:
    """投稿のアドレス(ユーザー名が要らない形)。"""
    return f"https://x.com/i/web/status/{post_id}"
