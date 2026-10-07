from PIL import Image

from core import db
from core.cli import cmd_init
from core.config import load_config
from core.dimensions import backfill_dimensions, read_dimensions
from core.ingest import ingest_inbox
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


def _setup(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    config = load_config(base_dir=tmp_path)
    cmd_init(config)
    return config, db.get_connection(config.paths.db_path)


def test_read_dimensions_for_images_and_broken_files(tmp_path):
    img = tmp_path / "a.png"
    Image.new("RGB", (300, 500)).save(img)
    assert read_dimensions(img, "image") == (300, 500)
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    assert read_dimensions(bad, "image") is None  # 壊れたファイルでも、例外にしない
    assert read_dimensions(tmp_path / "none.png", "image") is None


def test_exif_rotation_swaps_width_and_height(tmp_path):
    """スマホの縦写真など、EXIFで90度回転して表示される画像は、表示される向きの縦横を返す。"""
    img = Image.new("RGB", (400, 200))
    exif = img.getexif()
    exif[274] = 6  # 90度回転
    path = tmp_path / "rot.jpg"
    img.save(path, exif=exif)
    assert read_dimensions(path, "image") == (200, 400)


def test_ingest_records_dimensions_and_backfill_fills_old_assets(tmp_path):
    config, conn = _setup(tmp_path)
    Image.new("RGB", (640, 360)).save(config.paths.inbox / "wide.png")
    asset_id = ingest_inbox(config, conn, defer_analysis=True)[0].asset_id
    asset = db.get_asset(conn, asset_id)
    assert (asset["width"], asset["height"]) == (640, 360)  # 取り込み時に記録される

    conn.execute("UPDATE assets SET width = NULL, height = NULL")  # 旧バージョンで取り込んだ状態
    conn.commit()
    assert backfill_dimensions(conn) == 1
    assert (db.get_asset(conn, asset_id)["width"], db.get_asset(conn, asset_id)["height"]) == (640, 360)


def test_full_mode_toggle_and_aspect_ratio_in_page(tmp_path):
    config, conn = _setup(tmp_path)
    Image.new("RGB", (300, 600)).save(config.paths.inbox / "tall.png")
    asset_id = ingest_inbox(config, conn, defer_analysis=True)[0].asset_id
    conn.execute("UPDATE assets SET width = NULL, height = NULL WHERE id = ?", (asset_id,))  # 未記録 → 表示時に補われる
    conn.commit()

    client = create_app(config).test_client()
    body = client.get("/").get_data(as_text=True)
    assert 'style="--ar: 300 / 600; --arn: 0.5"' in body  # 縦横比が、カードに渡される(読み込み前から枠を確保)
    assert 'id="thumb-full-toggle"' in body and "コンパクト" in body and "フル" in body  # スライダーの右のトグル
    assert body.index('id="thumb-size-slider"') < body.index('id="thumb-full-toggle"')
    assert (db.get_asset(conn, asset_id)["width"], db.get_asset(conn, asset_id)["height"]) == (300, 600)  # 補った値は保存される
    assert 'id="thumb-full-toggle"' in client.get("/trash").get_data(as_text=True)  # ごみ箱にも同じ操作がある
