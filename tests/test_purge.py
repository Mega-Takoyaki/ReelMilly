from core import db
from core.cli import cmd_init
from core.config import load_config
from core.purge import purge_assets

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


def _setup(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    config = load_config(base_dir=tmp_path)
    cmd_init(config)
    return config, db.get_connection(config.paths.db_path)


def _asset(config, conn, asset_id, path=None):
    if path is None:
        folder = config.paths.ready / asset_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "look.jpg"
        path.write_bytes(b"x")
        (folder / "extra.txt").write_text("派生ファイル")
    db.insert_asset(conn, {
        "id": asset_id, "status": "ready", "kind": "image", "file_path": str(path),
        "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
    })
    db.add_tag_to_asset(conn, asset_id, "夜")
    db.set_channels(conn, asset_id, ["fanvue"])
    db.set_post(conn, asset_id, "fanvue", "posted", url="u")
    return path


def test_purge_removes_files_and_all_records_of_trashed_assets(tmp_path):
    config, conn = _setup(tmp_path)
    path = _asset(config, conn, "a1")
    db.trash_assets(conn, ["a1"])

    result = purge_assets(config, conn, ["a1"])

    assert result.deleted == 1 and result.errors == []
    assert not path.parent.exists()  # 作品のフォルダごと消える
    assert db.get_asset(conn, "a1") is None
    for table in ("asset_tags", "channels", "posts"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE asset_id = 'a1'").fetchone()[0] == 0


def test_purge_never_touches_assets_not_in_trash(tmp_path):
    config, conn = _setup(tmp_path)
    path = _asset(config, conn, "a1")

    result = purge_assets(config, conn, ["a1"])

    assert result.deleted == 0
    assert path.exists() and db.get_asset(conn, "a1") is not None


def test_purge_does_not_delete_files_outside_the_library(tmp_path):
    config, conn = _setup(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    keep = outside / "mine.jpg"
    keep.write_bytes(b"x")
    _asset(config, conn, "a1", path=keep)
    db.trash_assets(conn, ["a1"])

    result = purge_assets(config, conn, ["a1"])

    assert result.deleted == 1  # 記録は消えるが、ライブラリ外のファイルには触らない
    assert keep.exists()


def test_purge_all_empties_trash_only_and_blocks_running_tasks(tmp_path):
    config, conn = _setup(tmp_path)
    _asset(config, conn, "a1")
    _asset(config, conn, "a2")
    _asset(config, conn, "live")
    db.trash_assets(conn, ["a1", "a2"])
    db.enqueue_ai_task(conn, "a2", "nsfw")
    db.claim_next_ai_task(conn)  # a2のAI処理が実行中

    result = purge_assets(config, conn, None)

    assert result.deleted == 1 and "a2" in result.errors[0]  # 実行中のa2は残す
    assert db.get_asset(conn, "a1") is None and db.get_asset(conn, "a2") is not None
    assert db.get_asset(conn, "live") is not None
