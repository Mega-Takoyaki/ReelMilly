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


def test_list_assets_confirmed_true_filters_to_confirmed_only(conn):
    db.insert_asset(conn, _make_asset("a1", content_rating_confirmed=1))
    db.insert_asset(conn, _make_asset("a2", content_rating_confirmed=0))

    result = db.list_assets(conn, confirmed=True)
    assert {row["id"] for row in result} == {"a1"}


def test_list_assets_confirmed_false_filters_to_unconfirmed_only(conn):
    db.insert_asset(conn, _make_asset("a1", content_rating_confirmed=1))
    db.insert_asset(conn, _make_asset("a2", content_rating_confirmed=0))

    result = db.list_assets(conn, confirmed=False)
    assert {row["id"] for row in result} == {"a2"}


def test_list_assets_confirmed_none_returns_all(conn):
    db.insert_asset(conn, _make_asset("a1", content_rating_confirmed=1))
    db.insert_asset(conn, _make_asset("a2", content_rating_confirmed=0))

    result = db.list_assets(conn, confirmed=None)
    assert {row["id"] for row in result} == {"a1", "a2"}


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


def test_legacy_posted_statuses_migrate_to_posts_axis(tmp_path):
    conn = db.get_connection(tmp_path / "t.db")
    db.init_db(conn)
    db.insert_asset(conn, _make_asset("p1", status="posted", fanvue_url="https://f/x", fanvue_uuid="u1"))
    db.insert_asset(conn, _make_asset("f1", status="failed_fanvue"))

    db.init_db(conn)  # 起動時に走る移行(冪等)
    db.init_db(conn)

    assert db.get_asset(conn, "p1")["status"] == "ready"
    assert db.get_asset(conn, "f1")["status"] == "ready"
    posts = db.get_posts(conn, ["p1", "f1"])
    assert posts["p1"]["fanvue"]["status"] == "posted" and posts["p1"]["fanvue"]["url"] == "https://f/x"
    assert posts["f1"]["fanvue"]["status"] == "failed"


def test_list_assets_filters_by_post_state(tmp_path):
    conn = db.get_connection(tmp_path / "t.db")
    db.init_db(conn)
    for i in ("a", "b", "c"):
        db.insert_asset(conn, _make_asset(i))
    db.set_post(conn, "a", "fanvue", "posted", url="u")
    db.set_post(conn, "b", "fanvue", "failed", error="e")

    ids = lambda **kw: sorted(x["id"] for x in db.list_assets(conn, **kw))
    assert ids(post_channel="fanvue", post_status="posted") == ["a"]
    assert ids(post_channel="fanvue", post_status="failed") == ["b"]
    assert ids(post_channel="fanvue", post_status="none") == ["c"]
    assert ids(post_channel="x", post_status="none") == ["a", "b", "c"]


def test_list_assets_multi_select_filters_and_text_search(tmp_path):
    conn = db.get_connection(tmp_path / "t.db")
    db.init_db(conn)
    db.insert_asset(conn, _make_asset("a", status="analyzing", content_description="赤いドレスの女性"))
    db.insert_asset(conn, _make_asset("b", status="ready", content_rating="sfw", content_rating_confirmed=True))
    db.insert_asset(conn, _make_asset("c", status="pending_approval", content_description="海辺の風景 100%"))
    db.add_tag_to_asset(conn, "a", "夜")
    db.add_tag_to_asset(conn, "a", "女性")
    db.add_tag_to_asset(conn, "b", "夜")
    f1 = db.create_folder(conn, "f1")
    db.add_asset_to_folder(conn, "a", f1)

    ids = lambda **kw: sorted(x["id"] for x in db.list_assets(conn, **kw))
    assert ids(status=["analyzing", "ready"]) == ["a", "b"]  # 項目内はOR
    assert ids(status=["analyzing"], tag=["夜"]) == ["a"]  # 項目間はAND
    assert ids(tag=["夜", "女性"]) == ["a"]  # タグを複数選ぶとすべてを含むもの
    assert ids(folder_id=[f1]) == ["a"]
    assert ids(confirmed=[True]) == ["b"]
    assert ids(confirmed=[True, False]) == ["a", "b", "c"]  # 両方選ぶと絞り込まない
    assert ids(post_filters=[("fanvue", "none")]) == ["a", "b", "c"]
    assert ids(status="ready") == ["b"]  # 従来の単一指定も使える

    # フリーテキスト: AI生成の説明文またはタグ名。空白区切りはAND。%や_はそのまま扱う
    assert ids(q="ドレス") == ["a"]
    assert ids(q="夜") == ["a", "b"]
    assert ids(q="ドレス 女性") == ["a"]
    assert ids(q="ドレス 風景") == []
    assert ids(q="100%") == ["c"]
    assert ids(q="%") == ["c"]


