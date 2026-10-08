"""X(旧Twitter)への投稿: OAuth・APIクライアント・投稿ジョブ・設定画面・今すぐ投稿(方針の検査)。"""
import base64
import time
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from core import db, notifications
from core.web.app import create_app
from posting import x as x_module
from posting import x_oauth
from posting.jobs import run_x_post_now
from posting.x import XApiError, XClient, check_media_set

from tests.test_worker import _insert, _setup  # noqa: F401


# ------------------------------------------------------------------ OAuth
def test_authorization_url_has_pkce_and_offline_access():
    url = x_oauth.build_authorization_url("cid", "http://127.0.0.1:8420/settings/x/oauth/callback", "st", "ch")
    query = parse_qs(urlparse(url).query)
    assert url.startswith("https://x.com/i/oauth2/authorize?")
    assert query["code_challenge_method"] == ["S256"] and query["response_type"] == ["code"]
    scopes = query["scope"][0].split()
    assert {"tweet.write", "media.write", "users.read", "tweet.read", "offline.access"} <= set(scopes)  # offline.accessが無いと、2時間で切れる


def _resp(json_data=None, ok=True, status=200, text=""):
    r = MagicMock()
    r.ok = ok
    r.status_code = status
    r.json.return_value = json_data or {}
    r.text = text
    r.content = b"{}" if json_data is not None else b""
    return r


def test_token_exchange_and_refresh_use_basic_auth():
    ok = _resp({"access_token": "at", "refresh_token": "rt", "expires_in": 7200})
    with patch("posting.x_oauth.requests.post", return_value=ok) as post:
        tokens = x_oauth.exchange_code_for_tokens("cid", "sec", "http://x/cb", "code", "verifier")
    assert tokens.access_token == "at" and tokens.refresh_token == "rt"
    kwargs = post.call_args.kwargs
    assert post.call_args.args[0] == "https://api.x.com/2/oauth2/token"
    assert kwargs["headers"]["Authorization"] == "Basic " + base64.b64encode(b"cid:sec").decode()
    assert kwargs["data"]["code_verifier"] == "verifier" and "client_secret" not in kwargs["data"]

    with patch("posting.x_oauth.requests.post", return_value=_resp({"access_token": "at2", "expires_in": 7200})) as post:
        refreshed = x_oauth.refresh_tokens("cid", "sec", "rt")
    assert refreshed.refresh_token == "rt"  # 新しい更新用トークンが無ければ、元のものを残す
    assert post.call_args.kwargs["data"]["grant_type"] == "refresh_token"

    with patch("posting.x_oauth.requests.post", return_value=_resp(ok=False, status=400, text="invalid_grant")):
        with pytest.raises(x_oauth.XOAuthError, match="refresh failed"):
            x_oauth.refresh_tokens("cid", "sec", "rt")


def test_x_token_store_refreshes_with_x_and_reports_x_messages(tmp_path):
    store = x_oauth.XTokenStore(tmp_path / "x.json")
    store.save(x_oauth.TokenSet("old", "r1", time.time() - 5))
    new = x_oauth.TokenSet("new", "r2", time.time() + 7200)
    with patch("posting.x_oauth.refresh_tokens", return_value=new) as refresh, patch("posting.fanvue_oauth.refresh_tokens") as fanvue_refresh:
        assert store.get_access_token("c", "s") == "new"
    refresh.assert_called_once_with("c", "s", "r1")
    fanvue_refresh.assert_not_called()  # Fanvueの更新を呼ばない

    store.save(x_oauth.TokenSet("old", "", time.time() - 5))
    with pytest.raises(x_oauth.XTokenExpired, match="Xの連携が切れています"):
        store.get_access_token("c", "s")
    with pytest.raises(x_oauth.XOAuthError, match="Xと連携していません"):
        x_oauth.XTokenStore(tmp_path / "none.json").get_access_token("c", "s")


# ------------------------------------------------------------------ APIクライアント
def test_media_set_rules():
    assert check_media_set(["image"] * 4) is None and check_media_set(["video"]) is None
    assert "4枚" in check_media_set(["image"] * 5)
    assert "1本" in check_media_set(["video", "video"]) and "1本" in check_media_set(["video", "image"])
    assert check_media_set([]) is not None


