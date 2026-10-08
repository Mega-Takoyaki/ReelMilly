"""動画の編集(切り出し)。ffmpegが無い環境では、実際に切り出すテストだけ飛ばす。"""
import subprocess
from unittest.mock import patch

import pytest

from core import db, edits, ffmpeg, notifications, worker
from core.web.app import create_app

from tests.test_worker import _setup  # noqa: F401

needs_ffmpeg = pytest.mark.skipif(not ffmpeg.available(), reason="ffmpegが無い")


def make_video(path, seconds=4):
    """音声つきの、短いテスト動画を作る。"""
    subprocess.run(
        [ffmpeg.find("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=160x120:rate=15",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(path)],
        check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def add_video(config, conn, asset_id="20260101-aaaaaaaa", seconds=4):
    folder = config.paths.ready / asset_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{asset_id}.mp4"
    make_video(path, seconds)
    db.insert_asset(conn, {
        "id": asset_id, "status": "ready", "kind": "video", "file_path": str(path), "original_name": "clip.mp4",
        "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
    })
    return db.get_asset(conn, asset_id)


def run_worker(config, conn):
    """編集用スレッドが数秒おきに行う処理を、1回分実行する。"""
    return edits.run_pending(conn, config, log=lambda m: None)


def test_summary_text():
    assert edits.summarize("trim", {"start": 2.0, "end": 5.0}) == "トリム 0:02.0～0:05.0"
    assert edits.summarize("trim", {"start": 61.5, "end": 75.0, "mode": "fast"}) == "トリム 1:01.5～1:15.0（高速）"


@needs_ffmpeg
def test_trim_accurate_creates_a_sub_video_and_notifies(tmp_path):
    config, conn = _setup(tmp_path)
    asset = add_video(config, conn)
    original = asset["file_path"]
    edit_id = edits.enqueue_trim(conn, asset, 1, 3)
    assert edits.get_edit(conn, asset["id"], edit_id)["status"] == "queued"

    run_worker(config, conn)

    edit = edits.get_edit(conn, asset["id"], edit_id)
    assert edit["status"] == "done" and edit["summary"] == "トリム 0:01.0～0:03.0"
    path = edits.edit_path(asset, edit)
    assert path.exists() and path.parent.name == "edits" and path.suffix == ".mp4"
    assert ffmpeg.probe_duration(path) == pytest.approx(2.0, abs=0.25)  # 指定した長さ(2秒)で切れている
    assert edit["size_bytes"] > 0 and edit["duration"] == pytest.approx(2.0, abs=0.25)
    assert db.get_asset(conn, asset["id"])["file_path"] == original  # 元の動画は変わらない
    assert ffmpeg.probe_duration(__import__("pathlib").Path(original)) == pytest.approx(4.0, abs=0.3)
    item = notifications.list_notifications(conn, 3)[0]
    assert item["kind"] == "edit" and item["level"] == "success" and "トリム" in item["body"] and item["asset_id"] == asset["id"]


@needs_ffmpeg
def test_trim_fast_copies_without_reencoding(tmp_path):
    config, conn = _setup(tmp_path)
    asset = add_video(config, conn)
    edit_id = edits.enqueue_trim(conn, asset, 0, 2, mode="fast")
    run_worker(config, conn)
    edit = edits.get_edit(conn, asset["id"], edit_id)
    assert edit["status"] == "done" and "（高速）" in edit["summary"]
    assert ffmpeg.probe_duration(edits.edit_path(asset, edit)) > 0.5


@needs_ffmpeg
def test_invalid_ranges_and_limit_are_rejected(tmp_path):
    config, conn = _setup(tmp_path)
    asset = add_video(config, conn, seconds=3)
    for start, end in [(2, 1), (1, 1), (-1, 2), (0, 0.05), ("x", 2), (10, 12)]:
        with pytest.raises(edits.EditError):
            edits.enqueue_trim(conn, asset, start, end)
    assert edits.active_count(conn, asset["id"]) == 0
    edits.enqueue_trim(conn, asset, 0, 99)  # 動画の長さを超える終了は、最後までとして扱う
    assert edits.list_edits(conn, asset["id"])[0]["params"]["end"] == pytest.approx(3.0, abs=0.1)
    for i in range(edits.MAX_EDITS_PER_ASSET - 1):
        edits.enqueue_trim(conn, asset, 0, 1 + i * 0.1)
    with pytest.raises(edits.EditError, match="件まで"):
        edits.enqueue_trim(conn, asset, 0, 1)


def test_images_cannot_be_edited(tmp_path):
    config, conn = _setup(tmp_path)
    path = tmp_path / "a.jpg"
    path.write_bytes(b"x")
    db.insert_asset(conn, {"id": "img1", "status": "ready", "kind": "image", "file_path": str(path),
                           "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00"})
    with pytest.raises(edits.EditError, match="動画だけ"):
        edits.enqueue_trim(conn, db.get_asset(conn, "img1"), 0, 1)


@needs_ffmpeg
def test_failed_edit_is_reported_and_not_counted(tmp_path):
    config, conn = _setup(tmp_path)
    asset = add_video(config, conn)
    edit_id = edits.enqueue_trim(conn, asset, 0, 2)
    with patch("core.ffmpeg.trim", side_effect=ffmpeg.FfmpegError("ffmpegが失敗しました: テスト")):
        run_worker(config, conn)
    edit = edits.get_edit(conn, asset["id"], edit_id)
    assert edit["status"] == "failed" and "テスト" in edit["error"]
    assert edits.active_count(conn, asset["id"]) == 0  # 失敗は、上限に数えない
    item = notifications.list_notifications(conn, 3)[0]
    assert item["kind"] == "edit" and item["level"] == "error"
    edits.enqueue_trim(conn, asset, 0, 1)  # 次の登録で、失敗の記録は片付く
    assert [e["status"] for e in edits.list_edits(conn, asset["id"])] == ["queued"]


@needs_ffmpeg
def test_edits_do_not_wait_for_the_ai_worker(tmp_path):
    """AI処理(数分かかる)の最中でも、編集は別に処理される。AI処理のワーカーは、編集に関与しない。"""
    config, conn = _setup(tmp_path)
    asset = add_video(config, conn)
    edit_id = edits.enqueue_trim(conn, asset, 0, 1)
    db.enqueue_ai_task(conn, asset["id"], "nsfw")
    assert edits.run_pending(conn, config, log=lambda m: None) == 1  # AI処理のキューがあっても、編集はすぐ済む
    assert edits.get_edit(conn, asset["id"], edit_id)["status"] == "done"
    assert db.ai_task_counts(conn)["queued"] == 1  # AI処理には触れない


def test_claim_is_exclusive(tmp_path):
    config, conn = _setup(tmp_path)
    conn.execute("INSERT INTO assets (id, status, kind, file_path, created_at, updated_at) VALUES ('v1','ready','video','x.mp4','t','t')")
    conn.execute("INSERT INTO asset_edits (asset_id, kind, params, summary, status, created_at) VALUES ('v1','trim','{}','s','queued','t')")
    conn.commit()
    other = db.get_connection(config.paths.db_path)
    first = edits.claim_next(conn)
    assert first is not None and edits.claim_next(other) is None  # 同じ1件を、2つのプロセスが処理しない
    other.close()


def test_edits_wait_while_storage_is_unavailable(tmp_path):
    config, conn = _setup(tmp_path)
    conn.execute("INSERT INTO assets (id, status, kind, file_path, created_at, updated_at) VALUES ('v1','ready','video','x.mp4','t','t')")
    conn.execute("INSERT INTO asset_edits (asset_id, kind, params, summary, status, created_at) VALUES ('v1','trim','{}','s','queued','t')")
    conn.commit()
    with patch("core.storage.check") as check:
        check.return_value.available = False
        assert edits.run_pending(conn, config, log=lambda m: None) == 0
    assert conn.execute("SELECT status FROM asset_edits").fetchone()[0] == "queued"  # 失敗にせず、つながるまで待つ


@needs_ffmpeg
def test_web_api_create_list_download_delete_and_purge(tmp_path):
    config, conn = _setup(tmp_path)
    asset = add_video(config, conn)
    client = create_app(config).test_client()

    assert "動画の編集" in client.get(f"/assets/{asset['id']}").get_data(as_text=True)
    bad = client.post(f"/api/assets/{asset['id']}/edits", json={"kind": "trim", "start": 3, "end": 1})
    assert bad.status_code == 400 and "終了" in bad.get_json()["error"]
    created = client.post(f"/api/assets/{asset['id']}/edits", json={"kind": "trim", "start": 0.5, "end": 2.5})
    assert created.status_code == 201
    listing = client.get(f"/api/assets/{asset['id']}/edits").get_json()
    assert listing["used"] == 1 and listing["limit"] == 10 and listing["edits"][0]["status"] == "queued"

    run_worker(config, conn)
    item = client.get(f"/api/assets/{asset['id']}/edits").get_json()["edits"][0]
    assert item["status"] == "done" and item["available"] and item["summary"] == "トリム 0:00.5～0:02.5"
    with client.get(item["url"]) as res:
        assert res.status_code == 200
    with client.get(item["download_url"]) as dl:
        assert dl.status_code == 200 and "clip_trim_0.5-2.5s.mp4" in dl.headers["Content-Disposition"]

    path = edits.edit_path(asset, edits.get_edit(conn, asset["id"], item["id"]))
    assert client.delete(f"/api/assets/{asset['id']}/edits/{item['id']}").status_code == 200
    assert not path.exists()
    with client.get(item["url"]) as gone:
        assert gone.status_code == 404

    # ごみ箱→完全削除で、編集動画のファイルも消える
    client.post(f"/api/assets/{asset['id']}/edits", json={"start": 0, "end": 1})
    run_worker(config, conn)
    kept = edits.edit_path(asset, edits.list_edits(conn, asset["id"])[0])
    assert kept.exists()
    db.trash_assets(conn, [asset["id"]])
    assert client.post("/api/assets/purge", json={"asset_ids": [asset["id"]]}).get_json()["deleted"] == 1
    assert not kept.exists() and not (config.paths.ready / asset["id"]).exists()


def test_edit_section_only_for_videos_and_missing_ffmpeg_is_reported(tmp_path):
    config, conn = _setup(tmp_path)
    path = tmp_path / "a.jpg"
    path.write_bytes(b"x")
    db.insert_asset(conn, {"id": "img1", "status": "ready", "kind": "image", "file_path": str(path),
                           "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00"})
    client = create_app(config).test_client()
    assert "動画の編集" not in client.get("/assets/img1").get_data(as_text=True)
    with patch("core.ffmpeg.available", return_value=False):
        res = client.post("/api/assets/img1/edits", json={"start": 0, "end": 1})
    assert res.status_code == 503 and "ffmpeg" in res.get_json()["error"]


def test_requeue_running_edits_after_worker_stops(tmp_path):
    config, conn = _setup(tmp_path)
    conn.execute(
        "INSERT INTO assets (id, status, kind, file_path, created_at, updated_at) VALUES ('v1','ready','video','x.mp4','t','t')"
    )
    conn.execute("INSERT INTO asset_edits (asset_id, kind, params, summary, status, created_at) VALUES ('v1','trim','{}','s','running','t')")
    conn.commit()
    edits.requeue_running(conn)
    assert edits.pending_count(conn) == 1
    assert conn.execute("SELECT status FROM asset_edits").fetchone()[0] == "queued"
