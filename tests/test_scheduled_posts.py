"""予約投稿(Fanvue・X): 予約・取り消し・時刻が来たときの実行・失敗の扱い・画面。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from core import db, notifications
from core.web.app import create_app
from posting import scheduled

from tests.test_worker import _insert, _setup  # noqa: F401


def future(minutes=60):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def fake_fanvue():
    c = MagicMock()
    c.upload_media.side_effect = ["m1", "m2", "m3"]
    c.wait_for_media_ready.return_value = True
    c.create_post.return_value = {"uuid": "p1"}
    return c


def fake_x():
    c = MagicMock()
    c.upload_media.side_effect = ["x1", "x2"]
    c.create_post.return_value = {"id": "900"}
    return c


@pytest.fixture
def env(tmp_path):
    config, conn = _setup(tmp_path)
    for i in (1, 2):
        _insert(conn, config.paths.ready, f"a{i}")
        db.update_asset(conn, f"a{i}", content_rating="sfw", content_rating_confirmed=1)
    return config, conn, create_app(config).test_client()


def payload(channel="fanvue", ids=("a1",), **extra):
    return {"channel": channel, "items": [{"asset_id": i, "version": "original"} for i in ids], "text": "予約の本文", **extra}


def schedule(client, body, minutes=60, fanvue=None, x=None):
    with patch("core.cli._try_create_fanvue_client", return_value=(fanvue or fake_fanvue(), None)), patch(
        "core.cli._try_create_x_client", return_value=(x or fake_x(), None)
    ):
        return client.post("/api/scheduled-posts", json={**body, "run_at": future(minutes)})


# ------------------------------------------------------------------ 予約の作成
def test_schedule_and_list_with_local_time_and_details(env):
    config, conn, client = env
    res = schedule(client, payload(ids=("a1", "a2"), audience="followers-and-subscribers", price_cents=500), minutes=120)
    assert res.status_code == 201
    posts = client.get("/api/scheduled-posts").get_json()["posts"]
    assert len(posts) == 1
    p = posts[0]
    assert p["status"] == "scheduled" and p["summary"] == "Fanvue 2件" and "フォロワー" in p["detail"] and "5.00ドル" in p["detail"]
    assert p["assets"] and p["text"] == "予約の本文" and "(" in p["run_at_label"]


def test_schedule_validations(env):
    config, conn, client = env
    body = payload()
    with patch("core.cli._try_create_fanvue_client", return_value=(fake_fanvue(), None)):
        assert client.post("/api/scheduled-posts", json={**body}).status_code == 400  # 日時なし
        assert client.post("/api/scheduled-posts", json={**body, "run_at": "あした"}).status_code == 400
        assert client.post("/api/scheduled-posts", json={**body, "run_at": "2026-10-10T10:00:00"}).status_code == 400  # タイムゾーンなし
        past = client.post("/api/scheduled-posts", json={**body, "run_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()})
        assert past.status_code == 400 and "これから先" in past.get_json()["error"]
        bad_item = payload(ids=("nope",))
        assert client.post("/api/scheduled-posts", json={**bad_item, "run_at": future()}).status_code == 400  # いま投稿できない依頼は、予約させない
    with patch("core.cli._try_create_x_client", return_value=(None, "Xと未連携です")):
        res = client.post("/api/scheduled-posts", json={**payload("x"), "run_at": future()})
    assert res.status_code == 503 and "未連携" in res.get_json()["error"]
    assert client.get("/api/scheduled-posts").get_json()["posts"] == []


# ------------------------------------------------------------------ 実行
def test_run_due_posts_only_what_is_due_and_in_time_order(env):
    config, conn, client = env
    fanvue, x = fake_fanvue(), fake_x()
    schedule(client, payload("fanvue", ("a1",)), minutes=60, fanvue=fanvue, x=x)   # まだ
    schedule(client, payload("x", ("a2",)), minutes=30, fanvue=fanvue, x=x)         # まだ
    assert scheduled.run_due(conn, config, log=lambda m: None) == 0  # 時刻が来るまで、何もしない

    conn.execute("UPDATE scheduled_posts SET run_at = ?", ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),))
    conn.execute("UPDATE scheduled_posts SET run_at = ? WHERE channel = 'x'", ((datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),))
    conn.commit()
    with patch("core.cli._try_create_fanvue_client", return_value=(fanvue, None)), patch("core.cli._try_create_x_client", return_value=(x, None)):
        assert scheduled.run_due(conn, config, log=lambda m: None) == 2
    assert [p["status"] for p in scheduled.list_posts(conn)] == ["done", "done"]
    assert db.get_posts(conn, ["a1"])["a1"]["fanvue"]["status"] == "posted"
    assert db.get_posts(conn, ["a2"])["a2"]["x"]["status"] == "posted"
    assert x.create_post.call_args.args[0] == "予約の本文"
    assert scheduled.run_due(conn, config, log=lambda m: None) == 0  # 二度は、実行しない


def test_failed_post_is_recorded_and_not_retried(env):
    config, conn, client = env
    schedule(client, payload())
    conn.execute("UPDATE scheduled_posts SET run_at = ?", ((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),))
    conn.commit()
    bad = fake_fanvue()
    bad.create_post.side_effect = RuntimeError("boom")
    with patch("core.cli._try_create_fanvue_client", return_value=(bad, None)):
        scheduled.run_due(conn, config, log=lambda m: None)
        scheduled.run_due(conn, config, log=lambda m: None)
    post = scheduled.list_posts(conn)[0]
    assert post["status"] == "failed" and "boom" in post["error"]
    assert bad.create_post.call_count == 1  # 再試行しない(二重投稿を避ける)
    assert notifications.list_notifications(conn, 1)[0]["level"] == "error"


def test_run_checks_again_at_run_time(env):
    """予約したあとに、作品がごみ箱へ入った・連携が切れた場合は、投稿せずに、理由つきの失敗にして通知する。"""
    config, conn, client = env
    schedule(client, payload())
    db.trash_assets(conn, ["a1"])
    conn.execute("UPDATE scheduled_posts SET run_at = ?", ((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),))
    conn.commit()
    fanvue = fake_fanvue()
    with patch("core.cli._try_create_fanvue_client", return_value=(fanvue, None)):
        scheduled.run_due(conn, config, log=lambda m: None)
    post = scheduled.list_posts(conn)[0]
    assert post["status"] == "failed" and "投稿できない作品" in post["error"]
    fanvue.upload_media.assert_not_called()
    item = notifications.list_notifications(conn, 1)[0]
    assert "実行できませんでした" in item["title"] and item["level"] == "error"


def test_runs_wait_while_storage_is_unavailable_and_overdue_runs_late(env):
    config, conn, client = env
    schedule(client, payload())
    conn.execute("UPDATE scheduled_posts SET run_at = ?", ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),))
    conn.commit()
    with patch("core.storage.check") as check:
        check.return_value.available = False
        assert scheduled.run_due(conn, config, log=lambda m: None) == 0
    assert scheduled.list_posts(conn)[0]["status"] == "scheduled"  # つながるまで、予約のまま
    logs = []
    with patch("core.cli._try_create_fanvue_client", return_value=(fake_fanvue(), None)):
        assert scheduled.run_due(conn, config, log=logs.append) == 1  # 遅れても、実行する
    assert "分遅れ" in logs[0]


def test_claim_is_exclusive_and_interrupted_runs_are_not_repeated(env):
    config, conn, client = env
    schedule(client, payload())
    conn.execute("UPDATE scheduled_posts SET run_at = ?", ((datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),))
    conn.commit()
    other = db.get_connection(config.paths.db_path)
    assert scheduled.claim_due(conn) is not None and scheduled.claim_due(other) is None  # 同じ予約を、2つのプロセスが実行しない
    other.close()
    assert scheduled.mark_interrupted(conn) == 1  # 実行の途中で止まったものは、再実行せずに、失敗として残す
    post = scheduled.list_posts(conn)[0]
    assert post["status"] == "failed" and "途中で止まりました" in post["error"]


# ------------------------------------------------------------------ 取り消し・いますぐ実行
def test_cancel_and_run_now(env):
    config, conn, client = env
    first = schedule(client, payload(ids=("a1",))).get_json()["id"]
    second = schedule(client, payload(ids=("a2",))).get_json()["id"]
    assert client.post(f"/api/scheduled-posts/{first}/cancel").status_code == 200
    assert client.post(f"/api/scheduled-posts/{first}/cancel").status_code == 409  # 取り消し済みは、取り消せない
    assert client.post(f"/api/scheduled-posts/{second}/run-now").status_code == 200
    with patch("core.cli._try_create_fanvue_client", return_value=(fake_fanvue(), None)):
        assert scheduled.run_due(conn, config, log=lambda m: None) == 1  # いますぐ実行にした分だけ
    statuses = {p["id"]: p["status"] for p in scheduled.list_posts(conn)}
    assert statuses == {first: "cancelled", second: "done"}
    assert db.get_posts(conn, ["a1"]) == {} or "fanvue" not in db.get_posts(conn, ["a1"]).get("a1", {})


# ------------------------------------------------------------------ 画面
def test_versions_api_lists_existing_reservations_and_ui_hooks(env):
    config, conn, client = env
    schedule(client, payload("x", ("a1",)), minutes=120)
    data = client.get("/api/assets/a1/versions").get_json()
    assert len(data["scheduled"]) == 1 and data["scheduled"][0]["channel"] == "x" and "(" in data["scheduled"][0]["run_at_label"]
    assert client.get("/api/assets/a2/versions").get_json()["scheduled"] == []

    html = client.get("/").get_data(as_text=True)
    for needle in ('name="pn-when"', 'id="pn-run-at"', 'id="pn-scheduled-note"'):
        assert needle in html
    js = client.get("/static/post-now.js").get_data(as_text=True)
    assert "/api/scheduled-posts" in js and "run_at" in js
    settings_page = client.get("/settings").get_data(as_text=True)
    assert 'id="sp-list"' in settings_page and "予約中の投稿" in settings_page