def make_client(*responses):
    session = MagicMock()
    session.request.side_effect = list(responses)
    return XClient("token", session=session), session


def test_image_upload_posts_multipart_and_sets_the_sensitive_flag(tmp_path):
    image = tmp_path / "a.png"
    image.write_bytes(b"png-bytes")
    client, session = make_client(_resp({"data": {"id": "111"}}), _resp({"data": {}}))

    assert client.upload_media(image, "image", sensitive=True) == "111"

    upload, metadata = session.request.call_args_list
    assert upload.args[:2] == ("POST", "https://api.x.com/2/media/upload")
    assert upload.kwargs["data"]["media_category"] == "tweet_image" and upload.kwargs["headers"]["Authorization"] == "Bearer token"
    assert metadata.args[:2] == ("POST", "https://api.x.com/2/media/metadata")
    # センシティブ指定: 配列ではなく、真偽値のオブジェクト(配列は、実機で400になった)
    assert metadata.kwargs["json"] == {
        "id": "111",
        "metadata": {"sensitive_media_warning": {"adult_content": True, "graphic_violence": False, "other": False}},
    }


def test_image_upload_without_sensitive_does_not_call_metadata(tmp_path):
    image = tmp_path / "a.jpg"
    image.write_bytes(b"x")
    client, session = make_client(_resp({"data": {"id": "5"}}))
    client.upload_media(image, "image")
    assert session.request.call_count == 1


def test_oversize_image_and_unknown_extension_are_refused(tmp_path):
    big = tmp_path / "big.png"
    big.write_bytes(b"x" * (x_module.MAX_IMAGE_BYTES + 1))
    client, session = make_client()
    with pytest.raises(XApiError, match="大きすぎます"):
        client.upload_media(big, "image")
    odd = tmp_path / "a.bmpx"
    odd.write_bytes(b"x")
    with pytest.raises(XApiError, match="形式"):
        client.upload_media(odd, "image")
    session.request.assert_not_called()


def test_video_upload_is_chunked_and_waits_for_processing(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"v" * 10)
    client, session = make_client(
        _resp({"data": {"id": "900"}}),                                               # initialize
        _resp({"data": {}}),                                                          # append
        _resp({"data": {"processing_info": {"state": "pending", "check_after_secs": 1}}}),  # finalize
        _resp({"data": {"processing_info": {"state": "succeeded"}}}),                 # status
    )
    with patch("posting.x.time.sleep") as sleep, patch("posting.x.CHUNK_BYTES", 4):
        # 10バイトを4バイトずつ: 3回のappend
        session.request.side_effect = [
            _resp({"data": {"id": "900"}}), _resp({"data": {}}), _resp({"data": {}}), _resp({"data": {}}),
            _resp({"data": {"processing_info": {"state": "pending", "check_after_secs": 1}}}),
            _resp({"data": {"processing_info": {"state": "succeeded"}}}),
        ]
        assert client.upload_media(video, "video") == "900"
    calls = [(c.args[0], c.args[1].replace("https://api.x.com", "")) for c in session.request.call_args_list]
    assert calls[0] == ("POST", "/2/media/upload/initialize")
    assert [c[1] for c in calls[1:4]] == ["/2/media/upload/900/append"] * 3
    assert calls[4] == ("POST", "/2/media/upload/900/finalize") and calls[5] == ("GET", "/2/media/upload")
    assert [c.kwargs["data"]["segment_index"] for c in session.request.call_args_list[1:4]] == [0, 1, 2]
    assert session.request.call_args_list[0].kwargs["json"]["media_category"] == "tweet_video"
    sleep.assert_called()


