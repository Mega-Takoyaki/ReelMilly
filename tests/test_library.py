"""原本と加工版のライブラリ: 区分の引き継ぎ・加工版ごとの承認・AI判定・API。"""
from unittest.mock import MagicMock, patch

import pytest

from core import db, versions, worker
from core.nsfw import NsfwResult
from core.web.app import create_app

from tests.test_worker import _insert, _setup  # noqa: F401


@pytest.fixture
def env(tmp_path):
    config, conn = _setup(tmp_path)
    _insert(conn, tmp_path, "a1")
    wm = tmp_path / "wm.png"
    wm.write_bytes(b"w")
    db.update_asset(conn, "a1", wm_path=str(wm), wm_text="@me")
    # 編集動画(サブ動画)は、DBに直接入れる(ffmpegなしで試す)
    conn.execute(
        "INSERT INTO asset_edits (asset_id, kind, params, summary, status, filename, created_at) "
        "VALUES ('a1', 'trim', '{}', 'トリム 0:02.0～0:05.0', 'done', 'e1.mp4', 't')"
    )
    conn.commit()
    edits_dir = tmp_path / "edits"
    edits_dir.mkdir()
    (edits_dir / "e1.mp4").write_bytes(b"v")
    with patch("core.edits.edits_dir", return_value=edits_dir):
        yield config, conn, create_app(config).test_client(), tmp_path


def asset(conn):
    return db.get_asset(conn, "a1")


def test_rating_is_inherited_until_the_derived_file_is_decided(env):
    config, conn, client, _ = env
    # 原本が未承認: どれも未承認
    assert versions.rating_info(conn, asset(conn), "edit:1")["rating"] is None
    db.update_asset(conn, "a1", content_rating="explicit", content_rating_confirmed=1)
    info = versions.rating_info(conn, asset(conn), "edit:1")
    assert (info["rating"], info["source"]) == ("explicit", "inherited")  # 加工版は、原本の区分を引き継ぐ(安全側)
    assert versions.rating_info(conn, asset(conn), "wm")["source"] == "inherited"

    # 加工版だけを、sfwにできる(例: 見せたくない部分を切り落とした)。原本は、explicitのまま
    versions.set_rating(conn, asset(conn), "edit:1", "sfw")
    info = versions.rating_info(conn, asset(conn), "edit:1")
    assert (info["rating"], info["source"]) == ("sfw", "own")
    assert versions.rating_info(conn, asset(conn), "original")["rating"] == "explicit"
    assert versions.rating_info(conn, asset(conn), "wm")["rating"] == "explicit"  # ほかの加工版には、影響しない

    versions.set_rating(conn, asset(conn), "edit:1", None)  # 引き継ぎに戻す
    assert versions.rating_info(conn, asset(conn), "edit:1")["source"] == "inherited"
    with pytest.raises(versions.VersionError):
        versions.set_rating(conn, asset(conn), "original", "sfw")
    with pytest.raises(versions.VersionError):
        versions.set_rating(conn, asset(conn), "edit:1", "weird")
    with pytest.raises(versions.VersionError):
        versions.set_rating(conn, asset(conn), "edit:99", "sfw")


def test_less_restrictive_helper():
    assert versions.is_less_restrictive("sfw", "explicit") and versions.is_less_restrictive("suggestive", "explicit")
    assert not versions.is_less_restrictive("explicit", "sfw") and not versions.is_less_restrictive("sfw", "sfw")
    assert not versions.is_less_restrictive("sfw", None)


def test_library_rows_lists_original_wm_and_edits_with_states(env):
    config, conn, client, _ = env
    conn.execute("INSERT INTO asset_edits (asset_id, kind, params, summary, status, created_at) VALUES ('a1','trim','{}','トリム 0:00.0～0:01.0','queued','t2')")
    conn.commit()
    rows = versions.library_rows(conn, asset(conn))
    assert [r["key"] for r in rows] == ["original", "wm", "edit:2", "edit:1"]  # 原本→透かし入り→加工版(新しい順)
    assert [r["state"] for r in rows] == ["done", "done", "queued", "done"]
    assert rows[0]["label"] == "原本" and rows[1]["label"] == "透かし入り" and rows[3]["detail"].startswith("トリム")
    assert rows[0]["media_type"] == "image" and rows[3]["media_type"] == "video"


