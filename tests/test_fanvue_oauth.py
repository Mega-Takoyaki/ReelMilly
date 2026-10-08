import base64
import time
from unittest.mock import MagicMock, patch

import pytest

from posting.fanvue_oauth import (
    FanvueOAuthError,
    FanvueTokenStore,
    TokenSet,
    build_authorization_url,
    exchange_code_for_tokens,
    generate_pkce_pair,
    refresh_tokens,
)


def _mock_response(status_code=200, json_data=None, text=""):
    response = MagicMock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.json.return_value = json_data or {}
    response.text = text
    return response


def test_generate_pkce_pair_produces_valid_verifier_and_challenge():
    pair = generate_pkce_pair()

    assert 43 <= len(pair.verifier) <= 128
    assert pair.challenge != pair.verifier
    # base64url(SHA256(verifier))はパディング("=")を含まない
    assert "=" not in pair.challenge


def test_generate_pkce_pair_is_random_each_call():
    pair1 = generate_pkce_pair()
    pair2 = generate_pkce_pair()

    assert pair1.verifier != pair2.verifier
    assert pair1.challenge != pair2.challenge


def test_build_authorization_url_includes_required_params():
    url = build_authorization_url(
        client_id="client-1",
        redirect_uri="http://127.0.0.1:8420/settings/fanvue/oauth/callback",
        state="state-1",
        code_challenge="challenge-1",
        scopes=["read:self", "write:post"],
    )

    assert url.startswith("https://auth.fanvue.com/oauth2/auth?")
    assert "client_id=client-1" in url
    assert "state=state-1" in url
    assert "code_challenge=challenge-1" in url
    assert "code_challenge_method=S256" in url
    assert "response_type=code" in url
    assert "scope=read%3Aself+write%3Apost" in url


def test_exchange_code_for_tokens_posts_expected_params():
    response = _mock_response(json_data={"access_token": "at1", "refresh_token": "rt1", "expires_in": 3600})
    with patch("posting.fanvue_oauth.requests.post", return_value=response) as mocked_post:
        tokens = exchange_code_for_tokens(
            client_id="client-1",
            client_secret="secret-1",
            redirect_uri="http://localhost/callback",
            code="auth-code",
            code_verifier="verifier-1",
        )

    assert tokens.access_token == "at1"
    assert tokens.refresh_token == "rt1"
    assert tokens.expires_at > time.time()
    _, kwargs = mocked_post.call_args
    assert kwargs["data"]["grant_type"] == "authorization_code"
    assert kwargs["data"]["code"] == "auth-code"
    assert kwargs["data"]["code_verifier"] == "verifier-1"
    # クライアント認証は、client_secret_basic(Authorizationヘッダ)。シークレットを、リクエスト本文には入れない
    assert kwargs["headers"]["Authorization"] == "Basic " + base64.b64encode(b"client-1:secret-1").decode()
    assert "client_secret" not in kwargs["data"] and "client_id" not in kwargs["data"]


def test_exchange_code_for_tokens_raises_on_error_response():
    response = _mock_response(status_code=400, text="invalid_grant")
    with patch("posting.fanvue_oauth.requests.post", return_value=response):
        with pytest.raises(FanvueOAuthError):
            exchange_code_for_tokens(
                client_id="c", client_secret="s", redirect_uri="http://x", code="bad", code_verifier="v"
            )


def test_refresh_tokens_posts_refresh_grant():
    response = _mock_response(json_data={"access_token": "at2", "refresh_token": "rt2", "expires_in": 3600})
    with patch("posting.fanvue_oauth.requests.post", return_value=response) as mocked_post:
        tokens = refresh_tokens(client_id="c", client_secret="s", refresh_token="rt1")

    assert tokens.access_token == "at2"
    assert tokens.refresh_token == "rt2"
    _, kwargs = mocked_post.call_args
    assert kwargs["data"]["grant_type"] == "refresh_token"
    assert kwargs["data"]["refresh_token"] == "rt1"
    assert kwargs["headers"]["Authorization"] == "Basic " + base64.b64encode(b"c:s").decode()
    assert "client_secret" not in kwargs["data"]


