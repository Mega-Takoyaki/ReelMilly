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