def test_library_api(env):
    config, conn, client, _ = env
    data = client.get("/api/assets/a1/library").get_json()
    keys = [r["key"] for r in data["rows"]]
    assert keys == ["original", "wm", "edit:1"] and data["edit_limit"] == 10
    by_key = {r["key"]: r for r in data["rows"]}
    assert by_key["wm"]["url"].endswith("variant=wm") and "download" in by_key["original"]["download_url"]
    assert by_key["edit:1"]["download_url"].endswith("download=1")
    assert client.get("/api/assets/nope/library").status_code == 404


def test_rating_api_returns_effective_rating(env):
    config, conn, client, _ = env
    db.update_asset(conn, "a1", content_rating="explicit", content_rating_confirmed=1)
    res = client.post("/api/assets/a1/versions/edit:1/rating", json={"rating": "sfw"}).get_json()
    assert (res["rating"], res["source"]) == ("sfw", "own") and res["original_rating"] == "explicit"
    assert client.post("/api/assets/a1/versions/wm/rating", json={"rating": "suggestive"}).get_json()["rating"] == "suggestive"
    assert client.post("/api/assets/a1/versions/edit:1/rating", json={"rating": None}).get_json()["source"] == "inherited"
    assert client.post("/api/assets/a1/versions/original/rating", json={"rating": "sfw"}).status_code == 400
    assert client.post("/api/assets/a1/versions/edit:1/rating", json={"rating": "x"}).status_code == 400


def test_dialog_versions_carry_per_file_rating(env):
    config, conn, client, _ = env
    db.update_asset(conn, "a1", content_rating="explicit", content_rating_confirmed=1)
    versions.set_rating(conn, asset(conn), "edit:1", "sfw")
    data = client.get("/api/assets/a1/versions").get_json()
    by_key = {v["key"]: v for v in data["versions"]}
    assert by_key["original"]["rating"] == "explicit" and by_key["wm"]["rating_source"] == "inherited"
    assert by_key["edit:1"]["rating"] == "sfw" and by_key["edit:1"]["rating_source"] == "own"  # 投稿のとき、選んだファイルの区分を使う


# ------------------------------------------------------------------ 加工版のAI判定
def test_version_nsfw_is_queued_per_version_and_recorded_on_that_version(env):
    config, conn, client, tmp = env
    assert client.post("/api/assets/a1/versions/edit:1/nsfw").get_json() == {"queued": 1}
    assert client.post("/api/assets/a1/versions/edit:1/nsfw").get_json() == {"queued": 0}  # 同じ加工版は、二重に積まない
    assert client.post("/api/assets/a1/versions/wm/nsfw").get_json() == {"queued": 1}       # 別の加工版は、別のタスク
    assert client.post("/api/assets/a1/versions/edit:9/nsfw").status_code == 400
    rows = versions.library_rows(conn, asset(conn))
    assert [r["ai_busy"] for r in rows] == [False, True, True]

    classifier = MagicMock()
    classifier.classify.return_value = NsfwResult(rating="sfw", confidence=0.93)
    with patch("core.worker.try_create_classifier", return_value=classifier), patch("core.generation.try_create_generator", return_value=None):
        worker.AnalysisWorker(config).run_once(conn, log=lambda m: None)
    rows = {r["key"]: r for r in versions.library_rows(conn, asset(conn))}
    assert (rows["edit:1"]["auto"], rows["wm"]["auto"]) == ("sfw", "sfw") and rows["edit:1"]["auto_confidence"] == pytest.approx(0.93)
    assert rows["original"]["auto"] is None and not rows["edit:1"]["ai_busy"]  # 原本には、書かない(原本の判定は、別)
    assert db.get_asset(conn, "a1")["nsfw_auto_rating"] is None


def test_original_nsfw_uses_the_existing_queue(env):
    config, conn, client, _ = env
    assert client.post("/api/assets/a1/versions/original/nsfw").get_json() == {"queued": 1}
    assert conn.execute("SELECT kind FROM ai_tasks").fetchone()[0] == "nsfw"