def test_refresh_tokens_keeps_old_refresh_token_when_not_returned():
    response = _mock_response(json_data={"access_token": "at2", "expires_in": 3600})
    with patch("posting.fanvue_oauth.requests.post", return_value=response):
        tokens = refresh_tokens(client_id="c", client_secret="s", refresh_token="rt1")

    assert tokens.refresh_token == "rt1"


def test_token_set_is_expired_respects_margin():
    fresh = TokenSet(access_token="a", refresh_token="r", expires_at=time.time() + 3600)
    expiring_soon = TokenSet(access_token="a", refresh_token="r", expires_at=time.time() + 10)

    assert fresh.is_expired() is False
    assert expiring_soon.is_expired() is True


def test_token_store_save_and_load_roundtrip(tmp_path):
    store = FanvueTokenStore(tmp_path / "fanvue_oauth_tokens.json")
    tokens = TokenSet(access_token="a", refresh_token="r", expires_at=time.time() + 3600)

    store.save(tokens)
    loaded = store.load()

    assert loaded == tokens


def test_token_store_load_returns_none_when_missing(tmp_path):
    store = FanvueTokenStore(tmp_path / "missing.json")

    assert store.load() is None


def test_token_store_get_access_token_raises_when_not_connected(tmp_path):
    store = FanvueTokenStore(tmp_path / "missing.json")

    with pytest.raises(FanvueOAuthError):
        store.get_access_token("client-id", "client-secret")


def test_token_store_get_access_token_returns_existing_when_not_expired(tmp_path):
    store = FanvueTokenStore(tmp_path / "tokens.json")
    store.save(TokenSet(access_token="valid-token", refresh_token="r", expires_at=time.time() + 3600))

    with patch("posting.fanvue_oauth.requests.post") as mocked_post:
        token = store.get_access_token("client-id", "client-secret")

    assert token == "valid-token"
    mocked_post.assert_not_called()


def test_token_store_get_access_token_refreshes_when_expired(tmp_path):
    store = FanvueTokenStore(tmp_path / "tokens.json")
    store.save(TokenSet(access_token="old-token", refresh_token="old-refresh", expires_at=time.time() - 10))

    response = _mock_response(json_data={"access_token": "new-token", "refresh_token": "new-refresh", "expires_in": 3600})
    with patch("posting.fanvue_oauth.requests.post", return_value=response):
        token = store.get_access_token("client-id", "client-secret")

    assert token == "new-token"
    assert store.load().access_token == "new-token"


def test_token_store_clear_removes_file(tmp_path):
    path = tmp_path / "tokens.json"
    store = FanvueTokenStore(path)
    store.save(TokenSet(access_token="a", refresh_token="r", expires_at=time.time() + 3600))

    store.clear()

    assert not path.exists()
    assert store.load() is None


def test_redirect_uri_is_fixed_regardless_of_the_address_used_to_open_the_page(tmp_path, monkeypatch):
    """Fanvueに登録した値と完全一致させる必要があるため、画面を開いたアドレス(localhost・Tailscale名)で変わらない。"""
    from urllib.parse import parse_qs, urlparse

    from tests.test_duplicates import CONFIG_YAML
    from core.cli import cmd_init
    from core.config import load_config
    from core.web.app import create_app

    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    config = load_config(base_dir=tmp_path)
    cmd_init(config)
    monkeypatch.setenv("FANVUE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.delenv("FANVUE_OAUTH_REDIRECT_URI", raising=False)
    client = create_app(config).test_client()
    expected = "http://127.0.0.1:8420/settings/fanvue/oauth/callback"

    for host in ("localhost:8420", "my-pc.tail1234.ts.net", "127.0.0.1:8420"):
        res = client.get("/settings/fanvue/oauth/start", headers={"Host": host})
        sent = parse_qs(urlparse(res.headers["Location"]).query)["redirect_uri"][0]
        assert sent == expected, host

    page = client.get("/settings", headers={"Host": "localhost:8420"}).get_data(as_text=True)
    assert f'id="fanvue-redirect-uri">{expected}<' in page  # 登録すべき値を、設定画面に表示している

    monkeypatch.setenv("FANVUE_OAUTH_REDIRECT_URI", "https://example.test/cb")  # 明示した値が優先
    res = client.get("/settings/fanvue/oauth/start")
    assert parse_qs(urlparse(res.headers["Location"]).query)["redirect_uri"][0] == "https://example.test/cb"
