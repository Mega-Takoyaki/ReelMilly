from core import db
from core.cli import cmd_init, cmd_migrate_filenames
from core.config import load_config
from core.filenames import id_filename, normalize_asset_files
from core.ingest import ingest_inbox

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


def _legacy_asset(config, conn, asset_id, name):
    """旧バージョンで取り込まれた作品(ファイル名が元の名前のまま)。original_nameは未記録。"""
    folder = config.paths.ready / asset_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"x")
    db.insert_asset(conn, {
        "id": asset_id, "status": "ready", "kind": "image", "file_path": str(path),
        "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
    })
    return path


def test_id_filename_lowercases_extension():
    assert id_filename("20261007-ab12", "展示会.PNG") == "20261007-ab12.png"


def test_ingest_stores_id_named_file_and_records_original_name(tmp_path):
    config, conn = _setup(tmp_path)
    (config.paths.inbox / "展示会ブースの笑顔.PNG").write_bytes(b"x")

    results = ingest_inbox(config, conn, defer_analysis=True)

    asset = db.get_asset(conn, results[0].asset_id)
    assert asset["original_name"] == "展示会ブースの笑顔.PNG"
    assert asset["file_path"].endswith(f"{asset['id']}.png")  # ディスク上はID名(拡張子は小文字)
    assert (config.paths.ready / asset["id"] / f"{asset['id']}.png").exists()


def test_normalize_renames_legacy_files_and_is_idempotent(tmp_path):
    config, conn = _setup(tmp_path)
    old = _legacy_asset(config, conn, "a1", "展示会ブースの笑顔.png")

    first = normalize_asset_files(config, conn)
    assert first.renamed == 1 and first.skipped == []
    asset = db.get_asset(conn, "a1")
    assert asset["file_path"] == str(old.with_name("a1.png"))
    assert asset["original_name"] == "展示会ブースの笑顔.png"  # 元の名前を記録
    assert not old.exists() and old.with_name("a1.png").exists()

    assert normalize_asset_files(config, conn).renamed == 0  # 2回目は何もしない


def test_normalize_backfills_original_name_on_init(tmp_path):
    """original_name列が無かった頃のDBでも、起動時にいまのファイル名を元の名前として記録する。"""
    config, conn = _setup(tmp_path)
    _legacy_asset(config, conn, "a1", "legacy.png")
    conn.execute("UPDATE assets SET original_name = NULL")
    conn.commit()

    db.init_db(conn)

    assert db.get_asset(conn, "a1")["original_name"] == "legacy.png"


def test_normalize_skips_missing_conflicting_and_running(tmp_path):
    config, conn = _setup(tmp_path)
    missing = _legacy_asset(config, conn, "m1", "gone.png")
    missing.unlink()
    conflict = _legacy_asset(config, conn, "c1", "other.png")
    conflict.with_name("c1.png").write_bytes(b"already")
    busy = _legacy_asset(config, conn, "b1", "busy.png")
    db.enqueue_ai_task(conn, "b1", "nsfw")
    db.claim_next_ai_task(conn)  # AI処理の実行中

    result = normalize_asset_files(config, conn)

    assert result.renamed == 0 and len(result.skipped) == 3
    assert conflict.exists() and busy.exists()  # どれも触らない


def test_normalize_includes_trashed_assets_and_cli_command(tmp_path, capsys):
    config, conn = _setup(tmp_path)
    old = _legacy_asset(config, conn, "t1", "in-trash.png")
    db.trash_assets(conn, ["t1"])
    conn.close()

    assert cmd_migrate_filenames(config) == 0

    assert old.with_name("t1.png").exists()
    assert "1件のファイル名を作品IDに改名" in capsys.readouterr().out