def test_trash_hides_assets_everywhere_and_restore_brings_back(tmp_path):
    conn = db.get_connection(tmp_path / "t.db")
    db.init_db(conn)
    db.insert_asset(conn, _make_asset("a"))
    db.insert_asset(conn, _make_asset("b"))
    db.enqueue_ai_task(conn, "a", "nsfw")

    assert db.trash_assets(conn, ["a", "missing"]) == 1
    assert db.trash_assets(conn, ["a"]) == 0  # 二重に移さない

    ids = lambda **kw: sorted(x["id"] for x in db.list_assets(conn, **kw))
    assert ids() == ["b"]  # 通常の一覧には出ない
    assert ids(trashed=True) == ["a"]
    assert db.count_trashed(conn) == 1
    assert db.ai_task_counts(conn)["queued"] == 0  # 待機中のAI処理は取り消す
    assert "a" not in db.unprocessed_asset_ids(conn, "nsfw")  # 定期実行の対象外
    assert db.get_asset(conn, "a") is not None  # 詳細は開ける(ファイルも消えない)

    assert db.restore_assets(conn, ["a"]) == 1
    assert ids() == ["a", "b"] and db.count_trashed(conn) == 0


def test_no_plan_is_channels_empty_and_filterable(tmp_path):
    conn = db.get_connection(tmp_path / "t.db")
    db.init_db(conn)
    db.insert_asset(conn, _make_asset("a"))
    db.insert_asset(conn, _make_asset("b"))
    db.set_channels(conn, "a", ["fanvue", "x"])
    db.set_channels(conn, "b", [])  # 投稿予定なし

    ids = lambda **kw: sorted(x["id"] for x in db.list_assets(conn, **kw))
    assert ids(plan=["planned"]) == ["a"]
    assert ids(plan=["none"]) == ["b"]
    assert ids(channel="fanvue") == ["a"]  # 投稿ジョブ(channel指定)の対象から外れる
    assert db.get_channels_map(conn, ["a", "b"]) == {"a": ["fanvue", "x"], "b": []}


def test_facets_return_only_existing_values_with_counts(tmp_path):
    conn = db.get_connection(tmp_path / "t.db")
    db.init_db(conn)
    db.insert_asset(conn, _make_asset("a", status="ready", content_rating="sfw", content_rating_confirmed=True, nsfw_auto_rating="sfw"))
    db.insert_asset(conn, _make_asset("b", status="analyzing"))
    db.insert_asset(conn, _make_asset("c", status="analyzing"))
    db.add_tag_to_asset(conn, "a", "夜")
    db.add_tag_to_asset(conn, "c", "夜")
    db.set_channels(conn, "a", ["fanvue"])
    db.set_post(conn, "a", "fanvue", "posted", url="u")
    db.trash_assets(conn, ["c"])

    f = db.facets(conn, ["fanvue"])
    assert f["status"] == [("analyzing", 1), ("ready", 1)]  # ごみ箱のcは数えない
    assert f["content_rating"] == [("sfw", 1), (db.NONE_VALUE, 1)]  # suggestive/explicitは実在しないので出ない
    assert f["nsfw_auto"] == [("sfw", 1), (db.NONE_VALUE, 1)]
    assert f["tag"] == [("夜", 1)]
    assert f["plan"] == [("planned", 1), ("none", 1)]
    assert ("fanvue", "posted", 1) in f["post"] and ("fanvue", "none", 1) in f["post"]

    # 「未設定」で絞り込める
    ids = sorted(x["id"] for x in db.list_assets(conn, content_rating=[db.NONE_VALUE]))
    assert ids == ["b"]


