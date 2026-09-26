from datetime import datetime, timezone

import pytest

from core import db


@pytest.fixture
def conn(tmp_path):
    connection = db.get_connection(tmp_path / "reelmilly.db")
    db.init_db(connection)
    yield connection
    connection.close()


def _make_asset(asset_id="a1", **overrides):
    now = datetime.now(timezone.utc).isoformat()
    asset = {
        "id": asset_id,
        "status": "ready",
        "kind": "image",
        "file_path": f"data/library/ready/{asset_id}/look-a.jpg",
        "created_at": now,
        "updated_at": now,
    }
    asset.update(overrides)
    return asset


def test_insert_and_get_asset(conn):
    db.insert_asset(conn, _make_asset())

    result = db.get_asset(conn, "a1")

    assert result["id"] == "a1"
    assert result["status"] == "ready"
    assert result["content_rating_confirmed"] == 0


def test_get_asset_missing_returns_none(conn):
    assert db.get_asset(conn, "missing") is None


def test_update_asset(conn):
    db.insert_asset(conn, _make_asset())

    db.update_asset(conn, "a1", content_rating="sfw", content_rating_confirmed=1)

    result = db.get_asset(conn, "a1")
    assert result["content_rating"] == "sfw"
    assert result["content_rating_confirmed"] == 1


def test_list_assets_filters_by_status_and_content_rating(conn):
    db.insert_asset(conn, _make_asset("a1", status="ready", content_rating="sfw"))
    db.insert_asset(conn, _make_asset("a2", status="posted", content_rating="sfw"))
    db.insert_asset(conn, _make_asset("a3", status="ready", content_rating="explicit"))

    ready_only = db.list_assets(conn, status="ready")
    assert {row["id"] for row in ready_only} == {"a1", "a3"}

    ready_sfw = db.list_assets(conn, status="ready", content_rating="sfw")
    assert {row["id"] for row in ready_sfw} == {"a1"}


def test_channels(conn):
    db.insert_asset(conn, _make_asset())

    db.add_channel(conn, "a1", "fanvue")
    db.add_channel(conn, "a1", "x")
    db.add_channel(conn, "a1", "fanvue")  # 重複は無視される

    assert set(db.get_channels(conn, "a1")) == {"fanvue", "x"}


def test_tags(conn):
    db.insert_asset(conn, _make_asset())

    db.add_tag_to_asset(conn, "a1", "推し")
    db.add_tag_to_asset(conn, "a1", "夏")

    assert db.list_tags_for_asset(conn, "a1") == ["夏", "推し"]
    assert set(db.list_all_tags(conn)) == {"夏", "推し"}

    db.remove_tag_from_asset(conn, "a1", "夏")
    assert db.list_tags_for_asset(conn, "a1") == ["推し"]


def test_folders(conn):
    db.insert_asset(conn, _make_asset())
    folder_id = db.create_folder(conn, "2026-09")

    db.add_asset_to_folder(conn, "a1", folder_id)

    folders = db.list_folders_for_asset(conn, "a1")
    assert len(folders) == 1
    assert folders[0]["name"] == "2026-09"

    assets_in_folder = db.list_assets(conn, folder_id=folder_id)
    assert {row["id"] for row in assets_in_folder} == {"a1"}

    db.remove_asset_from_folder(conn, "a1", folder_id)
    assert db.list_folders_for_asset(conn, "a1") == []


def test_list_assets_by_tag(conn):
    db.insert_asset(conn, _make_asset("a1"))
    db.insert_asset(conn, _make_asset("a2"))
    db.add_tag_to_asset(conn, "a1", "推し")

    result = db.list_assets(conn, tag="推し")
    assert {row["id"] for row in result} == {"a1"}


def test_list_assets_by_channel(conn):
    db.insert_asset(conn, _make_asset("a1"))
    db.insert_asset(conn, _make_asset("a2"))
    db.add_channel(conn, "a1", "fanvue")
    db.add_channel(conn, "a2", "x")

    result = db.list_assets(conn, channel="fanvue")
    assert {row["id"] for row in result} == {"a1"}


def test_list_assets_confirmed_only(conn):
    db.insert_asset(conn, _make_asset("a1", content_rating_confirmed=1))
    db.insert_asset(conn, _make_asset("a2", content_rating_confirmed=0))

    result = db.list_assets(conn, confirmed_only=True)
    assert {row["id"] for row in result} == {"a1"}


def test_list_assets_order_asc_returns_oldest_first(conn):
    db.insert_asset(conn, _make_asset("a1", created_at="2026-01-01T00:00:00+00:00"))
    db.insert_asset(conn, _make_asset("a2", created_at="2026-01-02T00:00:00+00:00"))

    result = db.list_assets(conn, order="asc")
    assert [row["id"] for row in result] == ["a1", "a2"]

    result_desc = db.list_assets(conn, order="desc")
    assert [row["id"] for row in result_desc] == ["a2", "a1"]


def test_job_runs_last_run_date(conn):
    assert db.get_last_run_date(conn, "drop") is None

    db.set_last_run_date(conn, "drop", "2026-09-26")
    assert db.get_last_run_date(conn, "drop") == "2026-09-26"

    db.set_last_run_date(conn, "drop", "2026-09-27")
    assert db.get_last_run_date(conn, "drop") == "2026-09-27"


def test_list_assets_filters_by_kind(conn):
    db.insert_asset(conn, _make_asset("a1", kind="image"))
    db.insert_asset(conn, _make_asset("a2", kind="video"))

    result = db.list_assets(conn, kind="image")

    assert [row["id"] for row in result] == ["a1"]


def test_get_and_set_setting(conn):
    assert db.get_setting(conn, "caption_mode") is None

    db.set_setting(conn, "caption_mode", "draft")
    assert db.get_setting(conn, "caption_mode") == "draft"

    db.set_setting(conn, "caption_mode", "auto")
    assert db.get_setting(conn, "caption_mode") == "auto"


def test_list_settings_returns_all(conn):
    db.set_setting(conn, "caption_mode", "draft")
    db.set_setting(conn, "generation_provider", "openai")

    result = db.list_settings(conn)

    assert result == {"caption_mode": "draft", "generation_provider": "openai"}
