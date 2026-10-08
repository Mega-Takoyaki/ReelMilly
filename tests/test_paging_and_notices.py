"""一覧・ごみ箱の続き読み込み(下へスクロール)と、アップロード等の通知。"""
import pytest

from core import db, notifications
from core.web import app as app_module
from core.web.app import create_app

from tests.test_duplicates import env, png_bytes, upload  # noqa: F401  (fixtureとヘルパーを共有)


def _make_assets(client, n):
    res = upload(client, *[(f"p{i}.png", png_bytes((i % 250, (i * 7) % 250, (i * 13) % 250), (8 + i // 50, 8 + i % 50))) for i in range(n)])
    assert res["ingested"] == n
    return res


def test_index_pages_through_all_assets(env, monkeypatch):
    config, conn = env
    client = create_app(config).test_client()
    _make_assets(client, 7)
    monkeypatch.setattr(app_module, "GRID_PAGE_SIZE", 3)

    page = client.get("/").get_data(as_text=True)
    assert page.count('class="asset-card"') == 3
    assert 'id="grid-more"' in page and 'data-next="3"' in page and "全7件" in page

    seen = []
    nxt = 0
    while True:
        data = client.get(f"/?offset={nxt}&partial=cards").get_json()
        assert data["total"] == 7
        seen += [chunk.split('"')[0] for chunk in data["html"].split('data-asset-id="')[1:]]
        if data["next"] >= data["total"]:
            break
        nxt = data["next"]
    assert len(seen) == 7 and len(set(seen)) == 7  # 全件を、重複なく読み込める

    # 絞り込み(検索)も、続き読み込みに効く
    only = client.get("/?q=p3&offset=0&partial=cards").get_json()
    assert only["total"] == 1


def test_no_more_marker_when_everything_fits(env):
    config, conn = env
    client = create_app(config).test_client()
    _make_assets(client, 2)
    page = client.get("/").get_data(as_text=True)
    assert 'id="grid-more"' not in page and 'id="top-controls"' in page and 'id="grid-toolbar"' in page


def test_trash_pages_too(env, monkeypatch):
    config, conn = env
    client = create_app(config).test_client()
    res = _make_assets(client, 5)
    db.trash_assets(conn, res["asset_ids"])
    monkeypatch.setattr(app_module, "GRID_PAGE_SIZE", 2)
    page = client.get("/trash").get_data(as_text=True)
    assert page.count('class="asset-card"') == 2 and 'id="grid-more"' in page
    data = client.get("/trash?offset=2&partial=cards").get_json()
    assert data["total"] == 5 and data["next"] == 4


def test_upload_creates_a_notification(env):
    config, conn = env
    client = create_app(config).test_client()
    upload(client, ("a.png", png_bytes()), ("b.png", png_bytes((1, 2, 3))))
    item = notifications.list_notifications(conn, 5)[0]
    assert item["kind"] == "import" and "2件を取り込みました" in item["body"] and item["level"] == "success"

    upload(client, ("again.png", png_bytes()))  # 重複 → 保留。通知から確認を開ける
    item = notifications.list_notifications(conn, 5)[0]
    assert "重複のため保留 1件" in item["body"] and item["action"] == "duplicates"


def test_purging_many_creates_a_notification_but_one_does_not(env):
    config, conn = env
    client = create_app(config).test_client()
    ids = _make_assets(client, 3)["asset_ids"]
    db.trash_assets(conn, ids)
    before = notifications.count(conn)
    client.post("/api/assets/purge", json={"asset_ids": ids[:1]})
    assert notifications.count(conn) == before  # 1件だけの削除は、通知しない
    client.post("/api/assets/purge", json={"all": True})
    item = notifications.list_notifications(conn, 1)[0]
    assert item["kind"] == "trash" and "2件" in item["title"]


def test_cli_ingest_notifies_inbox_import(env):
    from core.cli import cmd_ingest

    config, conn = env
    (config.paths.inbox / "x.png").write_bytes(png_bytes())
    conn.close()
    assert cmd_ingest(config, defer_analysis=True) == 0
    conn = db.get_connection(config.paths.db_path)
    assert any(n["kind"] == "import" and "1件" in n["title"] for n in notifications.list_notifications(conn, 10))


def _card_ids(html):
    return [chunk.split('"')[0] for chunk in html.split('data-asset-id="')[1:]]


def test_sort_options(env):
    config, conn = env
    client = create_app(config).test_client()
    ids = _make_assets(client, 3)["asset_ids"]
    conn.execute("UPDATE assets SET created_at = ?, original_name = ? WHERE id = ?", ("2026-01-01T00:00:00+00:00", "b.png", ids[0]))
    conn.execute("UPDATE assets SET created_at = ?, original_name = ? WHERE id = ?", ("2026-01-02T00:00:00+00:00", "c.png", ids[1]))
    conn.execute("UPDATE assets SET created_at = ?, original_name = ? WHERE id = ?", ("2026-01-03T00:00:00+00:00", "a.png", ids[2]))
    conn.commit()

    def order(sort):
        return _card_ids(client.get(f"/?sort={sort}").get_data(as_text=True))

    assert order("created_desc") == [ids[2], ids[1], ids[0]]
    assert order("created_asc") == [ids[0], ids[1], ids[2]]
    assert order("name_asc") == [ids[2], ids[0], ids[1]]
    assert order("name_desc") == [ids[1], ids[0], ids[2]]
    assert order("nonsense") == [ids[2], ids[1], ids[0]]  # 不明な値は、既定(登録日時の新しい順)
    assert 'value="created_desc" selected' in client.get("/").get_data(as_text=True)


def test_folder_view_vs_flat_view(env):
    config, conn = env
    client = create_app(config).test_client()
    ids = _make_assets(client, 3)["asset_ids"]
    folder = db.create_folder(conn, "お気に入り")
    folder_id = folder["id"] if isinstance(folder, dict) else folder
    db.add_asset_to_folder(conn, ids[0], folder_id)

    top = client.get("/").get_data(as_text=True)
    assert 'class="folder-card"' in top and "お気に入り" in top and "1件" in top
    assert ids[0] not in _card_ids(top) and len(_card_ids(top)) == 2  # フォルダの中身は、フォルダを開くまで出ない

    flat = client.get("/?flat=1").get_data(as_text=True)
    assert 'class="folder-card"' not in flat and len(_card_ids(flat)) == 3  # フラット: 中身もすべて

    inside = client.get(f"/?folder_id={folder_id}").get_data(as_text=True)
    assert _card_ids(inside) == [ids[0]] and "の中身を表示中" in inside and 'class="folder-card"' not in inside

    searched = client.get("/?q=p").get_data(as_text=True)  # 検索中は、フォルダに入っているものも該当すれば出る
    assert len(_card_ids(searched)) == 3 and 'class="folder-card"' not in searched
    assert 'name="flat"' in top and "checked" not in top.split('name="flat"')[1].split(">")[0]


def test_full_layout_uses_row_order_with_aspect_numbers(env):
    config, conn = env
    client = create_app(config).test_client()
    _make_assets(client, 1)
    html = client.get("/").get_data(as_text=True)
    assert "--arn:" in html
    css = client.get("/static/style.css").get_data(as_text=True)
    assert ".asset-grid.is-full { display: flex; flex-wrap: wrap;" in css


def test_hidden_assets_are_left_out_of_the_default_list_only(env):
    config, conn = env
    client = create_app(config).test_client()
    ids = _make_assets(client, 3)["asset_ids"]

    res = client.post("/api/assets/hidden", json={"asset_ids": ids[:1], "hidden": True}).get_json()
    assert res["changed"] == 1
    assert client.post("/api/assets/hidden", json={"asset_ids": ids[:1], "hidden": True}).get_json()["changed"] == 0

    default = client.get("/").get_data(as_text=True)
    assert ids[0] not in _card_ids(default) and len(_card_ids(default)) == 2 and "全" not in default.split('id="grid-count"')[1][:80]
    assert ids[0] in _card_ids(client.get("/?hidden=show").get_data(as_text=True))
    only = client.get("/?hidden=only").get_data(as_text=True)
    assert _card_ids(only) == [ids[0]] and "chip-hidden" in only
    assert client.get("/trash").status_code == 200

    # ごみ箱・破綻画像とは別で、投稿・AI処理の対象には影響しない
    asset = db.get_asset(conn, ids[0])
    assert asset["deleted_at"] is None and asset["is_broken"] == 0 and asset["is_hidden"] == 1
    assert any(a["id"] == ids[0] for a in db.list_assets(conn, limit=10))  # 一覧以外(既定の呼び出し)には、影響しない

    # フォルダの件数も、非表示を除く
    folder_id = db.create_folder(conn, "f")
    db.add_asset_to_folder(conn, ids[0], folder_id)
    db.add_asset_to_folder(conn, ids[1], folder_id)
    top = client.get("/").get_data(as_text=True)
    assert "1件" in top.split('class="folder-card"')[1][:600]

    # 詳細画面から解除
    assert client.post(f"/assets/{ids[0]}/hidden").status_code == 302
    assert db.get_asset(conn, ids[0])["is_hidden"] == 0
    assert "非表示にする" in client.get(f"/assets/{ids[0]}").get_data(as_text=True)


def test_hidden_filter_and_bulk_buttons_are_on_the_list_page(env):
    config, conn = env
    client = create_app(config).test_client()
    _make_assets(client, 1)
    page = client.get("/").get_data(as_text=True)
    for needle in ('name="hidden"', 'id="hidden-badge"', 'id="bulk-hide"', 'id="bulk-unhide"'):
        assert needle in page
