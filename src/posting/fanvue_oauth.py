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
from urllib.parse import quote_plus, urlencode

import requests

DEFAULT_AUTHORIZATION_URL = "https://auth.fanvue.com/oauth2/auth"
DEFAULT_TOKEN_URL = "https://auth.fanvue.com/oauth2/token"
# 投稿に必要な許可(スコープ)。read:mediaは、アップロードしたメディアの処理状況(GET /media/{uuid})の確認に必要
# offline_accessは、アクセストークン(1時間で切れる)を更新するための「更新用トークン」を発行してもらう許可。
# これが無いと、更新用トークンが空になり、1時間後に投稿できなくなる(実機で起きた)
DEFAULT_SCOPES = ["offline_access", "read:self", "read:media", "write:media", "write:post", "read:post"]

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


def _basic_auth_header(client_id: str, client_secret: str) -> dict[str, str]:
    """client_secret_basic(HTTP Basic認証)のヘッダ。FanvueのOAuthアプリは、この方式を受け付ける(RFC 6749 2.3.1)。

    IDとシークレットは、base64の前に、フォームのURLエンコードをする決まり。
    """
    raw = f"{quote_plus(client_id)}:{quote_plus(client_secret)}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


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
        headers=_basic_auth_header(client_id, client_secret),
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
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


class FanvueTokenExpired(FanvueOAuthError):
    """アクセストークンの期限が切れていて、更新もできない(設定画面から、連携し直す必要がある)。"""


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
        # 途中で止まっても(停電・クラッシュ)、トークンのファイルが壊れたり、古い内容に戻ったりしないよう、
        # 一時ファイルに書いてディスクへ確実に書き出してから、置き換える
        import os

        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as f:
            f.write(json.dumps(asdict(tokens)))
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, self._path)

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()

    def get_access_token(self, client_id: str, client_secret: str) -> str:
        """有効なアクセストークンを返す。必要であれば自動的にリフレッシュする。

        まだ一度もFanvueと連携していない場合は`FanvueOAuthError`を送出する。
        """
        tokens = self.load()
        if tokens is None:
            raise FanvueOAuthError(self._not_connected_message())
        if not tokens.is_expired():
            return tokens.access_token
        with self._refresh_lock():
            # 待っている間に、別のプロセス(画面の投稿・定期の投稿)が更新していれば、それを使う
            # (更新用トークンは、使うと新しいものに替わる。同時に更新すると、片方が失敗して、連携が切れてしまう)
            tokens = self.load() or tokens
            if not tokens.is_expired():
                return tokens.access_token
            if not tokens.refresh_token:
                raise FanvueTokenExpired(self.RECONNECT_MESSAGE + "（更新用トークンがありません）")
            try:
                tokens = self._refresh(client_id, client_secret, tokens.refresh_token)
            except FanvueOAuthError as exc:
                if "invalid_grant" in str(exc):
                    raise FanvueTokenExpired(self.RECONNECT_MESSAGE) from exc
                raise
            self.save(tokens)
        return tokens.access_token

    RECONNECT_MESSAGE = "Fanvueの連携が切れています。設定の「Fanvue」タブで、連携し直してください"

    # 連携先ごとに違う部分(Xは、`x_oauth.XTokenStore`が引き継いで、置き換える)
    def _refresh(self, client_id: str, client_secret: str, refresh_token: str) -> TokenSet:
        return refresh_tokens(client_id, client_secret, refresh_token)

    def _not_connected_message(self) -> str:
        return "Fanvueと連携していません。本体UIの設定画面から連携してください。"

    def needs_reconnect(self) -> bool:
        """連携はしているが、期限が切れていて、更新もできない(連携し直しが必要)状態か。"""
        tokens = self.load()
        return tokens is not None and tokens.is_expired(margin_seconds=0) and not tokens.refresh_token

    def _refresh_lock(self):
        """プロセス間の排他(ロックファイル)。古いロックは、60秒で無効とみなす。"""
        import contextlib
        import os

        lock_path = self._path.with_suffix(".lock")

        @contextlib.contextmanager
        def lock():
            deadline = time.monotonic() + 30
            while True:
                try:
                    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.close(fd)
                    break
                except FileExistsError:
                    try:
                        if time.time() - lock_path.stat().st_mtime > 60:
                            lock_path.unlink(missing_ok=True)  # 前のプロセスが、ロックを残して止まった
                            continue
                    except OSError:
                        pass
                    if time.monotonic() > deadline:
                        break  # 待ちきれなければ、ロックなしで進める(止まり続けるよりは、よい)
                    time.sleep(0.2)
            try:
                yield
            finally:
                lock_path.unlink(missing_ok=True)

        return lock()
