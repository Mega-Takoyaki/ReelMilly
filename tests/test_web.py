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


def _is_checked(html: str, name: str, value: str) -> bool:
    """name・valueが一致するinputが、checkedになっているか(属性の並び順に依存しない)。"""
    import re

    pattern = rf'<input[^>]*name="{re.escape(name)}"[^>]*value="{re.escape(value)}"[^>]*>'
    return any(" checked" in m.group(0) for m in re.finditer(pattern, html))


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

    # 同じ名前を2回アップロードしても、別々の作品になる。ディスク上はID名、元の名前は記録される
    all_assets = db.list_assets(conn, limit=100)
    dup_named = [a for a in all_assets if a["original_name"] == "dup.jpg"]
    assert len(dup_named) == 2
    assert all(a["file_path"].endswith(f"{a['id']}.jpg") for a in dup_named)


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
    assert _is_checked(only, "status", "analyzing")  # 選択状態の保持
    assert 'value="女性"' in found  # 検索語の保持


def test_trash_flow_in_ui(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()
    assert 'href="/trash"' in client.get("/").get_data(as_text=True)  # 一覧の右にリンク

    assert client.post("/api/assets/trash", json={"asset_ids": ["a1"]}).get_json() == {"moved": 1}
    assert 'data-asset-id="a1"' not in client.get("/").get_data(as_text=True)  # 通常の一覧には出ない
    trash = client.get("/trash").get_data(as_text=True)
    assert 'data-asset-id="a1"' in trash and "ごみ箱に入れた日時" in trash
    assert "に入っています（通常の一覧" in client.get("/assets/a1").get_data(as_text=True)

    assert client.post("/api/assets/restore", json={"asset_ids": ["a1"]}).get_json() == {"restored": 1}
    assert 'data-asset-id="a1"' in client.get("/").get_data(as_text=True)
    assert client.post("/assets/a1/trash").status_code == 302  # 詳細画面からの移動
    assert db.count_trashed(conn) == 1
    assert client.post("/api/assets/trash", json={}).status_code == 400


def test_trashed_asset_is_excluded_from_ai_tasks(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()
    client.post("/api/assets/trash", json={"asset_ids": ["a1"]})
    res = client.post("/api/ai-tasks", json={"asset_ids": ["a1"], "kind": "nsfw"}).get_json()
    assert res["queued"] == 0


def test_no_plan_flag_via_bulk_and_detail_form(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()
    assert 'chip chip-noplan' not in client.get("/").get_data(as_text=True)

    client.post("/api/assets/post-plan", json={"asset_ids": ["a1"], "planned": False})
    assert db.get_channels(conn, "a1") == []
    page = client.get("/").get_data(as_text=True)
    assert 'chip chip-noplan' in page and "投稿予定なし（SNS投稿の対象外）" in page
    assert 'data-asset-id="a1"' in client.get("/?plan=none").get_data(as_text=True)
    assert 'data-asset-id="a1"' not in client.get("/?plan=planned").get_data(as_text=True)
    assert client.get("/api/ai-live?ids=a1").get_json()["assets"]["a1"]["no_plan"] is True

    client.post("/api/assets/post-plan", json={"asset_ids": ["a1"], "planned": True})
    assert sorted(db.get_channels(conn, "a1")) == ["fanvue", "x"]
    client.post("/assets/a1/channels", data={"channel": ["x"]})  # 詳細画面: Xだけ
    assert db.get_channels(conn, "a1") == ["x"]
    client.post("/assets/a1/channels", data={})  # すべてオフ=投稿予定なし
    assert db.get_channels(conn, "a1") == []


def test_filter_options_are_existing_values_with_counts(app_and_conn):
    app, conn = app_and_conn
    body = app.test_client().get("/").get_data(as_text=True)
    # a1はready・nsfw自動判定・区分未承認(content_rating未設定)。実在しない値は候補に出ない
    assert 'name="status" value="ready"' in body and 'name="status" value="analyzing"' not in body
    assert 'name="content_rating" value="__none__"' in body and "（未設定・未承認）" in body
    assert 'name="content_rating" value="suggestive"' not in body
    assert 'name="nsfw_auto" value="nsfw"' in body
    assert "(1)" in body  # 件数つき
    # 未設定(NULL)でも絞り込める
    assert 'data-asset-id="a1"' in app.test_client().get("/?content_rating=__none__").get_data(as_text=True)


def test_purge_api_and_trash_page_controls(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()
    assert client.post("/api/assets/purge", json={}).status_code == 400

    # 通常の作品は完全削除できない(ごみ箱に入れていないものは対象外)
    assert client.post("/api/assets/purge", json={"asset_ids": ["a1"]}).get_json()["deleted"] == 0
    assert db.get_asset(conn, "a1") is not None

    client.post("/api/assets/trash", json={"asset_ids": ["a1"]})
    page = client.get("/trash").get_data(as_text=True)
    assert 'id="trash-purge"' in page and 'id="trash-empty"' in page and "元に戻せません" in page
    assert client.post("/api/assets/purge", json={"all": True}).get_json() == {"deleted": 1, "errors": []}
    assert db.get_asset(conn, "a1") is None


def test_index_replaceable_grid_region_and_instant_filter_script(app_and_conn):
    app, _ = app_and_conn
    body = app.test_client().get("/").get_data(as_text=True)
    assert 'id="grid-region"' in body and "filters.js" in body
    assert body.index('id="grid-region"') > body.index('id="dropzone"')  # ドロップ先の内側で差し替える
    assert 'id="filter-clear"' in body and "hidden" in body.split('id="filter-clear"')[1].split(">")[0]  # 絞り込み無しなら非表示
    assert 'id="filter-form"' in body.split('id="grid-region"')[0]  # フィルタは差し替え領域の外(開いたまま)


def test_file_type_two_level_filter_in_ui(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()
    db.insert_asset(conn, {
        "id": "v1", "status": "ready", "kind": "video", "file_path": "/x/v1/clip.mp4",
        "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
    })
    body = client.get("/").get_data(as_text=True)
    # 1階層目=画像/動画(件数つき)、2階層目=実在する拡張子(件数つき)
    assert 'name="kind" value="image"' in body and 'name="kind" value="video"' in body
    assert 'name="ext" value="jpg"' in body and 'name="ext" value="mp4"' in body
    assert 'name="ext" value="png"' not in body  # 実在しない拡張子は出ない

    only_video = client.get("/?kind=video").get_data(as_text=True)
    assert 'data-asset-id="v1"' in only_video and 'data-asset-id="a1"' not in only_video
    assert _is_checked(only_video, "ext", "mp4")  # 親を選ぶと配下も選択済み表示
    only_jpg = client.get("/?ext=jpg").get_data(as_text=True)
    assert 'data-asset-id="a1"' in only_jpg and 'data-asset-id="v1"' not in only_jpg


def test_favicon_is_linked_and_served(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()
    for page in ("/", "/trash", "/settings", "/help", "/assets/a1"):
        body = client.get(page).get_data(as_text=True)
        assert 'rel="icon" type="image/svg+xml"' in body and "favicon.ico" in body, page
    assert client.get("/static/favicon.svg").status_code == 200
    assert client.get("/static/favicon.ico").data[:4] == b"\x00\x00\x01\x00"  # ICOのシグネチャ
    assert client.get("/static/apple-touch-icon.png").data[:4] == b"\x89PNG"
    assert client.get("/favicon.ico").status_code == 302  # 既定の/favicon.icoの要求も受ける


def test_tag_and_folder_mode_switches_in_ui(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()
    db.insert_asset(conn, {
        "id": "a2", "status": "ready", "kind": "image", "file_path": "/x/a2/two.jpg",
        "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
    })
    db.add_tag_to_asset(conn, "a1", "夜")
    db.add_tag_to_asset(conn, "a2", "海")

    body = client.get("/").get_data(as_text=True)
    # 既定: タグ=すべて含む、フォルダ=いずれか。切り替えのラジオが出る
    assert _is_checked(body, "tag_mode", "all")
    assert _is_checked(body, "folder_mode", "any")

    has = lambda html, aid: f'data-asset-id="{aid}"' in html
    both_and = client.get("/?tag=夜&tag=海").get_data(as_text=True)
    assert not has(both_and, "a1") and not has(both_and, "a2")  # すべて含む(AND)
    both_or = client.get("/?tag=夜&tag=海&tag_mode=any").get_data(as_text=True)
    assert has(both_or, "a1") and has(both_or, "a2")  # いずれか(OR)
    assert _is_checked(both_or, "tag_mode", "any")


def test_watermark_api_dialog_preview_and_clear(app_and_conn):
    import json

    from PIL import Image

    app, conn = app_and_conn
    client = app.test_client()
    asset = db.get_asset(conn, "a1")
    Image.new("RGB", (400, 300), (40, 40, 120)).save(asset["file_path"], format="JPEG")

    page = client.get("/").get_data(as_text=True)
    assert 'id="wm-dialog"' in page and 'id="bulk-wm"' in page and "挿入位置" in page
    assert 'id="wm-open"' in client.get("/assets/a1").get_data(as_text=True)

    # 実行前のプレビュー(保存しない)
    res = client.get("/assets/a1/watermark-preview?text=@ai_hiyo&position=r1c1&position=r3c3&opacity=16&size=3")
    assert res.status_code == 200 and res.mimetype == "image/jpeg"
    assert client.get("/assets/a1/watermark-preview?text=&position=r2c2").status_code == 400
    assert db.get_asset(conn, "a1")["wm_path"] is None

    # 非同期ジョブとして積む(文字・位置・濃さ・大きさを指定)
    bad = client.post("/api/watermark", json={"asset_ids": ["a1"], "text": "", "positions": ["r2c2"]})
    assert bad.status_code == 400
    no_pos = client.post("/api/watermark", json={"asset_ids": ["a1"], "text": "x", "positions": []})
    assert no_pos.status_code == 400  # 位置は1か所以上必要
    ok = client.post(
        "/api/watermark",
        json={"asset_ids": ["a1", "none"], "text": "@ai_hiyo", "positions": ["r4c4", "r0c0"], "opacity": 16, "size": 3},
    )
    assert ok.get_json()["queued"] == 1
    task = conn.execute("SELECT kind, params FROM ai_tasks").fetchone()
    assert task["kind"] == "watermark" and json.loads(task["params"])["text"] == "@ai_hiyo"
    assert json.loads(task["params"])["positions"] == ["r4c4", "r0c0"]  # 複数の位置を指定できる

    # 透かし入りができた後: サムネイルのチップ・API・外す操作
    out = asset["file_path"].replace("look-a.jpg", "watermarked.jpg")
    Image.new("RGB", (400, 300)).save(out, format="JPEG")
    db.update_asset(conn, "a1", wm_path=out, wm_text="@ai_hiyo", wm_position="r4c4")
    assert "chip chip-wm" in client.get("/").get_data(as_text=True)
    assert client.get("/api/ai-live?ids=a1").get_json()["assets"]["a1"]["wm"]["text"] == "@ai_hiyo"
    assert client.get("/assets/a1/media?variant=wm").status_code == 200
    assert client.post("/api/watermark/clear", json={"asset_ids": ["a1"]}).get_json() == {"cleared": 1}
    assert db.get_asset(conn, "a1")["wm_path"] is None


def test_live_script_does_not_hardcode_task_kinds(app_and_conn):
    """処理の種類を足したとき(透かし挿入など)、完了検知の入れ物が足りず更新が止まる不具合の再発防止。"""
    app, _ = app_and_conn
    js = app.test_client().get("/static/ai-live.js").get_data(as_text=True)
    assert "nsfw: [], describe: []" not in js
    assert "Object.keys(LABEL).map" in js and "watermark" in js


def test_safe_upload_filename_keeps_japanese_and_blocks_paths():
    from core.web.app import safe_upload_filename as f

    assert f("展示会ブースの笑顔の案内スタッフ.png") == "展示会ブースの笑顔の案内スタッフ.png"  # 日本語は残す
    assert f("日本語.PNG") == "日本語.png"  # 拡張子は小文字にそろえる
    assert f("photo 1.png") == "photo 1.png"
    assert f(r"C:\Users\me\pic.jpg") == "pic.jpg" and f("../../evil.png") == "evil.png"  # フォルダ部分は捨てる
    assert f('a<b>:c?.webp') == "a_b__c_.webp"  # 保存できない文字は置き換える
    assert f("   .mp4") == "upload.mp4"  # 名前が空になったら補う
    assert f("x.png.exe").endswith(".exe")  # 最後の拡張子で判定される(取り込み対象外になる)


def test_upload_accepts_japanese_file_names(app_and_conn):
    """日本語のファイル名が、ASCII以外を消す整形で拡張子ごと失われ「非対応形式」にされていた不具合の回帰テスト。"""
    app, conn = app_and_conn
    client = app.test_client()
    res = client.post(
        "/assets/upload",
        data={"files": [
            (io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"x" * 20), "展示会ブースの笑顔の案内スタッフ.png"),
            (io.BytesIO(b"x"), "メモ.txt"),
        ]},
        content_type="multipart/form-data",
    ).get_json()
    assert res["ingested"] == 1 and res["rejected"] == ["メモ.txt"]  # 画像は取り込まれ、非対応形式だけが除かれる
    asset = db.get_asset(conn, res["asset_ids"][0])
    assert asset["original_name"] == "展示会ブースの笑顔の案内スタッフ.png"  # 元の名前は記録される
    assert asset["file_path"].endswith(f"{asset['id']}.png") and asset["kind"] == "image"  # ディスク上はID名


def test_download_uses_original_name_and_search_finds_it(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()
    db.update_asset(conn, "a1", original_name="展示会ブースの笑顔.jpg")

    res = client.get("/assets/a1/download")
    assert res.status_code == 200
    disposition = res.headers["Content-Disposition"]
    assert "attachment" in disposition and "filename*=UTF-8''" in disposition  # 日本語の元の名前で保存される
    assert "%E5%B1%95%E7%A4%BA%E4%BC%9A" in disposition

    page = client.get("/assets/a1").get_data(as_text=True)
    assert "展示会ブースの笑顔.jpg" in page and "ダウンロード" in page

    # 元のファイル名でも検索できる
    assert 'data-asset-id="a1"' in client.get("/?q=展示会").get_data(as_text=True)
    assert 'data-asset-id="a1"' not in client.get("/?q=存在しない名前").get_data(as_text=True)


def test_download_watermarked_variant_name(app_and_conn, tmp_path):
    app, conn = app_and_conn
    client = app.test_client()
    wm = tmp_path / "watermarked.jpg"
    wm.write_bytes(b"x")
    db.update_asset(conn, "a1", original_name="元の名前.jpg", wm_path=str(wm), wm_text="@x", wm_position="center")
    disposition = client.get("/assets/a1/download?variant=wm").headers["Content-Disposition"]
    assert "_watermarked.jpg" in disposition


def test_broken_flag_ui_filter_and_posting_exclusion(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()
    has = lambda html: 'data-asset-id="a1"' in html

    assert client.post("/api/assets/broken", json={"asset_ids": ["a1"]}).status_code == 400
    assert client.post("/api/assets/broken", json={"asset_ids": ["a1"], "broken": True}).get_json() == {"changed": 1, "broken": True}
    assert not has(client.get("/").get_data(as_text=True))  # 既定の一覧に出ない
    shown = client.get("/?broken=show").get_data(as_text=True)
    assert has(shown) and "chip chip-broken" in shown  # 含めて表示 → 「破」のチップが付く
    assert has(client.get("/?broken=only").get_data(as_text=True))
    assert _is_checked(client.get("/?broken=only").get_data(as_text=True), "broken", "only")
    assert client.get("/api/ai-live?ids=a1").get_json()["assets"]["a1"]["broken"] is True

    detail = client.get("/assets/a1").get_data(as_text=True)
    assert "badge-broken" in detail and "破綻画像のマークを解除する" in detail  # 見出しのバッジと、操作メニューの項目
    assert client.post("/assets/a1/broken").status_code == 302  # 詳細画面から解除(トグル)
    assert has(client.get("/").get_data(as_text=True)) and db.get_asset(conn, "a1")["is_broken"] == 0


def test_filter_bar_is_grouped_and_has_chip_area(app_and_conn):
    app, _ = app_and_conn
    body = app.test_client().get("/").get_data(as_text=True)
    # 9〜10個あったプルダウンを、ファイルタイプ・タグ・フォルダ・状態/区分・投稿・破綻画像の6つにまとめる
    filters = body.split('<section class="filters">')[1].split("</section>")[0]
    assert filters.count('<details>') == 6
    for heading in ("状態・区分", "投稿", "ステータス", "区分（承認済み）", "AI判定", "承認状態", "投稿予定", "投稿状態", "破綻画像"):
        assert heading in filters
    assert 'id="filter-chips"' in filters  # 選択中の条件のチップ


def test_watermark_view_popup_and_5x5_picker_and_settings_defaults(app_and_conn):
    from PIL import Image

    app, conn = app_and_conn
    client = app.test_client()
    asset = db.get_asset(conn, "a1")
    out = asset["file_path"].replace("look-a.jpg", "watermarked.jpg")
    Image.new("RGB", (40, 30)).save(out, format="JPEG")
    db.update_asset(conn, "a1", wm_path=out, wm_text="@x", wm_position="r0c0,r4c4")

    detail = client.get("/assets/a1").get_data(as_text=True)
    assert "data-wm-view" in detail and 'target="_blank"' not in detail.split("透かし入りを見る")[0][-200:]  # 新しいタブではなくポップアップ
    assert 'id="lightbox"' in detail and "lightbox.js" in detail
    assert "（2か所）" in detail  # 複数位置の説明

    page = client.get("/").get_data(as_text=True)
    import re

    assert len(re.findall(r'<input[^>]*name="wm-position"', page)) == 26  # 5x5の25か所 + 全面に繰り返す
    assert "data-wm-all" in page and "25か所すべて選択" in page

    # 設定画面: 既定の文字は@GirlAidol。編集して保存できる
    settings_page = client.get("/settings").get_data(as_text=True)
    assert 'name="wm_text" value="@GirlAidol"' in settings_page
    client.post("/settings", data={"wm_text": "@Mine", "wm_opacity": "20", "wm_size": "4", "wm_position": ["r2c2", "tile"]})
    saved = client.get("/settings").get_data(as_text=True)
    assert 'name="wm_text" value="@Mine"' in saved and _is_checked(saved, "wm_position", "r2c2") and _is_checked(saved, "wm_position", "tile")
    assert '"text": "@Mine"' in client.get("/").get_data(as_text=True)  # ダイアログの初期値にも反映


def test_detail_page_is_organized_with_menu_and_merged_post_card(app_and_conn):
    app, conn = app_and_conn
    detail = app.test_client().get("/assets/a1").get_data(as_text=True)

    # 画像への操作は、1つの「操作」メニューにまとまる(AI処理・透かし・ダウンロード・破綻・ごみ箱)
    menu = detail.split('id="asset-menu"')[1].split("</details>")[0]
    for item in ('data-ai-run="nsfw"', 'data-ai-run="describe"', 'data-ai-run="both"', "download", "破綻画像", "ごみ箱へ移動する"):
        assert item in menu, item
    # 以前は独立したカードだった項目が、重複していない
    for old_card in ("<h2 style=\"margin-top: 0;\">AI処理</h2>", ">破綻画像（キメラ）</h2>", ">投稿先（投稿予定）</h2>", ">投稿状態</h2>", ">透かし</h2>"):
        assert old_card not in detail, old_card
    # 投稿予定と投稿状態は、「投稿」のカード1つに、投稿先ごとの行としてまとまる
    posts = detail.split('id="live-posts"')[1].split('id="no-plan-note"')[0]
    assert posts.count('class="post-row"') == 2
    for ch in ('fanvue', 'x'):
        row = posts.split('data-channel="' + ch + '"')[1].split('class="post-row"')[0]
        assert 'name="channel"' in row and '未投稿' in row and '投稿済みとして記録' in row, ch
    # 説明文は画面に出さず、ⓘのツールチップへ集約する
    assert detail.count('class="info-icon"') >= 8
    assert 'チェックした投稿先だけがSNS投稿の対象になります' in detail  # data-info(ツールチップの文)の中にある
    assert '<p class="hint">処理は別プロセス' not in detail  # 本文には出さない
    assert "post-actions.js" in detail and 'id="manual-post-dialog"' in detail


def test_manual_post_record_clear_and_effect_on_auto_posting(app_and_conn):
    from posting.jobs import run_fanvue_drop

    app, conn = app_and_conn
    client = app.test_client()
    db.update_asset(conn, "a1", content_rating="sfw", content_rating_confirmed=1)

    # 投稿日時(日本時間)とURLを指定して、手動の投稿を記録する
    res = client.post("/api/assets/a1/posts/x/manual", json={"posted_at": "2026-10-07T21:30:00+09:00", "url": "https://x.com/me/status/1"})
    assert res.status_code == 200
    post = db.get_posts(conn, ["a1"])["a1"]["x"]
    assert (post["status"], post["source"], post["url"]) == ("posted", "manual", "https://x.com/me/status/1")
    assert post["posted_at"].startswith("2026-10-07T12:30:00")  # UTCで保存される

    page = client.get("/assets/a1").get_data(as_text=True)
    assert "手動で記録" in page and "https://x.com/me/status/1" in page and "記録を取り消す" in page
    assert "手動で記録" in client.get("/").get_data(as_text=True)  # サムネイルのフラグにも出る
    assert client.get("/api/ai-live?ids=a1").get_json()["assets"]["a1"]["posts"]["x"]["source"] == "manual"

    # Fanvueを手動で記録すると、自動投稿の対象から外れる(二重投稿を防ぐ)
    from unittest.mock import MagicMock

    db.set_channels(conn, "a1", ["fanvue"])
    client.post("/api/assets/a1/posts/fanvue/manual", json={})
    skipped = run_fanvue_drop(app.config["REELMILLY_CONFIG"], conn, MagicMock(), "h", "https://f.com/{handle}")
    assert skipped.executed is False and skipped.asset_id is None
    # 記録を取り消すと、再び自動投稿の対象になる
    assert client.post("/api/assets/a1/posts/fanvue/clear").get_json() == {"cleared": True}
    assert "fanvue" not in db.get_posts(conn, ["a1"]).get("a1", {})

    # 入力の検証
    assert client.post("/api/assets/a1/posts/bogus/manual", json={}).status_code == 400
    assert client.post("/api/assets/a1/posts/x/manual", json={"url": "javascript:alert(1)"}).status_code == 400
    assert client.post("/api/assets/a1/posts/x/manual", json={"posted_at": "yesterday"}).status_code == 400
    assert client.post("/api/assets/missing/posts/x/manual", json={}).status_code == 404


def test_channels_saved_via_ajax_from_detail(app_and_conn):
    app, conn = app_and_conn
    client = app.test_client()
    res = client.post("/assets/a1/channels", data={"channel": ["x"]}, headers={"X-Requested-With": "XMLHttpRequest"})
    assert res.get_json() == {"channels": ["x"], "no_plan": False}
    assert db.get_channels(conn, "a1") == ["x"]
    res = client.post("/assets/a1/channels", data={}, headers={"X-Requested-With": "XMLHttpRequest"})
    assert res.get_json()["no_plan"] is True and db.get_channels(conn, "a1") == []


def test_drag_select_script_loaded_and_internal_drags_are_not_uploads(app_and_conn):
    app, _ = app_and_conn
    client = app.test_client()
    for page in ("/", "/trash"):
        assert "drag-select.js" in client.get(page).get_data(as_text=True)
    js = client.get("/static/drag-select.js").get_data(as_text=True)
    assert "checkbox.checked" not in js and ".asset-checkbox" in js and 'new Event("change"' in js  # 一括操作へはchangeで伝える
    upload = client.get("/static/upload.js").get_data(as_text=True)
    # ページ内のサムネイルのドラッグを、アップロードとして扱わない(以前は、サムネイルをドラッグすると重複アップロードされた)
    assert "internalDrag" in upload and 'closest(".asset-card")' in upload