def test_file_type_two_level_filter_and_facets(tmp_path):
    conn = db.get_connection(tmp_path / "t.db")
    db.init_db(conn)
    for asset_id, kind, path in [
        ("a", "image", "/x/a/one.JPG"), ("b", "image", "/x/b/two.png"), ("c", "image", "/x/c/three.jpg"),
        ("d", "video", "/x/d/four.mp4"), ("e", "video", "/x/e/five.webm"),
    ]:
        db.insert_asset(conn, _make_asset(asset_id, kind=kind, file_path=path))

    ids = lambda **kw: sorted(x["id"] for x in db.list_assets(conn, **kw))
    assert ids(kind=["image"]) == ["a", "b", "c"]  # 親(種別)=配下の全拡張子
    assert ids(kind="video") == ["d", "e"]  # 単一指定(投稿ジョブ等)も従来どおり
    assert ids(ext=["jpg"]) == ["a", "c"]  # 拡張子は大文字小文字を区別しない
    assert ids(ext=[".png", "mp4"]) == ["b", "d"]  # 種別をまたいで拡張子だけ選べる
    assert ids(kind=["video"], ext=["png"]) == ["b", "d", "e"]  # 種別と拡張子はOR
    assert ids(kind=["image"], status="ready") == ["a", "b", "c"]  # 他の項目とはAND
    assert ids(kind=["image"], status="analyzing") == []

    tree = db.facets(conn, [])["type"]
    assert tree == [
        ("image", 3, [("jpg", 2), ("png", 1)]),
        ("video", 2, [("mp4", 1), ("webm", 1)]),
    ]


def test_tag_and_folder_filters_support_all_and_any_modes(tmp_path):
    conn = db.get_connection(tmp_path / "t.db")
    db.init_db(conn)
    for i in ("a", "b", "c"):
        db.insert_asset(conn, _make_asset(i))
    db.add_tag_to_asset(conn, "a", "夜")
    db.add_tag_to_asset(conn, "a", "女性")
    db.add_tag_to_asset(conn, "b", "夜")
    db.add_tag_to_asset(conn, "c", "海")
    f1 = db.create_folder(conn, "f1")
    f2 = db.create_folder(conn, "f2")
    db.add_asset_to_folder(conn, "a", f1)
    db.add_asset_to_folder(conn, "a", f2)
    db.add_asset_to_folder(conn, "b", f1)

    ids = lambda **kw: sorted(x["id"] for x in db.list_assets(conn, **kw))
    # タグ: 既定は「すべて含む」。"any"でいずれか
    assert ids(tag=["夜", "女性"]) == ["a"]
    assert ids(tag=["夜", "女性"], tag_mode="all") == ["a"]
    assert ids(tag=["夜", "海"], tag_mode="any") == ["a", "b", "c"]
    assert ids(tag=["夜", "海"], tag_mode="all") == []
    # フォルダ: 既定は「いずれか」。"all"ですべてに入っているもの
    assert ids(folder_id=[f1, f2]) == ["a", "b"]
    assert ids(folder_id=[f1, f2], folder_mode="any") == ["a", "b"]
    assert ids(folder_id=[f1, f2], folder_mode="all") == ["a"]


