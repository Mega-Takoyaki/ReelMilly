import io
from unittest.mock import MagicMock

import pytest
from PIL import Image

from core import db, duplicates, notifications
from core.cli import cmd_init, cmd_ingest
from core.config import load_config
from core.ingest import ingest_inbox
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
  fanvue: [sfw]
platform_auto_post_ratings:
  fanvue: [sfw]
web:
  host: 127.0.0.1
  port: 8420
"""


@pytest.fixture
def env(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    config = load_config(base_dir=tmp_path)
    cmd_init(config)
    conn = db.get_connection(config.paths.db_path)
    return config, conn


def png_bytes(color=(10, 20, 30), size=(40, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def upload(client, *files):
    return client.post(
        "/assets/upload",
        data={"files": [(io.BytesIO(data), name) for name, data in files]},
        content_type="multipart/form-data",
    ).get_json()


def test_ingest_records_content_hash_and_backfill_fills_old_assets(env):
    config, conn = env
    data = png_bytes()
    (config.paths.inbox / "a.png").write_bytes(data)
    asset_id = ingest_inbox(config, conn, defer_analysis=True)[0].asset_id
    import hashlib

    assert db.get_asset(conn, asset_id)["content_hash"] == hashlib.sha256(data).hexdigest()
    conn.execute("UPDATE assets SET content_hash = NULL")
    conn.commit()
    assert duplicates.backfill_hashes(conn) == 1
    assert db.get_asset(conn, asset_id)["content_hash"] == hashlib.sha256(data).hexdigest()


def test_upload_of_same_content_is_held_not_ingested(env):
    config, conn = env
    client = create_app(config).test_client()
    first = upload(client, ("one.png", png_bytes()))
    assert first["ingested"] == 1 and first["duplicates_held"] == 0

    second = upload(client, ("same-but-renamed.png", png_bytes()), ("different.png", png_bytes((200, 0, 0))))
    assert second["ingested"] == 1 and second["duplicates_held"] == 1  # 中身が同じものは取り込まず、別のものだけ取り込む
    assert len(db.list_assets(conn, limit=10)) == 2

    pending = client.get("/api/duplicates").get_json()["pending"]
    assert len(pending) == 1 and pending[0]["name"] == "same-but-renamed.png"
    assert [a["original_name"] for a in pending[0]["existing_assets"]] == ["one.png"]
    # 保留中のファイルは、プレビューできる(取り込み前のファイル)
    assert client.get(f"/api/duplicates/pending/{pending[0]['token']}/same-but-renamed.png").status_code == 200
    assert client.get(f"/api/duplicates/pending/{pending[0]['token']}/..%2Fmanifest.json").status_code == 404


def test_duplicate_within_one_upload_batch_is_held(env):
    config, conn = env
    client = create_app(config).test_client()
    res = upload(client, ("a.png", png_bytes()), ("b.png", png_bytes()))
    assert res["ingested"] == 1 and res["duplicates_held"] == 1
    pending = client.get("/api/duplicates").get_json()["pending"][0]
    assert pending["same_as"] == "a.png" and pending["existing"] == []  # 同じアップロードの中の重複


def test_duplicate_of_trashed_asset_is_also_detected(env):
    config, conn = env
    client = create_app(config).test_client()
    asset_id = db.list_assets(conn)[0]["id"] if upload(client, ("a.png", png_bytes())) else None
    db.trash_assets(conn, [asset_id])
    res = upload(client, ("a2.png", png_bytes()))
    assert res["duplicates_held"] == 1  # ごみ箱にあるものと同じでも、確認する(復旧できるため)
    assert client.get("/api/duplicates").get_json()["pending"][0]["existing_assets"][0]["deleted_at"]


def test_resolve_pending_skip_and_import_in_bulk(env):
    config, conn = env
    client = create_app(config).test_client()
    upload(client, ("orig.png", png_bytes()))
    upload(client, ("dup1.png", png_bytes()), ("dup2.png", png_bytes()))  # 既にあるものと同じ(2件)
    pending = client.get("/api/duplicates").get_json()["pending"]
    assert len(pending) == 2

    out = client.post("/api/duplicates/resolve", json={"uploads": [
        {"token": pending[0]["token"], "name": "dup1.png", "action": "skip"},
        {"token": pending[1]["token"], "name": "dup2.png", "action": "import"},
    ]}).get_json()
    assert out["uploads"] == {"imported": 1, "skipped": 1, "ingested": 1}
    assert out["remaining"] == {"pending": 0, "groups": 0}  # 「取り込む」と決めた重複は、あとで通知しない
    assert len(db.list_assets(conn, limit=10)) == 2
    assert not duplicates.staging_root(config).exists() or not any(duplicates.staging_root(config).iterdir())


def test_registered_duplicate_groups_trash_others_or_keep_all(env):
    config, conn = env
    for name in ("a.png", "b.png", "c.png"):  # インボックスからの取り込み(重複の確認なし)で、同じ中身が増えた状態
        (config.paths.inbox / name).write_bytes(png_bytes())
        ingest_inbox(config, conn, defer_analysis=True)
    (config.paths.inbox / "other1.png").write_bytes(png_bytes((1, 2, 3)))
    ingest_inbox(config, conn, defer_analysis=True)
    (config.paths.inbox / "other2.png").write_bytes(png_bytes((1, 2, 3)))
    ingest_inbox(config, conn, defer_analysis=True)

    groups = duplicates.duplicate_groups(conn)
    assert sorted(len(g["assets"]) for g in groups) == [2, 3]
    big = next(g for g in groups if len(g["assets"]) == 3)
    small = next(g for g in groups if len(g["assets"]) == 2)
    assert sum(a["keep"] for a in big["assets"]) == 1  # 残す候補は、1つだけ

    keep_id = big["assets"][1]["id"]
    out = duplicates.resolve_groups(conn, [
        {"hash": big["hash"], "action": "trash_others", "keep_id": keep_id},
        {"hash": small["hash"], "action": "keep_all"},
    ])
    assert out == {"trashed": 2, "kept_all": 1}
    assert [a["id"] for a in db.list_assets(conn, limit=10) if a["content_hash"] == big["hash"]] == [keep_id]
    assert duplicates.duplicate_groups(conn) == []  # 残す(承知の重複)と決めたものは、二度と出ない


def test_keep_candidate_prefers_posted_asset(env):
    config, conn = env
    ids = []
    for name in ("a.png", "b.png"):
        (config.paths.inbox / name).write_bytes(png_bytes())
        ids.append(ingest_inbox(config, conn, defer_analysis=True)[0].asset_id)
    db.set_post(conn, ids[1], "fanvue", "posted", url="u")  # 新しいほうが、投稿済み
    group = duplicates.duplicate_groups(conn)[0]
    assert next(a for a in group["assets"] if a["keep"])["id"] == ids[1]
    assert next(a for a in group["assets"] if a["id"] == ids[1])["posted_to"] == ["fanvue"]


def test_notify_new_duplicates_only_once_per_group(env):
    config, conn = env
    for name in ("a.png", "b.png"):
        (config.paths.inbox / name).write_bytes(png_bytes())
        ingest_inbox(config, conn, defer_analysis=True)
    assert duplicates.notify_new_duplicates(conn) == 1
    assert duplicates.notify_new_duplicates(conn) == 0  # 同じ重複を、繰り返し通知しない
    item = notifications.list_notifications(conn, 5)[0]
    assert item["kind"] == "duplicates" and item["action"] == "duplicates" and "1組" in item["title"]

    (config.paths.inbox / "c.png").write_bytes(png_bytes((9, 9, 9)))
    ingest_inbox(config, conn, defer_analysis=True)
    (config.paths.inbox / "d.png").write_bytes(png_bytes((9, 9, 9)))
    ingest_inbox(config, conn, defer_analysis=True)
    assert duplicates.notify_new_duplicates(conn) == 2  # 新しい重複が増えたら、また通知する


def test_cli_ingest_notifies_when_duplicates_appear(env):
    config, conn = env
    (config.paths.inbox / "a.png").write_bytes(png_bytes())
    (config.paths.inbox / "b.png").write_bytes(png_bytes())
    conn.close()
    assert cmd_ingest(config) == 0
    conn = db.get_connection(config.paths.db_path)
    assert any(n["kind"] == "duplicates" for n in notifications.list_notifications(conn, 10))


def test_stale_pending_uploads_are_cleaned_up(env):
    import os
    import time

    config, conn = env
    client = create_app(config).test_client()
    upload(client, ("a.png", png_bytes()))
    upload(client, ("b.png", png_bytes()))
    token_dir = next(duplicates.staging_root(config).iterdir())
    old = time.time() - duplicates.STALE_SECONDS - 60
    os.utime(token_dir, (old, old))
    assert duplicates.cleanup_stale(config) == 1 and not token_dir.exists()


def test_dialog_is_available_on_every_page_and_upload_opens_it(env):
    config, conn = env
    client = create_app(config).test_client()
    for page in ("/", "/trash", "/settings"):
        body = client.get(page).get_data(as_text=True)
        assert 'id="dup-dialog"' in body and "duplicates.js" in body, page
    assert "openDuplicates" in client.get("/static/upload.js").get_data(as_text=True)
    assert "openDuplicates" in client.get("/static/notifications.js").get_data(as_text=True)  # 通知の「確認する」から開く
