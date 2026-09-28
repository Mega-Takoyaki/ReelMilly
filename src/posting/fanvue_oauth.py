"""Fanvue API認証(OAuth 2.0 authorization code + PKCE)。

Fanvue公式OpenAPI仕様（`https://api.fanvue.com/docs/openapi.json`、2026-09-28に
一次情報で確認）により、Fanvue APIは静的なAPIキー方式ではなくOAuth 2.0
(authorization codeフロー、PKCE)専用であることが判明した(ADR-0021)。

認可エンドポイント: https://auth.fanvue.com/oauth2/auth
トークンエンドポイント: https://auth.fanvue.com/oauth2/token（リフレッシュも同じ）

トークンエンドポイントのレスポンス形式自体は一次情報で確認できていない
（`auth.fanvue.com`側はOpenAPI仕様の対象外のため）。OAuth 2.0標準
(RFC 6749)のトークンレスポンス形式（`access_token`/`refresh_token`/
`expires_in`）を前提に実装しており、実機で接続する際に調整が必要になる
可能性がある。
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlencode

import requests

DEFAULT_AUTHORIZATION_URL = "https://auth.fanvue.com/oauth2/auth"
DEFAULT_TOKEN_URL = "https://auth.fanvue.com/oauth2/token"
DEFAULT_SCOPES = ["read:self", "write:media", "write:post", "read:post"]

# アクセストークンの実際の有効期限より手前でリフレッシュし、
# リクエスト直前の失効を避けるための安全マージン
_EXPIRY_MARGIN_SECONDS = 60


class FanvueOAuthError(Exception):
    """OAuth認可・トークン取得・更新に失敗した場合に送出する。"""


@dataclass
class PkcePair:
    verifier: str
    challenge: str


def generate_pkce_pair() -> PkcePair:
    """RFC 7636のcode_verifier/code_challenge(S256)ペアを生成する。"""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return PkcePair(verifier=verifier, challenge=challenge)


def generate_state() -> str:
    return secrets.token_urlsafe(24)


def build_authorization_url(
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    scopes: list[str] | None = None,
    authorization_url: str = DEFAULT_AUTHORIZATION_URL,
) -> str:
    scopes = scopes or DEFAULT_SCOPES
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{authorization_url}?{urlencode(params)}"


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    expires_at: float  # time.time()を基準としたUNIXタイムスタンプ

    def is_expired(self, margin_seconds: float = _EXPIRY_MARGIN_SECONDS) -> bool:
        return time.time() >= (self.expires_at - margin_seconds)


def _token_set_from_response(payload: dict) -> TokenSet:
    expires_in = payload.get("expires_in", 3600)
    return TokenSet(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token", ""),
        expires_at=time.time() + float(expires_in),
    )


def exchange_code_for_tokens(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
    code_verifier: str,
    token_url: str = DEFAULT_TOKEN_URL,
) -> TokenSet:
    response = requests.post(
        token_url,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
            "code_verifier": code_verifier,
        },
    )
    if not response.ok:
        raise FanvueOAuthError(f"token exchange failed: {response.status_code} {response.text}")
    return _token_set_from_response(response.json())


def refresh_tokens(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    token_url: str = DEFAULT_TOKEN_URL,
) -> TokenSet:
    response = requests.post(
        token_url,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    if not response.ok:
        raise FanvueOAuthError(f"token refresh failed: {response.status_code} {response.text}")
    new_tokens = _token_set_from_response(response.json())
    if not new_tokens.refresh_token:
        # 一部のOAuthサーバーはリフレッシュ時に新しいrefresh_tokenを返さず、
        # 既存のものを継続利用させる仕様のため、その場合は元の値を保持する
        new_tokens.refresh_token = refresh_token
    return new_tokens


class FanvueTokenStore:
    """取得したOAuthトークンをローカルファイルに永続化する(ADR-0021)。

    `.env`はユーザーが手動で編集する設定用ファイルのため、自動的に更新
    され続けるトークン類はここでは扱わず、`state_dir`配下の専用JSON
    ファイルに保存する(events.jsonl等と同じ置き場所)。
    """

    def __init__(self, path: Path):
        self._path = path

    def load(self) -> TokenSet | None:
        if not self._path.exists():
            return None
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return TokenSet(**data)

    def save(self, tokens: TokenSet) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(asdict(tokens)), encoding="utf-8")

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()

    def get_access_token(self, client_id: str, client_secret: str) -> str:
        """有効なアクセストークンを返す。必要であれば自動的にリフレッシュする。

        まだ一度もFanvueと連携していない場合は`FanvueOAuthError`を送出する。
        """
        tokens = self.load()
        if tokens is None:
            raise FanvueOAuthError(
                "Fanvueと連携していません。本体UIの設定画面から連携してください。"
            )
        if tokens.is_expired():
            tokens = refresh_tokens(client_id, client_secret, tokens.refresh_token)
            self.save(tokens)
        return tokens.access_token
