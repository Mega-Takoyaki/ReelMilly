from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from core import db
from core.config import load_config
from core.events import read_events
from posting.jobs import run_fanvue_drop, run_fanvue_drop_batch


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
  x: [sfw, suggestive, explicit]
platform_auto_post_ratings:
  fanvue: [sfw, suggestive, explicit]
  x: [sfw]
web:
  host: 127.0.0.1
  port: 8420
"""


@pytest.fixture
def setup(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    config = load_config(base_dir=tmp_path)
    config.paths.ready.mkdir(parents=True, exist_ok=True)
    conn = db.get_connection(config.paths.db_path)
    db.init_db(conn)
    return config, conn


def _make_ready_asset(config, conn, asset_id="a1", **overrides):
    now = datetime.now(timezone.utc).isoformat()
    asset_dir = config.paths.ready / asset_id
    asset_dir.mkdir(parents=True, exist_ok=True)
    file_path = asset_dir / "look.jpg"
    file_path.write_bytes(b"fake-bytes")

    asset = {
        "id": asset_id,
        "status": "ready",
        "kind": "image",
        "file_path": str(file_path),
        "content_rating": "sfw",
        "content_rating_confirmed": 1,
        "audience": "subscribers",
        "price_cents": 499,
        "caption": "テストキャプション",
        "created_at": now,
        "updated_at": now,
    }
    asset.update(overrides)
    db.insert_asset(conn, asset)
    db.add_channel(conn, asset_id, "fanvue")
    return asset


def _mock_fanvue_client(media_uuid="media-uuid-1"):
    client = MagicMock()
    client.upload_media.return_value = media_uuid
    client.wait_for_media_ready.return_value = True
    client.create_post.return_value = {"id": "post-1"}
    return client


def test_run_fanvue_drop_no_candidates_returns_not_executed(setup):
    config, conn = setup

    result = run_fanvue_drop(
        config, conn, _mock_fanvue_client(), fanvue_handle="creator", post_url_template="https://fanvue.com/{handle}"
    )

    assert result.executed is False
    assert "ありません" in result.skipped_reason


def test_run_fanvue_drop_success_marks_posted(setup):
    config, conn = setup
    _make_ready_asset(config, conn)
    client = _mock_fanvue_client()

    result = run_fanvue_drop(
        config, conn, client, fanvue_handle="creator", post_url_template="https://www.fanvue.com/{handle}"
    )

    assert result.executed is True
    assert result.fanvue_uuid == "media-uuid-1"
    assert result.fanvue_url == "https://www.fanvue.com/creator"

    asset = db.get_asset(conn, "a1")
    assert asset["status"] == "posted"
    assert asset["fanvue_url"] == "https://www.fanvue.com/creator"
    assert asset["fanvue_uuid"] == "media-uuid-1"

    events = read_events(config.paths.events_path)
    assert any(e["event"] == "drop_ok" and e["asset_id"] == "a1" for e in events)


def test_run_fanvue_drop_success_tags_asset_as_posted(setup):
    config, conn = setup
    _make_ready_asset(config, conn)

    run_fanvue_drop(
        config, conn, _mock_fanvue_client(), fanvue_handle="creator", post_url_template="https://f.com/{handle}"
    )

    assert "fanvue投稿済み" in db.list_tags_for_asset(conn, "a1")


def test_run_fanvue_drop_failure_does_not_tag_asset(setup):
    config, conn = setup
    _make_ready_asset(config, conn)
    client = _mock_fanvue_client()
    client.upload_media.side_effect = RuntimeError("upload failed")

    run_fanvue_drop(config, conn, client, fanvue_handle="c", post_url_template="https://f.com/{handle}")

    assert db.list_tags_for_asset(conn, "a1") == []


def test_run_fanvue_drop_picks_oldest_ready_asset(setup):
    config, conn = setup
    _make_ready_asset(config, conn, "a1", created_at="2026-01-02T00:00:00+00:00")
    _make_ready_asset(config, conn, "a2", created_at="2026-01-01T00:00:00+00:00")
    client = _mock_fanvue_client()

    result = run_fanvue_drop(config, conn, client, fanvue_handle="c", post_url_template="https://f.com/{handle}")

    assert result.asset_id == "a2"


def test_run_fanvue_drop_skips_unconfirmed_asset(setup):
    config, conn = setup
    _make_ready_asset(config, conn, content_rating_confirmed=0)

    result = run_fanvue_drop(
        config, conn, _mock_fanvue_client(), fanvue_handle="c", post_url_template="https://f.com/{handle}"
    )

    assert result.executed is False
    assert result.asset_id is None  # confirmed_only=Trueのフィルタで最初から対象外


def test_run_fanvue_drop_skips_when_channel_not_registered(setup):
    config, conn = setup
    now = datetime.now(timezone.utc).isoformat()
    asset_dir = config.paths.ready / "a1"
    asset_dir.mkdir(parents=True, exist_ok=True)
    (asset_dir / "look.jpg").write_bytes(b"x")
    db.insert_asset(
        conn,
        {
            "id": "a1",
            "status": "ready",
            "kind": "image",
            "file_path": str(asset_dir / "look.jpg"),
            "content_rating": "sfw",
            "content_rating_confirmed": 1,
            "created_at": now,
            "updated_at": now,
        },
    )
    db.add_channel(conn, "a1", "x")  # fanvueチャンネル未登録

    result = run_fanvue_drop(
        config, conn, _mock_fanvue_client(), fanvue_handle="c", post_url_template="https://f.com/{handle}"
    )

    assert result.executed is False
    assert result.asset_id is None


def test_run_fanvue_drop_skips_explicit_content_when_policy_disallows(setup):
    config, conn = setup
    config.platform_auto_post_ratings["fanvue"] = ["sfw"]  # explicitは自動投稿対象外に変更
    _make_ready_asset(config, conn, content_rating="explicit")

    result = run_fanvue_drop(
        config, conn, _mock_fanvue_client(), fanvue_handle="c", post_url_template="https://f.com/{handle}"
    )

    assert result.executed is False
    assert result.asset_id == "a1"
    assert "自動投稿対象外" in result.skipped_reason

    asset = db.get_asset(conn, "a1")
    assert asset["status"] == "ready"  # 状態は変更されない


def test_run_fanvue_drop_failure_marks_failed_fanvue(setup):
    config, conn = setup
    _make_ready_asset(config, conn)
    client = _mock_fanvue_client()
    client.upload_media.side_effect = RuntimeError("upload failed: 500")

    result = run_fanvue_drop(
        config, conn, client, fanvue_handle="c", post_url_template="https://f.com/{handle}"
    )

    assert result.executed is False
    assert result.error == "upload failed: 500"

    asset = db.get_asset(conn, "a1")
    assert asset["status"] == "failed_fanvue"

    events = read_events(config.paths.events_path)
    assert any(e["event"] == "fanvue_failed" and e["asset_id"] == "a1" for e in events)


def test_run_fanvue_drop_generates_caption_when_missing_in_auto_mode(setup):
    config, conn = setup
    _make_ready_asset(config, conn, caption=None, content_description="赤いドレスの女性が微笑んでいる")
    client = _mock_fanvue_client()
    generator = MagicMock()
    generator.generate_caption.return_value = "今日の一枚です"

    result = run_fanvue_drop(
        config, conn, client, fanvue_handle="creator", post_url_template="https://f.com/{handle}",
        generator=generator,
    )

    assert result.executed is True
    assert generator.generate_caption.call_args[0][0] == "赤いドレスの女性が微笑んでいる"
    client.create_post.assert_called_once()
    assert client.create_post.call_args.kwargs["text"] == "今日の一枚です"

    asset = db.get_asset(conn, "a1")
    assert asset["fanvue_text"] == "今日の一枚です"


def test_run_fanvue_drop_draft_mode_saves_draft_without_posting(setup):
    config, conn = setup
    _make_ready_asset(config, conn, caption=None, content_description="赤いドレスの女性が微笑んでいる")
    db.set_setting(conn, "caption_mode", "draft")
    client = _mock_fanvue_client()
    generator = MagicMock()
    generator.generate_caption.return_value = "今日の一枚です"

    result = run_fanvue_drop(
        config, conn, client, fanvue_handle="creator", post_url_template="https://f.com/{handle}",
        generator=generator,
    )

    assert result.executed is False
    assert "下書き" in result.skipped_reason
    client.upload_media.assert_not_called()

    asset = db.get_asset(conn, "a1")
    assert asset["status"] == "ready"
    assert asset["fanvue_text"] is None
    assert asset["fanvue_caption_draft"] == "今日の一枚です"


def test_run_fanvue_drop_does_not_generate_caption_without_content_description(setup):
    config, conn = setup
    _make_ready_asset(config, conn, caption=None)
    client = _mock_fanvue_client()
    generator = MagicMock()

    result = run_fanvue_drop(
        config, conn, client, fanvue_handle="creator", post_url_template="https://f.com/{handle}",
        generator=generator,
    )

    assert result.executed is True
    generator.generate_caption.assert_not_called()
    assert client.create_post.call_args.kwargs["text"] == ""


def test_run_fanvue_drop_batch_posts_up_to_count(setup):
    config, conn = setup
    _make_ready_asset(config, conn, "a1", created_at="2026-01-01T00:00:00+00:00")
    _make_ready_asset(config, conn, "a2", created_at="2026-01-02T00:00:00+00:00")
    client = _mock_fanvue_client()

    results = run_fanvue_drop_batch(
        config, conn, client, fanvue_handle="c", post_url_template="https://f.com/{handle}", count=2
    )

    assert [r.asset_id for r in results] == ["a1", "a2"]
    assert all(r.executed for r in results)


def test_run_fanvue_drop_batch_stops_when_no_more_candidates(setup):
    config, conn = setup
    _make_ready_asset(config, conn, "a1")
    client = _mock_fanvue_client()

    results = run_fanvue_drop_batch(
        config, conn, client, fanvue_handle="c", post_url_template="https://f.com/{handle}", count=5
    )

    assert len(results) == 2  # 1件目は成功、2件目は「対象なし」で打ち切り
    assert results[0].executed is True
    assert results[1].executed is False


def test_run_fanvue_drop_filters_by_kind(setup):
    config, conn = setup
    _make_ready_asset(config, conn, "a1", kind="video")
    client = _mock_fanvue_client()

    result = run_fanvue_drop(
        config, conn, client, fanvue_handle="c", post_url_template="https://f.com/{handle}", kind="image"
    )

    assert result.executed is False
    assert result.asset_id is None


def test_run_fanvue_drop_timeout_marks_failed_fanvue(setup):
    config, conn = setup
    _make_ready_asset(config, conn)
    client = _mock_fanvue_client()
    client.wait_for_media_ready.return_value = False

    result = run_fanvue_drop(
        config, conn, client, fanvue_handle="c", post_url_template="https://f.com/{handle}"
    )

    assert result.executed is False
    asset = db.get_asset(conn, "a1")
    assert asset["status"] == "failed_fanvue"
    client.create_post.assert_not_called()
