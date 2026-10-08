"""X(旧Twitter)のOAuth 2.0(認可コード+PKCE)による連携。

FanvueのOAuth(`fanvue_oauth.py`)と同じ作りで、トークンの保存・更新・排他は、そのクラスを引き継ぐ。
違い: Xのアクセストークンは約2時間で切れ、更新用トークンは、更新するたびに新しいものに替わる(古いものは使えなくなる)。
更新用トークンを得るには、`offline.access`の許可(スコープ)が要る。

エンドポイント・スコープは、Xの公式OpenAPI(https://api.x.com/2/openapi.json)で確認した。認可の画面は、
ブラウザで開く`https://x.com/i/oauth2/authorize`、トークンは`https://api.x.com/2/oauth2/token`(機密クライアントは、Basic認証)。
"""
from __future__ import annotations

import time
from urllib.parse import urlencode

import requests

from posting.fanvue_oauth import (
    FanvueOAuthError,
    FanvueTokenExpired,
    FanvueTokenStore,
    TokenSet,
    _basic_auth_header,
    _token_set_from_response,
    generate_pkce_pair,
    generate_state,
)

DEFAULT_AUTHORIZATION_URL = "https://x.com/i/oauth2/authorize"
DEFAULT_TOKEN_URL = "https://api.x.com/2/oauth2/token"
# tweet.write: 投稿 / media.write: 画像・動画のアップロード / users.read・tweet.read: 投稿の作成に必要 /
# offline.access: 更新用トークンの発行(無いと、2時間後に投稿できなくなる)
DEFAULT_SCOPES = ["tweet.read", "tweet.write", "users.read", "media.write", "offline.access"]

__all__ = [
    "XOAuthError", "XTokenExpired", "XTokenStore", "build_authorization_url", "exchange_code_for_tokens",
    "refresh_tokens", "generate_pkce_pair", "generate_state", "TokenSet", "DEFAULT_SCOPES",
]

XOAuthError = FanvueOAuthError
XTokenExpired = FanvueTokenExpired


def build_authorization_url(
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    scopes: list[str] | None = None,
    authorization_url: str = DEFAULT_AUTHORIZATION_URL,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(scopes or DEFAULT_SCOPES),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{authorization_url}?{urlencode(params)}"


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
        headers=_basic_auth_header(client_id, client_secret),
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": code_verifier,
        },
    )
    if not response.ok:
        raise XOAuthError(f"X token exchange failed: {response.status_code} {response.text}")
    return _token_set_from_response(response.json())


def refresh_tokens(
    client_id: str, client_secret: str, refresh_token: str, token_url: str = DEFAULT_TOKEN_URL
) -> TokenSet:
    response = requests.post(
        token_url,
        headers=_basic_auth_header(client_id, client_secret),
        data={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id},
    )
    if not response.ok:
        raise XOAuthError(f"X token refresh failed: {response.status_code} {response.text}")
    new_tokens = _token_set_from_response(response.json())
    if not new_tokens.refresh_token:
        new_tokens.refresh_token = refresh_token
    return new_tokens


class XTokenStore(FanvueTokenStore):
    """Xのトークンの保存・更新(保存・排他・期限切れの扱いは、Fanvueと共通)。"""

    RECONNECT_MESSAGE = "Xの連携が切れています。設定の「X」タブで、連携し直してください"

    def _refresh(self, client_id: str, client_secret: str, refresh_token: str) -> TokenSet:
        return refresh_tokens(client_id, client_secret, refresh_token)

    def _not_connected_message(self) -> str:
        return "Xと連携していません。本体UIの設定画面から連携してください。"
