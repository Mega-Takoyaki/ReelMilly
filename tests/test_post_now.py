"""今すぐ投稿: バージョンの選択・複数作品を1つの投稿に・非同期の実行と通知。"""
from unittest.mock import MagicMock, patch

import pytest

from core import db, edits, notifications, versions
from core.web.app import create_app
from posting.jobs import run_fanvue_post_now

from tests.test_video_edit import add_video, needs_ffmpeg  # noqa: F401
from tests.test_worker import _insert, _setup  # noqa: F401


def fake_client(media_ids=("m1", "m2", "m3")):
    client = MagicMock()
    client.upload_media.side_effect = list(media_ids)
    client.wait_for_media_ready.return_value = True
    client.create_post.return_value = {"uuid": "post-1"}
    return client


@pytest.fixture
def env(tmp_path, monkeypatch):
    config, conn = _setup(tmp_path)
    app = create_app(config)
    app.config["POST_NOW_SYNC"] = True
    return config, conn, app.test_client()


def test_versions_list_original_watermark_and_done_edits(env):
    config, conn, client = env
    _insert(conn, config.base_dir if hasattr(config, "base_dir") else config.paths.ready, "a1")
    asset = db.get_asset(conn, "a1")
    assert [v["key"] for v in versions.list_versions(conn, asset)] == ["original"]
    assert versions.default_version(versions.list_versions(conn, asset)) == "original"

    wm = config.paths.ready / "wm.png"
    wm.write_bytes(b"w")
    db.update_asset(conn, "a1", wm_path=str(wm), wm_text="ロゴ")
    asset = db.get_asset(conn, "a1")
    listed = versions.list_versions(conn, asset)
    assert [v["key"] for v in listed] == ["original", "wm"] and "ロゴ" in listed[1]["label"]
    assert versions.default_version(listed) == "wm"  # 透かし入りがあれば、既定はそれ(これまでの投稿と同じ)
    path, media_type = versions.resolve(conn, asset, "wm")
    assert path == wm and media_type == "image"
    with pytest.raises(versions.VersionError):
        versions.resolve(conn, asset, "edit:99")
    with pytest.raises(versions.VersionError):
        versions.resolve(conn, asset, "bogus")


@needs_ffmpeg
def test_edited_sub_videos_can_be_chosen(env):
    config, conn, client = env
    asset = add_video(config, conn)
    edit_id = edits.enqueue_trim(conn, asset, 0, 2)
    assert [v["key"] for v in versions.list_versions(conn, asset)] == ["original"]  # 処理が終わるまでは、選べない
    edits.run_pending(conn, config, log=lambda m: None)
    keys = [v["key"] for v in versions.list_versions(conn, asset)]
    assert keys == ["original", f"edit:{edit_id}"]
    path, media_type = versions.resolve(conn, asset, f"edit:{edit_id}")
    assert media_type == "video" and path.parent.name == "edits"


def test_post_now_combines_several_assets_into_one_post(env):
    config, conn, _client = env
    for i in (1, 2, 3):
        _insert(conn, config.paths.ready, f"a{i}")
    items = [(db.get_asset(conn, f"a{i}"), config.paths.ready / f"a{i}.jpg", "image") for i in (1, 2, 3)]
    client = fake_client()

    result = run_fanvue_post_now(config, conn, client, "me", "https://f.test/{handle}", items, "まとめて投稿", "followers-and-subscribers", 500)

    assert result.ok and client.create_post.call_count == 1  # 複数の作品を、1つの投稿に
    kwargs = client.create_post.call_args.kwargs
    assert kwargs["media_uuids"] == ["m1", "m2", "m3"] and kwargs["audience"] == "followers-and-subscribers" and kwargs["price_cents"] == 500
    for i in (1, 2, 3):
        post = db.get_posts(conn, [f"a{i}"])[f"a{i}"]["fanvue"]
        assert post["status"] == "posted"
    assert db.get_asset(conn, "a1")["fanvue_text"] == "まとめて投稿"
    item = notifications.list_notifications(conn, 1)[0]
    assert item["kind"] == "post" and "3件" in item["body"] and item["level"] == "success"


