import io
from datetime import datetime, timezone

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
            "status": "ready",
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

    assert response.status_code == 200
    from core import settings as settings_module

    assert settings_module.get_generation_provider(conn) == "openai"
    assert settings_module.get_generation_model(conn) == "gpt-4o-mini"
    assert settings_module.get_caption_mode(conn) == "draft"
