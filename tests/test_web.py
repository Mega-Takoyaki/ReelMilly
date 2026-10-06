import io
import time
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest

from core import db
from core.config import load_config
from core.web.app import create_app


CONFIG_YAML = """
timezone: Asia/Tokyo
paths:
  library_root: data/library
  state_dir: data/state
  screenshots_dir: data/screenshots
nsfw:
  model: marqo/nsfw-image-detection-384
  threshold: 0.5
  video_frame_interval_seconds: 2
platform_content_rules:
  fanvue: [sfw, suggestive, explicit]
platform_auto_post_ratings:
  fanvue: [sfw, suggestive, explicit]
web:
  host: 127.0.0.1
  port: 8420
"""


@pytest.fixture
def app_and_conn(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    config = load_config(base_dir=tmp_path)
    config.paths.ready.mkdir(parents=True, exist_ok=True)

    conn = db.get_connection(config.paths.db_path)
    db.init_db(conn)

    now = datetime.now(timezone.utc).isoformat()
    asset_dir = config.paths.ready / "a1"
    asset_dir.mkdir(parents=True, exist_ok=True)
    image_path = asset_dir / "look-a.jpg"
    image_path.write_bytes(b"fake-image-bytes")

    db.insert_asset(
        conn,
        {
            "id": "a1",
            "status": "ready",
            "kind": "image",
            "file_path": str(image_path),
            "nsfw_auto_rating": "nsfw",
            "nsfw_auto_confidence": 0.83,
            "created_at": now,
            "updated_at": now,
        },
    )
    db.add_channel(conn, "a1", "fanvue")

    app = create_app(config)
    app.config["TESTING"] = True
    return app, conn


def test_index_lists_asset(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "a1" in body
    assert "nsfw" in body  # 自動判定バッジ
    assert 'class="asset-media"' in body  # サムネイルクリックでライトボックス表示する領域
    assert 'class="asset-detail-link"' in body  # 詳細画面への遷移は専用アイコンから
    assert 'id="lightbox"' in body
    assert 'id="thumb-size-slider"' in body


def test_index_filters_by_confirmed_status(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()
    db.update_asset(conn, "a1", content_rating="sfw", content_rating_confirmed=1)
    now = datetime.now(timezone.utc).isoformat()
    db.insert_asset(
        conn,
        {
            "id": "a2",
            "status": "ready",
            "kind": "image",
            "file_path": str(app.config["REELMILLY_CONFIG"].paths.ready / "a1" / "look-a.jpg"),
            "content_rating_confirmed": 0,
            "created_at": now,
            "updated_at": now,
        },
    )

    response_pending = client.get("/?confirmed=0")
    body_pending = response_pending.get_data(as_text=True)
    assert 'data-asset-id="a2"' in body_pending
    assert 'data-asset-id="a1"' not in body_pending

    response_done = client.get("/?confirmed=1")
    body_done = response_done.get_data(as_text=True)
    assert 'data-asset-id="a1"' in body_done
    assert 'data-asset-id="a2"' not in body_done


def test_index_shows_select_all_button_when_assets_present(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()

    response = client.get("/")

    assert 'id="select-all-toggle"' in response.get_data(as_text=True)


def test_asset_detail_shows_unconfirmed_state(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()

    response = client.get("/assets/a1")

    assert response.status_code == 200
    assert "未承認" in response.get_data(as_text=True)


def test_asset_media_serves_file_bytes(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()

    response = client.get("/assets/a1/media")

    assert response.status_code == 200
    assert response.data == b"fake-image-bytes"


def test_confirm_rating_sets_content_rating_confirmed(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()

    response = client.post("/assets/a1/confirm", data={"content_rating": "explicit"})

    assert response.status_code == 302
    asset = db.get_asset(conn, "a1")
    assert asset["content_rating"] == "explicit"
    assert asset["content_rating_confirmed"] == 1


def test_confirm_rating_promotes_pending_approval_to_ready(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()
    db.update_asset(conn, "a1", status="pending_approval")

    response = client.post("/assets/a1/confirm", data={"content_rating": "sfw"})

    assert response.status_code == 302
    asset = db.get_asset(conn, "a1")
    assert asset["status"] == "ready"


def test_confirm_rating_rejects_invalid_value(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()

    response = client.post("/assets/a1/confirm", data={"content_rating": "not-a-rating"})

    assert response.status_code == 400
    asset = db.get_asset(conn, "a1")
    assert asset["content_rating_confirmed"] == 0


def test_add_and_remove_tag(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()

    client.post("/assets/a1/tags", data={"tag_name": "推し"})
    assert db.list_tags_for_asset(conn, "a1") == ["推し"]

    client.post("/assets/a1/tags/推し/remove")
    assert db.list_tags_for_asset(conn, "a1") == []


def test_add_tag_xhr_returns_json(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()

    response = client.post(
        "/assets/a1/tags",
        data={"tag_name": "推し"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert response.status_code == 200
    assert response.get_json()["tags"] == ["推し"]


def test_remove_tag_xhr_returns_json(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()
    db.add_tag_to_asset(conn, "a1", "推し")

    response = client.post(
        "/assets/a1/tags/推し/remove",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert response.status_code == 200
    assert response.get_json()["tags"] == []


def test_add_to_folder_xhr_returns_json(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()
    folder_id = db.create_folder(conn, "2026-09")

    response = client.post(
        "/assets/a1/folders",
        data={"folder_id": folder_id},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert response.status_code == 200
    folders = response.get_json()["folders"]
    assert len(folders) == 1
    assert folders[0]["name"] == "2026-09"


def test_create_folder_and_add_asset(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()

    client.post("/folders", data={"name": "2026-09"})
    folder = db.list_folders(conn)[0]

    client.post(f"/assets/a1/folders", data={"folder_id": folder["id"]})

    folders_for_asset = db.list_folders_for_asset(conn, "a1")
    assert len(folders_for_asset) == 1
    assert folders_for_asset[0]["name"] == "2026-09"

    client.post(f"/assets/a1/folders/{folder['id']}/remove")
    assert db.list_folders_for_asset(conn, "a1") == []


def test_asset_detail_404_for_missing_asset(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()

    response = client.get("/assets/does-not-exist")

    assert response.status_code == 404


def test_upload_ingests_valid_files(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()

    response = client.post(
        "/assets/upload",
        data={
            "files": [
                (io.BytesIO(b"jpeg-bytes"), "new-photo.jpg"),
                (io.BytesIO(b"mp4-bytes"), "clip.mp4"),
            ]
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["uploaded"] == 2
    assert payload["ingested"] == 2
    assert payload["rejected"] == []

    # NSFW/生成AI未設定のためstatusは"analyzing"のまま(ADR-0015)。ここではingest自体の成功を確認する
    all_ids = {row["id"] for row in db.list_assets(conn, limit=100)}
    assert set(payload["asset_ids"]) <= all_ids


def test_upload_rejects_disallowed_extension(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()

    response = client.post(
        "/assets/upload",
        data={"files": [(io.BytesIO(b"not media"), "notes.txt")]},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["uploaded"] == 0
    assert payload["ingested"] == 0
    assert payload["rejected"] == ["notes.txt"]


def test_upload_with_no_files_returns_400(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()

    response = client.post("/assets/upload", data={}, content_type="multipart/form-data")

    assert response.status_code == 400


def test_bulk_add_tag(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()
    db.insert_asset(
        conn,
        {
            "id": "a2",
            "status": "ready",
            "kind": "image",
            "file_path": "unused.jpg",
            "created_at": "now",
            "updated_at": "now",
        },
    )

    response = client.post(
        "/assets/bulk/tag",
        json={"asset_ids": ["a1", "a2"], "tag_name": "夏"},
    )

    assert response.status_code == 200
    assert response.get_json()["updated"] == 2
    assert db.list_tags_for_asset(conn, "a1") == ["夏"]
    assert db.list_tags_for_asset(conn, "a2") == ["夏"]


def test_bulk_add_tag_requires_fields(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()

    response = client.post("/assets/bulk/tag", json={"asset_ids": [], "tag_name": "夏"})
    assert response.status_code == 400

    response = client.post("/assets/bulk/tag", json={"asset_ids": ["a1"], "tag_name": ""})
    assert response.status_code == 400


def test_bulk_add_to_folder(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()
    folder_id = db.create_folder(conn, "夏フォルダ")

    response = client.post(
        "/assets/bulk/folder",
        json={"asset_ids": ["a1"], "folder_id": folder_id},
    )

    assert response.status_code == 200
    folders = db.list_folders_for_asset(conn, "a1")
    assert len(folders) == 1
    assert folders[0]["id"] == folder_id


def test_bulk_confirm_rating(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()
    db.insert_asset(
        conn,
        {
            "id": "a2",
            "status": "pending_approval",
            "kind": "image",
            "file_path": "unused.jpg",
            "created_at": "now",
            "updated_at": "now",
        },
    )

    response = client.post(
        "/assets/bulk/confirm",
        json={"asset_ids": ["a1", "a2"], "content_rating": "sfw"},
    )

    assert response.status_code == 200
    assert response.get_json()["updated"] == 2
    for asset_id in ["a1", "a2"]:
        asset = db.get_asset(conn, asset_id)
        assert asset["content_rating"] == "sfw"
        assert asset["content_rating_confirmed"] == 1
        assert asset["status"] == "ready"  # ADR-0019: pending_approval→readyへ自動遷移


def test_bulk_confirm_rejects_invalid_rating(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()

    response = client.post(
        "/assets/bulk/confirm",
        json={"asset_ids": ["a1"], "content_rating": "not-valid"},
    )

    assert response.status_code == 400


def test_upload_avoids_filename_collision(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()

    for _ in range(2):
        response = client.post(
            "/assets/upload",
            data={"files": [(io.BytesIO(b"jpeg-bytes"), "dup.jpg")]},
            content_type="multipart/form-data",
        )
        assert response.get_json()["uploaded"] == 1

    all_assets = db.list_assets(conn, limit=100)
    dup_named = [a for a in all_assets if "dup" in a["file_path"]]
    assert len(dup_named) == 2


def test_update_caption_sets_fanvue_text(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()

    response = client.post("/assets/a1/caption", data={"fanvue_text": "新しい投稿文"})

    assert response.status_code == 302
    asset = db.get_asset(conn, "a1")
    assert asset["fanvue_text"] == "新しい投稿文"


def test_update_caption_with_blank_clears_fanvue_text(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()
    db.update_asset(conn, "a1", fanvue_text="旧文")

    client.post("/assets/a1/caption", data={"fanvue_text": "  "})

    asset = db.get_asset(conn, "a1")
    assert asset["fanvue_text"] is None


def test_adopt_caption_draft_copies_to_fanvue_text(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()
    db.update_asset(conn, "a1", fanvue_caption_draft="AI生成の下書き文")

    response = client.post("/assets/a1/caption/adopt")

    assert response.status_code == 302
    asset = db.get_asset(conn, "a1")
    assert asset["fanvue_text"] == "AI生成の下書き文"
    assert asset["fanvue_caption_draft"] is None


def test_adopt_caption_draft_missing_asset_returns_404(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()

    response = client.post("/assets/does-not-exist/caption/adopt")

    assert response.status_code == 404


def test_settings_page_get_shows_defaults(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()

    response = client.get("/settings")

    assert response.status_code == 200
    assert b"claude" in response.data.lower()


def test_settings_page_post_updates_values(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()

    response = client.post(
        "/settings",
        data={
            "generation_provider": "openai",
            "generation_model": "gpt-4o-mini",
            "caption_mode": "draft",
            "description_system_prompt": "カスタム説明",
            "caption_system_prompt": "カスタムキャプション",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/settings?saved=1")
    from core import settings as settings_module

    assert settings_module.get_generation_provider(conn) == "openai"
    assert settings_module.get_generation_model(conn) == "gpt-4o-mini"
    assert settings_module.get_caption_mode(conn) == "draft"


def test_settings_page_post_enables_auto_ingest_when_checkbox_checked(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()

    response = client.post("/settings", data={"auto_ingest": "on"})

    assert response.status_code == 302
    from core import settings as settings_module

    assert settings_module.get_auto_ingest(conn) is True


def test_settings_page_post_disables_auto_ingest_when_checkbox_unchecked(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()
    from core import settings as settings_module

    settings_module.set_auto_ingest(conn, True)

    response = client.post("/settings", data={})

    assert response.status_code == 302
    assert settings_module.get_auto_ingest(conn) is False


def test_settings_page_shows_not_connected_by_default(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()

    response = client.get("/settings")

    assert "未連携".encode() in response.data


def test_settings_page_shows_connected_when_tokens_exist(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()
    from posting.fanvue_oauth import FanvueTokenStore, TokenSet

    config = app.config["REELMILLY_CONFIG"]
    store = FanvueTokenStore(config.paths.state_dir / "fanvue_oauth_tokens.json")
    store.save(TokenSet(access_token="a", refresh_token="r", expires_at=time.time() + 3600))

    response = client.get("/settings")

    assert "連携済み".encode() in response.data


def test_fanvue_oauth_start_without_client_id_redirects_with_error(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()

    response = client.get("/settings/fanvue/oauth/start")

    assert response.status_code == 302
    assert "fanvue_error" in response.headers["Location"]


def test_fanvue_oauth_start_redirects_to_authorization_url(app_and_conn, monkeypatch):
    app, _ = app_and_conn
    client = app.test_client()
    monkeypatch.setenv("FANVUE_OAUTH_CLIENT_ID", "client-1")

    response = client.get("/settings/fanvue/oauth/start")

    assert response.status_code == 302
    location = response.headers["Location"]
    assert location.startswith("https://auth.fanvue.com/oauth2/auth?")
    assert "client_id=client-1" in location
    assert "code_challenge_method=S256" in location


def test_fanvue_oauth_callback_exchanges_code_and_saves_tokens(app_and_conn, monkeypatch):
    app, _ = app_and_conn
    client = app.test_client()
    monkeypatch.setenv("FANVUE_OAUTH_CLIENT_ID", "client-1")
    monkeypatch.setenv("FANVUE_OAUTH_CLIENT_SECRET", "secret-1")

    start_response = client.get("/settings/fanvue/oauth/start")
    query = parse_qs(urlparse(start_response.headers["Location"]).query)
    state = query["state"][0]

    from posting.fanvue_oauth import TokenSet

    fake_tokens = TokenSet(access_token="at", refresh_token="rt", expires_at=time.time() + 3600)
    with patch("posting.fanvue_oauth.exchange_code_for_tokens", return_value=fake_tokens) as mocked:
        callback_response = client.get(
            "/settings/fanvue/oauth/callback", query_string={"code": "auth-code", "state": state}
        )

    assert callback_response.status_code == 302
    assert "fanvue_connected=1" in callback_response.headers["Location"]
    mocked.assert_called_once()

    from posting.fanvue_oauth import FanvueTokenStore

    config = app.config["REELMILLY_CONFIG"]
    stored = FanvueTokenStore(config.paths.state_dir / "fanvue_oauth_tokens.json").load()
    assert stored == fake_tokens


def test_fanvue_oauth_callback_rejects_mismatched_state(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()

    response = client.get(
        "/settings/fanvue/oauth/callback", query_string={"code": "auth-code", "state": "unknown-state"}
    )

    assert response.status_code == 302
    assert "fanvue_error" in response.headers["Location"]


def test_fanvue_oauth_disconnect_clears_tokens(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()
    from posting.fanvue_oauth import FanvueTokenStore, TokenSet

    config = app.config["REELMILLY_CONFIG"]
    store = FanvueTokenStore(config.paths.state_dir / "fanvue_oauth_tokens.json")
    store.save(TokenSet(access_token="a", refresh_token="r", expires_at=time.time() + 3600))

    response = client.post("/settings/fanvue/disconnect")

    assert response.status_code == 302
    assert store.load() is None


def test_settings_page_get_shows_connection_status(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()

    response = client.get("/settings")

    assert response.status_code == 200
    assert "未設定".encode() in response.data
    assert "Fanvue".encode() in response.data


def test_settings_page_post_redirects_and_shows_saved_banner(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()

    response = client.post(
        "/settings",
        data={
            "generation_provider": "claude",
            "generation_model": "claude-opus-5",
            "caption_mode": "auto",
            "description_system_prompt": "p",
            "caption_system_prompt": "p",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert "保存しました" in response.get_data(as_text=True)


def test_settings_page_post_saves_connection_values_to_env(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()
    env_path = app.config["REELMILLY_CONFIG"].env_path

    response = client.post(
        "/settings",
        data={
            "FANVUE_OAUTH_CLIENT_SECRET": "fanvue-secret-token",
            "FANVUE_HANDLE": "my-creator",
            "generation_provider": "claude",
            "generation_model": "claude-opus-5",
            "caption_mode": "auto",
            "description_system_prompt": "説明プロンプト",
            "caption_system_prompt": "キャプションプロンプト",
        },
    )

    assert response.status_code == 302
    from core import env_settings

    status = env_settings.read_connection_status(env_path)
    assert status["FANVUE_OAUTH_CLIENT_SECRET"]["is_set"] is True
    assert status["FANVUE_HANDLE"]["value"] == "my-creator"


def test_settings_page_post_blank_token_keeps_existing_value(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()
    env_path = app.config["REELMILLY_CONFIG"].env_path

    client.post(
        "/settings",
        data={
            "FANVUE_OAUTH_CLIENT_SECRET": "first-token",
            "generation_provider": "claude",
            "generation_model": "claude-opus-5",
            "caption_mode": "auto",
            "description_system_prompt": "p",
            "caption_system_prompt": "p",
        },
    )
    client.post(
        "/settings",
        data={
            "FANVUE_OAUTH_CLIENT_SECRET": "",
            "generation_provider": "claude",
            "generation_model": "claude-opus-5",
            "caption_mode": "auto",
            "description_system_prompt": "p",
            "caption_system_prompt": "p",
        },
    )

    from core import env_settings

    status = env_settings.read_connection_status(env_path)
    assert status["FANVUE_OAUTH_CLIENT_SECRET"]["is_set"] is True


def test_refresh_models_requires_api_key(app_and_conn, monkeypatch):
    app, _conn = app_and_conn[:2] if isinstance(app_and_conn, tuple) else (app_and_conn, None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = app.test_client()
    res = client.post("/settings/models/refresh", json={"provider": "claude"})
    assert res.status_code == 400
    assert "ANTHROPIC_API_KEY" in res.get_json()["error"]


def test_refresh_models_local_caches_result(app_and_conn):
    app = app_and_conn[0] if isinstance(app_and_conn, tuple) else app_and_conn
    client = app.test_client()
    with patch("core.generation.list_available_models", return_value=["org/m1", "org/m2"]):
        res = client.post("/settings/models/refresh", json={"provider": "local"})
    assert res.get_json() == {"models": ["org/m1", "org/m2"]}
    page = client.get("/settings").get_data(as_text=True)
    assert "org/m1" in page


def test_upload_defers_analysis_and_returns_quickly(app_and_conn):
    app = app_and_conn[0]
    client = app.test_client()
    with patch("core.web.app.ingest_inbox", wraps=__import__("core.web.app", fromlist=["x"]).ingest_inbox) as spy:
        res = client.post(
            "/assets/upload",
            data={"files": (io.BytesIO(b"x"), "u.jpg")},
            content_type="multipart/form-data",
        )
    assert res.status_code == 200
    assert spy.call_args.kwargs["defer_analysis"] is True


def test_analysis_status_endpoint(app_and_conn):
    app = app_and_conn[0]
    data = app.test_client().get("/api/ai-live?ids=a1").get_json()
    assert set(data["status"]) == {
        "queued", "running", "failed", "worker_alive", "current", "upcoming", "next_schedule",
    }
    assert data["assets"]["a1"]["nsfw_auto_rating"] == "nsfw"


def test_detail_preselects_rating_from_auto_judgement(app_and_conn):
    app = app_and_conn[0]
    body = app.test_client().get("/assets/a1").get_data(as_text=True)
    assert '<option value="explicit" selected>' in body  # AI判定nsfw → explicit


def test_enqueue_ai_tasks_endpoint(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()

    res = client.post("/api/ai-tasks", json={"asset_ids": ["a1", "missing"], "kind": "both"})
    data = res.get_json()
    assert res.status_code == 200
    assert data["queued"] == 2  # 存在するa1のnsfw/describe
    assert data["status"]["queued"] == 2

    again = client.post("/api/ai-tasks", json={"asset_ids": ["a1"], "kind": "nsfw"}).get_json()
    assert again["queued"] == 0 and again["skipped"] == 1

    assert client.post("/api/ai-tasks", json={"asset_ids": ["a1"], "kind": "bogus"}).status_code == 400

    live = client.get("/api/ai-live?ids=a1").get_json()
    assert live["assets"]["a1"]["tasks"]["nsfw"]["status"] == "queued"


def test_settings_saves_schedule_and_tag_categories(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()

    client.post(
        "/settings",
        data={
            "sched_id": ["s1", "", "s3"],
            "sched_kind": ["nsfw", "nsfw", "describe"],
            "sched_enabled": ["1", "1", "0"],
            "sched_time": ["04:30", "12:00", "05:00"],
            "sched_scope": ["days", "all", "all"],
            "sched_days": ["3", "7", "7"],
            "tag_category_name": ["服装", "", "性別"],
            "tag_category_options": ["水着", "x", ""],
        },
    )

    from core import settings as settings_module

    schedules = settings_module.get_ai_schedules(conn)
    assert len(schedules) == 3  # 同じ処理を別の時刻に複数登録できる
    assert schedules[0] == {"id": "s1", "kind": "nsfw", "enabled": True, "time": "04:30", "scope": "days", "days": 3}
    assert schedules[1]["time"] == "12:00" and schedules[1]["id"]  # idは自動採番
    assert schedules[2]["enabled"] is False
    assert [c["name"] for c in settings_module.get_tag_categories(conn)] == ["服装", "性別"]
    page = client.get("/settings").get_data(as_text=True)
    assert 'value="04:30"' in page and "服装" in page


def test_help_page_and_nav_link(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()
    assert client.get("/help").status_code == 200
    assert 'href="/help" target="_blank"' in client.get("/").get_data(as_text=True)


def test_detail_has_ai_actions_and_esc_script(app_and_conn):
    app, _ = app_and_conn
    body = app.test_client().get("/assets/a1").get_data(as_text=True)
    assert 'data-ai-run="nsfw"' in body and 'data-ai-run="describe"' in body
    assert "esc-back.js" in body


def test_index_cards_use_compact_overlay_icons(app_and_conn):
    app, _ = app_and_conn
    body = app.test_client().get("/").get_data(as_text=True)
    assert "asset-meta" not in body  # 下部のバッジ領域は廃止し、サムネイル上のアイコンへ
    assert 'chip chip-type chip-image' in body and ">JPG<" in body  # 拡張子つきの種別アイコン
    assert "rating-auto-nsfw" in body  # AI判定nsfw(未承認)
    assert 'data-tip="AI自動判定: NSFW（確信度 0.83）' in body
    assert 'status-dot status-ready' in body and 'data-tip="READY（承認済み・投稿準備完了）"' in body


def test_post_flags_shown_on_card_filter_and_retry(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()
    db.set_post(conn, "a1", "fanvue", "posted", url="https://www.fanvue.com/creator")

    body = client.get("/").get_data(as_text=True)
    assert 'chip chip-post post-posted' in body and 'data-channel="fanvue"' in body
    assert "Fanvue: 投稿済み（" in body

    assert "chip-post post-" in client.get("/?post=fanvue:posted").get_data(as_text=True)
    assert 'data-channel="fanvue"' not in client.get("/?post=fanvue:failed").get_data(as_text=True)

    live = client.get("/api/ai-live?ids=a1").get_json()["assets"]["a1"]
    assert live["posts"]["fanvue"]["status"] == "posted"

    # 失敗した投稿だけ「再投稿の対象に戻す」でき、行が消えて未投稿に戻る
    assert client.post("/assets/a1/posts/fanvue/retry").status_code == 400  # posted は不可
    db.set_post(conn, "a1", "fanvue", "failed", error="boom")
    assert "再投稿の対象に戻す" in client.get("/assets/a1").get_data(as_text=True)
    assert client.post("/assets/a1/posts/fanvue/retry").status_code == 302
    assert db.get_posts(conn, ["a1"]) == {}


def test_index_layout_action_bar_dropzone_and_no_old_dropzone(app_and_conn):
    app, _ = app_and_conn
    body = app.test_client().get("/").get_data(as_text=True)
    assert 'id="upload-button"' in body and "画像アップロード" in body and "フォルダ作成" in body
    assert 'class="action-bar"' in body and body.index("action-bar") < body.index('class="filters"')  # フィルタの上
    assert 'id="dropzone" class="asset-area"' in body and 'class="asset-grid"' in body
    assert "ここに画像・動画をドラッグ&ドロップ、またはクリックして選択" not in body  # 旧ドロップ領域は廃止
    assert 'id="job-status"' in body


def test_index_filters_are_multi_select_and_free_text(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()
    db.insert_asset(conn, {
        "id": "a2", "status": "analyzing", "kind": "image", "file_path": "/x/a2.jpg",
        "content_description": "夜の街を歩く女性",
        "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
    })
    both = client.get("/?status=ready&status=analyzing").get_data(as_text=True)
    assert 'data-asset-id="a1"' in both and 'data-asset-id="a2"' in both
    only = client.get("/?status=analyzing").get_data(as_text=True)
    assert 'data-asset-id="a2"' in only and 'data-asset-id="a1"' not in only
    found = client.get("/?q=女性").get_data(as_text=True)
    assert 'data-asset-id="a2"' in found and 'data-asset-id="a1"' not in found
    assert 'type="checkbox" name="status" value="analyzing" checked' in only  # 選択状態の保持
    assert 'value="女性"' in found  # 検索語の保持