def test_video_processing_failure_is_reported(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    client, session = make_client(
        _resp({"data": {"id": "9"}}), _resp({"data": {}}),
        _resp({"data": {"processing_info": {"state": "failed"}}}),
    )
    with pytest.raises(XApiError, match="処理に失敗"):
        client.upload_media(video, "video")


def test_create_post_sends_media_and_ai_flag():
    client, session = make_client(_resp({"data": {"id": "42", "text": "t"}}))
    assert client.create_post("本文", ["1", "2"], made_with_ai=True)["id"] == "42"
    assert session.request.call_args.kwargs["json"] == {"text": "本文", "media": {"media_ids": ["1", "2"]}, "made_with_ai": True}
    client, session = make_client(_resp({"data": {"id": "43"}}))
    client.create_post("本文", ["1"], made_with_ai=False)
    assert "made_with_ai" not in session.request.call_args.kwargs["json"]
    client, session = make_client(_resp(ok=False, status=403, text="forbidden"))
    with pytest.raises(XApiError, match="403"):
        client.create_post("x", ["1"])


# ------------------------------------------------------------------ 投稿ジョブ
@pytest.fixture
def env(tmp_path):
    config, conn = _setup(tmp_path)
    app = create_app(config)
    app.config["POST_NOW_SYNC"] = True
    return config, conn, app.test_client()


def fake_x():
    x = MagicMock()
    x.upload_media.side_effect = ["m1", "m2", "m3", "m4", "m5"]
    x.create_post.return_value = {"id": "777"}
    return x


def test_run_x_post_now_records_posts_and_notifies(env):
    config, conn, _ = env
    for i in (1, 2):
        _insert(conn, config.paths.ready, f"a{i}")
    items = [(db.get_asset(conn, f"a{i}"), config.paths.ready / f"a{i}.jpg", "image") for i in (1, 2)]
    x = fake_x()

    result = run_x_post_now(config, conn, x, items, "本文", sensitive=True, made_with_ai=True)

    assert result.ok and result.fanvue_url == "https://x.com/i/web/status/777"
    assert [c.kwargs["sensitive"] for c in x.upload_media.call_args_list] == [True, True]
    x.create_post.assert_called_once_with("本文", ["m1", "m2"], made_with_ai=True)
    for i in (1, 2):
        post = db.get_posts(conn, [f"a{i}"])[f"a{i}"]["x"]
        assert post["status"] == "posted" and post["url"].endswith("/777")
        assert db.get_asset(conn, f"a{i}")["status"] == "ready"
    item = notifications.list_notifications(conn, 1)[0]
    assert "Xへ投稿しました" in item["title"] and "センシティブ指定" in item["body"]


def test_run_x_post_now_failure_marks_failed(env):
    config, conn, _ = env
    _insert(conn, config.paths.ready, "a1")
    x = fake_x()
    x.create_post.side_effect = XApiError("POST /2/tweets failed: 403")
    result = run_x_post_now(config, conn, x, [(db.get_asset(conn, "a1"), config.paths.ready / "a1.jpg", "image")], "t")
    assert not result.ok and db.get_posts(conn, ["a1"])["a1"]["x"]["status"] == "failed"
    assert db.get_asset(conn, "a1")["status"] == "analyzing"  # 失敗では、状態を変えない
    assert notifications.list_notifications(conn, 1)[0]["level"] == "error"


# ------------------------------------------------------------------ 今すぐ投稿(Xの方針)
def post_x(client, x, **payload):
    with patch("core.cli._try_create_x_client", return_value=(x, None) if x else (None, "Xと未連携です")):
        return client.post("/api/post-now", json={"channel": "x", **payload})


def approve(conn, asset_id, rating):
    db.update_asset(conn, asset_id, content_rating=rating, content_rating_confirmed=1)


def test_x_has_no_rating_restrictions_and_leaves_sensitive_to_the_user(env):
    config, conn, client = env
    _insert(conn, config.paths.ready, "a1")
    item = [{"asset_id": "a1", "version": "original"}]

    assert post_x(client, None, items=item, text="t").status_code == 503  # 未連携

    # 未承認でも、人がセンシティブ指定を外して(つけずに)投稿できる。指定をつければ、つけて投稿する
    x = fake_x()
    assert post_x(client, x, items=item, text="t").status_code == 202
    assert x.upload_media.call_args.kwargs["sensitive"] is False
    ok = post_x(client, fake_x(), items=item, text="t", sensitive=True)
    assert ok.status_code == 202 and ok.get_json()["ok"]

    # suggestive: 同じく、人が決める
    _insert(conn, config.paths.ready, "a2")
    approve(conn, "a2", "suggestive")
    item2 = [{"asset_id": "a2", "version": "original"}]
    assert post_x(client, fake_x(), items=item2, text="t").status_code == 202
    assert post_x(client, fake_x(), items=item2, text="t", sensitive=True).status_code == 202

    # explicit: 区分による制限は設けない(センシティブ指定は、人が決める)
    _insert(conn, config.paths.ready, "a3")
    approve(conn, "a3", "explicit")
    one = [{"asset_id": "a3", "version": "original"}]
    assert post_x(client, fake_x(), items=one, text="t", sensitive=True).status_code == 202
    assert post_x(client, fake_x(), items=one, text="t").status_code == 202

    # 承認済みのsfw: 指定なしで投稿できる
    _insert(conn, config.paths.ready, "a4")
    approve(conn, "a4", "sfw")
    x = fake_x()
    assert post_x(client, x, items=[{"asset_id": "a4", "version": "original"}], text="t").status_code == 202
    assert x.upload_media.call_args.kwargs["sensitive"] is False


def test_x_limits_text_weight_and_media_count(env):
    config, conn, client = env
    for i in range(5):
        _insert(conn, config.paths.ready, f"a{i}")
        approve(conn, f"a{i}", "sfw")
    items = [{"asset_id": f"a{i}", "version": "original"} for i in range(5)]
    res = post_x(client, fake_x(), items=items[:1], text="あ" * 141)
    assert res.status_code == 400 and "文字数" in res.get_json()["error"]
    res = post_x(client, fake_x(), items=items, text="t")  # 画像5枚
    assert res.status_code == 400 and "4枚" in res.get_json()["error"]
    assert post_x(client, fake_x(), items=items[:4], text="あ" * 140).status_code == 202  # 4枚・280ちょうど
    assert post_x(client, fake_x(), items=items[:1], text="t", made_with_ai=False).status_code == 202


def test_post_targets_api_and_x_status(env):
    config, conn, client = env
    _insert(conn, config.paths.ready, "a1")
    with patch("core.cli._try_create_x_client", return_value=(object(), None)), patch("core.cli._try_create_fanvue_client", return_value=(None, "未連携")):
        assert client.get("/api/post-targets").get_json() == {"fanvue": False, "x": True}
    db.set_post(conn, "a1", "x", "posted", url="u")
    assert client.get("/api/assets/a1/versions").get_json()["x_status"] == "posted"


# ------------------------------------------------------------------ 設定画面・連携
def test_settings_x_tab_fields_redirect_uri_and_status(env):
    config, conn, client = env
    page = client.get("/settings").get_data(as_text=True)
    assert 'id="sec-x"' in page and 'name="X_OAUTH_CLIENT_ID"' in page and 'name="X_OAUTH_CLIENT_SECRET"' in page
    assert 'id="x-redirect-uri">http://127.0.0.1:8420/settings/x/oauth/callback<' in page
    assert "Xと連携する" in page and "読み取りと書き込み" in page

    store = x_oauth.XTokenStore(config.paths.state_dir / "x_oauth_tokens.json")
    store.save(x_oauth.TokenSet("a", "", time.time() - 5))
    assert "Xの連携が切れています" in client.get("/settings").get_data(as_text=True)
    store.save(x_oauth.TokenSet("a", "r", time.time() + 100))
    page = client.get("/settings").get_data(as_text=True)
    assert "Xの連携が切れています" not in page and "連携済み" in page


def test_x_connect_flow_start_callback_disconnect(env, monkeypatch):
    config, conn, client = env
    monkeypatch.setenv("X_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("X_OAUTH_CLIENT_SECRET", "sec")
    monkeypatch.delenv("X_OAUTH_REDIRECT_URI", raising=False)

    res = client.get("/settings/x/oauth/start", headers={"Host": "my-pc.ts.net"})
    assert res.status_code == 302
    query = parse_qs(urlparse(res.headers["Location"]).query)
    assert query["redirect_uri"] == ["http://127.0.0.1:8420/settings/x/oauth/callback"]  # アドレスによらず固定
    state = query["state"][0]

    bad = client.get("/settings/x/oauth/callback", query_string={"code": "c", "state": "wrong"})
    assert "x_error" in bad.headers["Location"]

    client.get("/settings/x/oauth/start")  # 新しいstateで、やり直す
    state = parse_qs(urlparse(client.get("/settings/x/oauth/start").headers["Location"]).query)["state"][0]
    tokens = x_oauth.TokenSet("at", "rt", time.time() + 7200)
    with patch("posting.x_oauth.exchange_code_for_tokens", return_value=tokens) as exchange:
        res = client.get("/settings/x/oauth/callback", query_string={"code": "code1", "state": state})
    assert "x_connected=1" in res.headers["Location"]
    assert exchange.call_args.kwargs["redirect_uri"] == "http://127.0.0.1:8420/settings/x/oauth/callback"
    assert exchange.call_args.kwargs["client_secret"] == "sec"
    store = x_oauth.XTokenStore(config.paths.state_dir / "x_oauth_tokens.json")
    assert store.load().refresh_token == "rt"

    assert client.post("/settings/x/disconnect").status_code == 302
    assert store.load() is None

    monkeypatch.delenv("X_OAUTH_CLIENT_ID")
    assert "x_error" in client.get("/settings/x/oauth/start").headers["Location"]
    denied = client.get("/settings/x/oauth/callback", query_string={"error": "access_denied"})
    assert "x_error" in denied.headers["Location"]


def test_dialog_has_x_controls(env):
    config, conn, client = env
    html = client.get("/").get_data(as_text=True)
    for needle in ('id="pn-sensitive"', 'id="pn-sensitive-note"', 'id="pn-ai"', 'id="pn-xonly"', 'id="pn-fanvueonly"', 'id="pn-count"'):
        assert needle in html
    js = client.get("/static/post-now.js").get_data(as_text=True)
    assert "/api/post-targets" in js and "X_MAX_WEIGHT" in js
    assert "box.disabled" not in js and "box.checked = true" not in js  # センシティブ指定は、外せる(開いたときに、既定でオンにするだけ)


# ------------------------------------------------------------------ 一時的なサーバーの不調(503)
def test_metadata_is_retried_on_503_and_then_succeeds(tmp_path):
    """実機で、POST /2/media/metadata が503(Service Unavailable)になった。アップロード系は、やり直しても二重にならないので、再試行する。"""
    image = tmp_path / "a.png"
    image.write_bytes(b"png")
    client, session = make_client(
        _resp({"data": {"id": "5"}}),
        _resp(ok=False, status=503, text="Service Unavailable"),
        _resp(ok=False, status=503, text="Service Unavailable"),
        _resp({"data": {}}),
    )
    with patch("posting.x.time.sleep") as sleep:
        assert client.upload_media(image, "image", sensitive=True) == "5"
    assert session.request.call_count == 4  # アップロード1回+メタデータ3回(2回失敗、1回成功)
    assert [c.args[0] for c in sleep.call_args_list] == [2, 5]  # 待つ時間が、だんだん長くなる


def test_retries_give_up_with_a_friendly_hint(tmp_path):
    image = tmp_path / "a.png"
    image.write_bytes(b"png")
    client, session = make_client(_resp({"data": {"id": "5"}}), *[_resp(ok=False, status=503, text="Service Unavailable")] * 4)
    with patch("posting.x.time.sleep"):
        with pytest.raises(XApiError, match="503.*一時的に使えません"):
            client.upload_media(image, "image", sensitive=True)
    assert session.request.call_count == 1 + 1 + x_module.UPLOAD_RETRIES  # 最初+再試行3回


def test_image_file_is_rewound_when_the_upload_is_retried(tmp_path):
    image = tmp_path / "a.png"
    image.write_bytes(b"png-data")
    seen = []

    def fake_request(method, url, headers=None, files=None, **kwargs):
        if files:
            seen.append(files["media"][1].read())  # 送る側が読み出す。再試行では、先頭から読み直せる必要がある
        return _resp(ok=False, status=503, text="x") if len(seen) == 1 else _resp({"data": {"id": "9"}})

    session = MagicMock()
    session.request.side_effect = fake_request
    with patch("posting.x.time.sleep"):
        assert XClient("t", session=session).upload_media(image, "image") == "9"
    assert seen == [b"png-data", b"png-data"]


def test_create_post_is_never_retried(tmp_path):
    """投稿そのものは、成功していて応答だけ失敗した場合の二重投稿を避けるため、再試行しない。"""
    client, session = make_client(_resp(ok=False, status=503, text="Service Unavailable"))
    with patch("posting.x.time.sleep") as sleep:
        with pytest.raises(XApiError, match="503"):
            client.create_post("t", ["1"])
    assert session.request.call_count == 1 and not sleep.called