def test_post_now_failure_marks_all_assets_failed_and_notifies(env):
    config, conn, _client = env
    for i in (1, 2):
        _insert(conn, config.paths.ready, f"a{i}")
    items = [(db.get_asset(conn, f"a{i}"), config.paths.ready / f"a{i}.jpg", "image") for i in (1, 2)]
    client = fake_client()
    client.create_post.side_effect = RuntimeError("boom")

    result = run_fanvue_post_now(config, conn, client, "me", "https://f.test/{handle}", items, "t", "subscribers")

    assert not result.ok and "boom" in result.error
    assert all(db.get_posts(conn, [f"a{i}"])[f"a{i}"]["fanvue"]["status"] == "failed" for i in (1, 2))
    assert notifications.list_notifications(conn, 1)[0]["level"] == "error"


def post(client, **payload):
    """Fanvueと連携済みの環境を想定して呼ぶ(連携の有無の確認は、別のテスト)。"""
    with patch("core.cli._try_create_fanvue_client", return_value=(fake_client(), None)):
        return client.post("/api/post-now", json=payload)


def test_post_now_api_validations(env):
    config, conn, client = env
    _insert(conn, config.paths.ready, "a1")
    ok_items = [{"asset_id": "a1", "version": "original"}]

    assert post(client, channel="x", items=ok_items).status_code == 400 and "審査待ち" in post(client, channel="x", items=ok_items).get_json()["error"]
    assert post(client, channel="nope", items=ok_items).status_code == 400
    assert post(client, items=[]).status_code == 400
    assert post(client, items=ok_items, audience="everyone").status_code == 400
    assert post(client, items=ok_items, price_cents=100).status_code == 400  # 3ドル未満
    assert post(client, items=ok_items, price_cents="abc").status_code == 400
    assert post(client, items=[{"asset_id": "missing"}]).status_code == 400
    assert post(client, items=ok_items * 2).status_code == 400  # 同じ作品を2回
    assert post(client, items=[{"asset_id": f"a{i}"} for i in range(11)]).status_code == 400  # 上限
    assert post(client, items=[{"asset_id": "a1", "version": "wm"}]).status_code == 400  # 透かし入りが無い
    with patch("core.cli._try_create_fanvue_client", return_value=(None, "Fanvueと未連携です")):
        res = client.post("/api/post-now", json={"items": ok_items})
    assert res.status_code == 503 and "未連携" in res.get_json()["error"]
    db.trash_assets(conn, ["a1"])
    assert post(client, items=ok_items).status_code == 400  # ごみ箱の作品は、投稿できない


def test_post_now_api_runs_the_post_with_the_chosen_versions(env):
    config, conn, client = env
    _insert(conn, config.paths.ready, "a1")
    _insert(conn, config.paths.ready, "a2")
    wm = config.paths.ready / "wm.png"
    wm.write_bytes(b"w")
    db.update_asset(conn, "a2", wm_path=str(wm), wm_text="x")
    fanvue = fake_client(("m1", "m2"))

    with patch("core.cli._try_create_fanvue_client", return_value=(fanvue, None)):
        res = client.post("/api/post-now", json=dict(
            items=[{"asset_id": "a1", "version": "original"}, {"asset_id": "a2", "version": "wm"}],
            text="二枚", audience="subscribers", price_cents=None))

    body = res.get_json()
    assert res.status_code == 202 and body["ok"] and body["count"] == 2
    uploaded = [c.args[0].name for c in fanvue.upload_media.call_args_list]
    assert uploaded == ["a1.jpg", "wm.png"]  # 作品ごとに選んだバージョンのファイルを、順に投稿
    assert [c.kwargs["media_type"] for c in fanvue.upload_media.call_args_list] == ["image", "image"]
    assert fanvue.create_post.call_args.kwargs["text"] == "二枚"


def test_versions_api_and_ui_hooks(env):
    config, conn, client = env
    _insert(conn, config.paths.ready, "a1")
    data = client.get("/api/assets/a1/versions").get_json()
    assert data["default"] == "original" and data["versions"][0]["key"] == "original" and data["audience"] == "subscribers"
    assert client.get("/api/assets/nope/versions").status_code == 404

    listing = client.get("/").get_data(as_text=True)
    assert 'id="bulk-post-now"' in listing and 'id="post-now-dialog"' in listing and "post-now.js" in listing
    detail = client.get("/assets/a1").get_data(as_text=True)
    assert 'id="post-now-open"' in detail and 'id="post-now-dialog"' in detail
    assert 'value="x" disabled' in detail  # Xは、選択肢だけ見せて、使えない
