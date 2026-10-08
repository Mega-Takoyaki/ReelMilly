import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core import db, storage
from core.cli import cmd_init, cmd_ingest
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
    return config, conn, tmp_path


def _asset(config, conn, asset_id="a1", data=b"x" * 100):
    folder = config.paths.ready / asset_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{asset_id}.png"
    path.write_bytes(data)
    db.insert_asset(conn, {
        "id": asset_id, "status": "ready", "kind": "image", "file_path": str(path),
        "content_rating": "sfw", "content_rating_confirmed": 1,
        "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
    })
    db.set_channels(conn, asset_id, ["fanvue"])
    return path


def test_default_library_is_available_and_gets_a_marker(env):
    config, conn, _ = env
    status = storage.check(config, conn)
    assert status.available and status.code == "ok" and status.is_default
    assert (config.paths.root / storage.MARKER).read_text(encoding="utf-8") == storage.library_id(conn)


def test_missing_storage_is_reported_without_crashing(env, tmp_path):
    config, conn, _ = env
    db.set_setting(conn, storage.SETTING_ROOT, str(tmp_path / "removable" / "lib"))
    storage.apply_override(config, conn)  # 設定された場所に切り替わる(まだ存在しない)

    status = storage.check(config, conn)
    assert not status.available and status.code == "missing"
    assert "リムーバブルメディア" in status.reason
    # 起動時のフォルダ作成も、リムーバブルの場所には何も作らない(別ドライブへの空フォルダ作成を避ける)
    from core.config import ensure_directories

    ensure_directories(config)
    assert not (tmp_path / "removable").exists()


def test_wrong_library_in_same_place_is_not_used(env, tmp_path):
    config, conn, _ = env
    other = tmp_path / "usb"
    other.mkdir()
    (other / storage.MARKER).write_text("someone-elses-library", encoding="utf-8")
    db.set_setting(conn, storage.SETTING_ROOT, str(other))
    storage.apply_override(config, conn)
    status = storage.check(config, conn)
    assert not status.available and status.code == "wrong"  # ドライブ文字が同じでも、別のライブラリは使わない


def test_empty_folder_without_marker_is_not_used_when_assets_exist(env, tmp_path):
    config, conn, _ = env
    _asset(config, conn)
    empty = tmp_path / "empty_usb"
    empty.mkdir()
    db.set_setting(conn, storage.SETTING_ROOT, str(empty))
    storage.apply_override(config, conn)
    status = storage.check(config, conn)
    assert not status.available and status.code == "empty"  # 作品のファイルが無い場所は、ライブラリとはみなさない


def test_move_copies_files_rewrites_paths_and_keeps_source_by_default(env, tmp_path):
    config, conn, _ = env
    old = _asset(config, conn, "a1", b"abc" * 50)
    (config.paths.inbox / "pending.png").write_bytes(b"inbox-file")
    target = tmp_path / "newdrive" / "Reelmilly"

    result = storage.change_root(config, conn, str(target), "move")

    new_path = target / "ready" / "a1" / "a1.png"
    assert new_path.read_bytes() == b"abc" * 50 and (target / "inbox" / "pending.png").exists()
    assert db.get_asset(conn, "a1")["file_path"] == str(new_path)  # DBのパスが新しい場所へ
    assert config.paths.root == target and result["assets_updated"] == 1
    assert old.exists()  # 既定では、元のファイルは残す
    assert storage.check(config, conn).available and (target / storage.MARKER).exists()
    assert db.get_setting(conn, storage.SETTING_ROOT) == str(target)  # 次回以降も、この場所を使う
    assert storage.current_job(conn)["state"] == "done"


def test_move_can_delete_source_after_verified_copy(env, tmp_path):
    config, conn, _ = env
    old = _asset(config, conn)
    target = tmp_path / "moved"
    result = storage.change_root(config, conn, str(target), "move", delete_source=True)
    assert result["removed_source_files"] == 1 and not old.exists()
    assert (target / "ready" / "a1" / "a1.png").exists()


def test_move_rejects_nested_or_same_or_relative_destinations(env, tmp_path):
    config, conn, _ = env
    root = config.paths.root
    for bad in [str(root), str(root / "inside"), str(root.parent), "relative/path", "", "Z:\\no\\such\\drive"]:
        with pytest.raises(storage.StorageError):
            storage.change_root(config, conn, bad, "move")
    assert config.paths.root == root  # 失敗しても、いまの場所のまま


def test_move_fails_cleanly_when_current_storage_is_missing(env, tmp_path):
    config, conn, _ = env
    db.set_setting(conn, storage.SETTING_ROOT, str(tmp_path / "gone"))
    storage.apply_override(config, conn)
    with pytest.raises(storage.StorageError) as exc:
        storage.change_root(config, conn, str(tmp_path / "dest"), "move")
    assert "この場所を使う" in str(exc.value)  # 次にすべきことを案内する


def test_use_existing_location_after_drive_letter_change(env, tmp_path):
    """ドライブ文字が変わった(=ファイルは新しい場所にある)ときに、コピーせずに指し直せる。"""
    config, conn, _ = env
    _asset(config, conn, "a1", b"data")
    moved = tmp_path / "usb_new_letter"
    moved.mkdir()
    (moved / "ready").mkdir()
    import shutil

    shutil.copytree(config.paths.ready / "a1", moved / "ready" / "a1")  # 別のドライブ文字に、ファイルがある状態
    shutil.rmtree(config.paths.ready / "a1")

    assert storage.check(config, conn).available  # 既定の場所にはファイルが無いが、既定の場所は常に使える扱い
    result = storage.change_root(config, conn, str(moved), "use")

    assert result["assets_updated"] == 1
    assert Path(db.get_asset(conn, "a1")["file_path"]).read_bytes() == b"data"
    assert storage.check(config, conn).available
    assert storage.usage_summary(conn, moved) == {"assets": 1, "found": 1, "bytes": 4}


