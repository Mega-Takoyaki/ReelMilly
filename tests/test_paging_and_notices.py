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