def test_broken_images_are_hidden_by_default_and_excluded_from_everything(tmp_path):
    conn = db.get_connection(tmp_path / "t.db")
    db.init_db(conn)
    for i in ("ok", "bad"):
        db.insert_asset(conn, _make_asset(i))
    db.set_channels(conn, "bad", ["fanvue"])
    db.set_channels(conn, "ok", ["fanvue"])
    db.enqueue_ai_task(conn, "bad", "nsfw")

    assert db.set_broken(conn, ["bad", "ok", "missing"], True) == 2
    assert db.set_broken(conn, ["bad"], True) == 0  # 変化なし
    ids = lambda **kw: sorted(x["id"] for x in db.list_assets(conn, **kw))

    assert ids() == []  # どちらも破綻にした → 既定の一覧には出ない
    db.set_broken(conn, ["ok"], False)
    assert ids() == ["ok"]  # 既定は破綻画像を含めない
    assert ids(broken="show") == ["bad", "ok"]
    assert ids(broken="only") == ["bad"]
    assert ids(channel="fanvue") == ["ok"]  # 投稿ジョブ(既定)の対象にもならない
    assert db.ai_task_counts(conn)["queued"] == 0  # 待機中のAI処理は取り消される
    assert "bad" not in db.unprocessed_asset_ids(conn, "nsfw")  # 定期実行の対象外

    # 件数(絞り込み候補)は、いま一覧に出す範囲に合わせる
    assert dict(db.facets(conn, [])["status"]) == {"ready": 1}
    assert dict(db.facets(conn, [], "show")["status"]) == {"ready": 2}
    assert dict(db.facets(conn, [], "only")["status"]) == {"ready": 1}

    # ごみ箱の中では破綻の区別をしない(復旧・完全削除の対象から漏れない)
    db.trash_assets(conn, ["bad"])
    assert ids(trashed=True) == ["bad"]


def test_init_db_upgrades_an_old_database_without_new_columns(tmp_path):
    """旧バージョンのDB(後から足した列が無い)でも、起動(init_db)で壊れず、列・テーブルが足される。

    新しい列のインデックスをschema.sqlに書くと、既存DBでは「no such column」で起動できなくなる(実際に起きた)。
    """
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE assets (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, kind TEXT NOT NULL, file_path TEXT NOT NULL,
            caption TEXT, x_caption TEXT, fanvue_text TEXT, audience TEXT, price_cents INTEGER,
            fanvue_url TEXT, fanvue_uuid TEXT, x_ok INTEGER NOT NULL DEFAULT 0, content_rating TEXT,
            content_rating_confirmed INTEGER NOT NULL DEFAULT 0, nsfw_auto_rating TEXT, nsfw_auto_confidence REAL,
            content_description TEXT, fanvue_caption_draft TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        INSERT INTO assets (id, status, kind, file_path, created_at, updated_at)
        VALUES ('old1', 'posted', 'image', '/x/old1/pic.png', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
        CREATE TABLE posts (asset_id TEXT NOT NULL, channel TEXT NOT NULL, status TEXT NOT NULL, url TEXT,
            external_id TEXT, error TEXT, posted_at TEXT NOT NULL, PRIMARY KEY (asset_id, channel));
        CREATE TABLE ai_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, asset_id TEXT NOT NULL, kind TEXT NOT NULL,
            status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, finished_at TEXT);
        """
    )
    old.commit()
    old.close()

    conn = db.get_connection(path)
    db.init_db(conn)  # ここで例外にならないこと
    db.init_db(conn)  # 何度呼んでも安全

    columns = {r["name"] for r in conn.execute("PRAGMA table_info(assets)")}
    assert {"content_hash", "width", "height", "is_broken", "original_name", "deleted_at", "wm_path"} <= columns
    assert "source" in {r["name"] for r in conn.execute("PRAGMA table_info(posts)")}
    assert "params" in {r["name"] for r in conn.execute("PRAGMA table_info(ai_tasks)")}
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"notifications", "dup_ignores"} <= tables
    indexes = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_assets_content_hash" in indexes
    assert db.get_asset(conn, "old1")["status"] == "ready"  # 旧ステータス(posted)は、移行される