def test_rewrite_leaves_assets_outside_library_layout_alone(env, tmp_path):
    config, conn, _ = env
    external = tmp_path / "elsewhere" / "pic.png"
    external.parent.mkdir()
    external.write_bytes(b"x")
    db.insert_asset(conn, {
        "id": "ext", "status": "ready", "kind": "image", "file_path": str(external),
        "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
    })
    assert storage.rewrite_paths(conn, tmp_path / "new") == 0
    assert db.get_asset(conn, "ext")["file_path"] == str(external)


def test_browse_lists_only_folders_and_validates(env, tmp_path):
    (tmp_path / "A").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "file.txt").write_text("x")
    data = storage.browse(str(tmp_path))
    assert data["folders"][:2] == ["A", "b"] and ".hidden" not in data["folders"] and "file.txt" not in data["folders"]
    with pytest.raises(storage.StorageError):
        storage.browse("relative")
    with pytest.raises(storage.StorageError):
        storage.browse(str(tmp_path / "nope"))
    assert isinstance(storage.list_drives(), list) and storage.list_drives()


# ---------------------------------------------------------------- 画面・ワーカー・投稿の挙動

def test_app_starts_without_storage_and_shows_banner_and_placeholder(env, tmp_path):
    config, conn, _ = env
    _asset(config, conn)
    # リムーバブルメディアへ移したあとに、外れた状態: 保存先もDBのパスも、その場所を指しているが、中身は見えない
    storage.rewrite_paths(conn, tmp_path / "unplugged")
    db.set_setting(conn, storage.SETTING_ROOT, str(tmp_path / "unplugged"))
    app = create_app(config)
    client = app.test_client()

    page = client.get("/")
    body = page.get_data(as_text=True)
    assert page.status_code == 200 and "ストレージを使えません" in body  # 画像なしで起動し、警告を出す
    assert 'data-asset-id="a1"' in body  # 作品の一覧(DB)は見える
    media = client.get("/assets/a1/media")
    assert media.status_code == 200 and media.mimetype == "image/svg+xml"  # 画像は、見つからない旨の絵
    assert client.get("/settings").status_code == 200  # 設定画面で直せる
    # ファイルを触る操作は、理由つきで断る
    up = client.post("/assets/upload", data={"files": (io.BytesIO(b"x"), "a.png")}, content_type="multipart/form-data")
    assert up.status_code == 503 and "ストレージ" in up.get_json()["error"]
    assert client.post("/api/assets/purge", json={"all": True}).status_code == 503

    status = client.get("/api/storage/status").get_json()
    assert status["available"] is False and status["code"] == "missing"


def test_worker_pauses_and_keeps_tasks_when_storage_is_missing(env, tmp_path):
    from core.worker import AnalysisWorker

    config, conn, _ = env
    _asset(config, conn)
    db.enqueue_ai_task(conn, "a1", "nsfw")
    db.set_setting(conn, storage.SETTING_ROOT, str(tmp_path / "unplugged"))

    result = AnalysisWorker(config).run_once(conn, log=lambda m: None)

    assert result == (0, 0)
    assert db.ai_task_counts(conn)["queued"] == 1  # 失敗にせず、待機中のまま残す
    # つなぎ直す(場所が戻る)と、再開できる
    db.delete_setting(conn, storage.SETTING_ROOT)
    classifier = MagicMock()
    classifier.classify.return_value = MagicMock(rating="sfw", confidence=0.1)
    from unittest.mock import patch

    with patch("core.worker.try_create_classifier", return_value=classifier):
        assert AnalysisWorker(config).run_once(conn, log=lambda m: None) == (1, 0)


def test_drop_skips_without_recording_failure_when_file_is_missing(env):
    from posting.jobs import run_fanvue_drop

    config, conn, _ = env
    path = _asset(config, conn)
    db.update_asset(conn, "a1", fanvue_text="テストの投稿文")  # 投稿文が無い作品は、投稿を見送るため
    path.unlink()  # ストレージが外れている状態と同じ(ファイルが見えない)
    client = MagicMock()

    result = run_fanvue_drop(config, conn, client, "h", "https://f.com/{handle}")

    assert result.executed is False and "ファイルが見つかりません" in result.skipped_reason
    assert db.get_posts(conn, ["a1"]) == {}  # 失敗の記録を残さない(つながれば、次回の投稿対象のまま)
    client.upload_media.assert_not_called()


def test_cli_ingest_refuses_when_storage_missing(env, tmp_path, capsys):
    config, conn, _ = env
    db.set_setting(conn, storage.SETTING_ROOT, str(tmp_path / "unplugged"))
    conn.close()
    assert cmd_ingest(config) == 1
    assert "ストレージ" in capsys.readouterr().out


def test_storage_settings_tab_and_api(env):
    config, conn, tmp_path = env
    client = create_app(config).test_client()
    page = client.get("/settings").get_data(as_text=True)
    assert 'id="sec-storage"' in page and "コピーして移す" in page and "すでにファイルがある場所を使う" in page
    assert client.get("/api/storage/drives").get_json()["drives"]
    assert client.get(f"/api/storage/browse?path={tmp_path}").status_code == 200
    assert client.get("/api/storage/browse?path=relative").status_code == 400
    assert client.post("/api/storage/change", json={"path": "relative", "mode": "move"}).status_code == 400
    status = client.get("/api/storage/status?usage=1").get_json()
    assert status["available"] and status["default_root"] and status["usage"]["assets"] == 0
