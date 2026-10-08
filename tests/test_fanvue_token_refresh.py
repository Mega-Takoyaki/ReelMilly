"""Fanvueのトークン更新: 実機で、更新用トークンが空で、1時間後に投稿できなくなった。"""
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from posting.fanvue_oauth import (
    DEFAULT_SCOPES,
    FanvueOAuthError,
    FanvueTokenExpired,
    FanvueTokenStore,
    TokenSet,
    build_authorization_url,
)


def test_authorization_requests_offline_access_so_a_refresh_token_is_issued():
    """offline_accessが無いと、更新用トークンが空になる(実機で起きた)。"""
    assert "offline_access" in DEFAULT_SCOPES
    url = build_authorization_url(client_id="c", redirect_uri="http://x/cb", state="s", code_challenge="ch")
    assert "offline_access" in url


def _store(tmp_path, access="old", refresh="r1", expires_in=-10):
    store = FanvueTokenStore(tmp_path / "tokens.json")
    store.save(TokenSet(access_token=access, refresh_token=refresh, expires_at=time.time() + expires_in))
    return store


def test_valid_token_is_returned_without_refresh(tmp_path):
    store = _store(tmp_path, expires_in=3600)
    with patch("posting.fanvue_oauth.refresh_tokens") as refresh:
        assert store.get_access_token("c", "s") == "old"
    refresh.assert_not_called()


def test_expired_token_without_refresh_token_asks_to_reconnect(tmp_path):
    store = _store(tmp_path, refresh="")
    assert store.needs_reconnect() is True
    with pytest.raises(FanvueTokenExpired, match="連携し直してください"):
        store.get_access_token("c", "s")
    assert _store(tmp_path, expires_in=3600, refresh="").needs_reconnect() is False  # まだ有効なうちは、問題なし
    assert _store(tmp_path, refresh="r1").needs_reconnect() is False  # 更新用トークンがあれば、更新できる


def test_invalid_grant_on_refresh_becomes_a_friendly_reconnect_message(tmp_path):
    store = _store(tmp_path)
    with patch("posting.fanvue_oauth.refresh_tokens", side_effect=FanvueOAuthError('token refresh failed: 400 {"error":"invalid_grant"}')):
        with pytest.raises(FanvueTokenExpired, match="連携が切れています"):
            store.get_access_token("c", "s")
    with patch("posting.fanvue_oauth.refresh_tokens", side_effect=FanvueOAuthError("token refresh failed: 500 server error")):
        with pytest.raises(FanvueOAuthError) as exc:
            store.get_access_token("c", "s")
        assert not isinstance(exc.value, FanvueTokenExpired)  # 一時的な失敗は、連携が切れたことにしない


def test_refresh_saves_the_new_tokens(tmp_path):
    store = _store(tmp_path)
    new = TokenSet(access_token="new", refresh_token="r2", expires_at=time.time() + 3600)
    with patch("posting.fanvue_oauth.refresh_tokens", return_value=new) as refresh:
        assert store.get_access_token("c", "s") == "new"
    refresh.assert_called_once_with("c", "s", "r1")
    assert store.load().refresh_token == "r2"


def test_second_process_uses_the_token_the_first_one_refreshed(tmp_path):
    """画面の投稿とwatchが同時に更新すると、更新用トークン(使うと替わる)で片方が失敗する。待った側は、更新済みを使う。"""
    store = _store(tmp_path)
    other = FanvueTokenStore(tmp_path / "tokens.json")
    fresh = TokenSet(access_token="fresh", refresh_token="r2", expires_at=time.time() + 3600)

    original_lock = store._refresh_lock

    def lock_that_lets_the_other_refresh_first():
        # ロックを待っている間に、別のプロセスが更新を済ませた状況を作る
        other.save(fresh)
        return original_lock()

    store._refresh_lock = lock_that_lets_the_other_refresh_first
    with patch("posting.fanvue_oauth.refresh_tokens") as refresh:
        assert store.get_access_token("c", "s") == "fresh"
    refresh.assert_not_called()  # 二重に更新しない


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    store = _store(tmp_path, access="a", refresh="r", expires_in=3600)
    assert json.loads((tmp_path / "tokens.json").read_text(encoding="utf-8"))["access_token"] == "a"
    assert not (tmp_path / "tokens.tmp").exists() and not (tmp_path / "tokens.lock").exists()


def test_stale_lock_file_does_not_block_forever(tmp_path):
    store = _store(tmp_path)
    lock = tmp_path / "tokens.lock"
    lock.write_text("x")
    old = time.time() - 120
    import os

    os.utime(lock, (old, old))  # 前のプロセスが、ロックを残して止まった
    new = TokenSet(access_token="new", refresh_token="r2", expires_at=time.time() + 3600)
    with patch("posting.fanvue_oauth.refresh_tokens", return_value=new):
        assert store.get_access_token("c", "s") == "new"
    assert not lock.exists()


def test_settings_page_warns_when_reconnect_is_needed(tmp_path):
    from core.cli import cmd_init
    from core.config import load_config
    from core.web.app import create_app
    from tests.test_duplicates import CONFIG_YAML

    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    config = load_config(base_dir=tmp_path)
    cmd_init(config)
    client = create_app(config).test_client()
    FanvueTokenStore(config.paths.state_dir / "fanvue_oauth_tokens.json").save(
        TokenSet(access_token="a", refresh_token="", expires_at=time.time() - 5)
    )
    page = client.get("/settings").get_data(as_text=True)
    assert "Fanvueの連携が切れています" in page and "offline_access" in page
    FanvueTokenStore(config.paths.state_dir / "fanvue_oauth_tokens.json").save(
        TokenSet(access_token="a", refresh_token="r", expires_at=time.time() - 5)
    )
    assert "Fanvueの連携が切れています" not in client.get("/settings").get_data(as_text=True)
