from unittest.mock import MagicMock, patch

import pytest
from PIL import Image, ImageChops

from core import db, watermark, worker
from core.cli import cmd_init
from core.config import load_config

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


def _asset(config, conn, asset_id="a1", suffix=".png", kind="image", size=(400, 300), color=(40, 40, 120)):
    folder = config.paths.ready / asset_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"look{suffix}"
    if kind == "image":
        Image.new("RGB", size, color).save(path)
    else:
        path.write_bytes(b"video")
    db.insert_asset(conn, {
        "id": asset_id, "status": "ready", "kind": kind, "file_path": str(path),
        "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
    })
    return path


def _bbox(a: Image.Image, b: Image.Image):
    return ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox()


def test_render_changes_only_the_expected_region_and_is_faint():
    base = Image.new("RGB", (800, 600), (40, 40, 120))
    out = watermark.render(base, "@ai_hiyo", "bottom-right", opacity=16, size=3)
    assert out.size == base.size
    left, top, right, bottom = _bbox(base, out)
    assert left > 400 and top > 300  # 右下だけ変わる
    deltas = [abs(a - b) for pa, pb in zip(base.getdata(), out.getdata()) for a, b in zip(pa, pb)]
    assert 0 < max(deltas) < 0.3 * 255  # 不透明度16%なので、背景からの変化はごくわずか


@pytest.mark.parametrize("position,check", [
    ("top-left", lambda b, w, h: b[0] < w / 2 and b[1] < h / 2),
    ("top-right", lambda b, w, h: b[2] > w / 2 and b[1] < h / 2),
    ("bottom-left", lambda b, w, h: b[0] < w / 2 and b[3] > h / 2),
    ("center", lambda b, w, h: b[0] < w / 2 < b[2] and b[1] < h / 2 < b[3]),
])
def test_render_positions(position, check):
    base = Image.new("RGB", (800, 600), (40, 40, 120))
    out = watermark.render(base, "WM", position, opacity=30, size=4)
    assert check(_bbox(base, out), 800, 600)


def test_tile_covers_the_whole_image():
    base = Image.new("RGB", (800, 600), (40, 40, 120))
    left, top, right, bottom = _bbox(base, watermark.render(base, "WM", "tile", opacity=30, size=3))
    assert left < 100 and top < 100 and right > 600 and bottom > 450


def test_clean_params_validates_and_clamps():
    assert watermark.clean_params(" @x ", "center", 999, 0)["opacity"] == watermark.OPACITY_RANGE[1]
    assert watermark.clean_params("@x", "center", 1, 99)["size"] == watermark.SIZE_RANGE[1]
    for bad in [("", "center", 10, 3), ("x", "nowhere", 10, 3), ("x" * 101, "center", 10, 3), ("x", "center", "abc", 3)]:
        with pytest.raises(watermark.WatermarkError):
            watermark.clean_params(*bad)


def test_apply_to_file_keeps_original_and_writes_watermarked_copy(tmp_path):
    config, conn = _setup(tmp_path)
    src = _asset(config, conn)
    before = src.read_bytes()

    dst = watermark.apply_to_file(src, "@ai_hiyo", "bottom-right", 16, 3)

    assert dst.name == "watermarked.png" and dst.parent == src.parent
    assert src.read_bytes() == before  # 元のファイルは変えない
    assert _bbox(Image.open(src), Image.open(dst)) is not None
    with pytest.raises(watermark.WatermarkError):
        watermark.apply_to_file(src.with_name("clip.mp4"), "x", "center", 16, 3)  # 動画は未対応


def test_worker_runs_watermark_task_and_records_result(tmp_path):
    config, conn = _setup(tmp_path)
    src = _asset(config, conn)
    params = watermark.clean_params("@ai_hiyo", ["r0c0", "r4c4"], 16, 3)
    assert db.enqueue_ai_task(conn, "a1", "watermark", params)
    assert not db.enqueue_ai_task(conn, "a1", "watermark", params)  # 二重に積まない

    with patch("core.worker.try_create_classifier", return_value=None), patch(
        "core.generation.try_create_generator", return_value=None
    ):
        result = worker.AnalysisWorker(config).run_once(conn, log=lambda m: None)

    assert result == (1, 0)
    asset = db.get_asset(conn, "a1")
    assert asset["wm_path"] == str(src.with_name("watermarked.png"))
    assert (asset["wm_text"], asset["wm_position"]) == ("@ai_hiyo", "r0c0,r4c4")  # 複数の位置はカンマ区切りで記録
    assert db.ai_task_states(conn, ["a1"])["a1"]["watermark"]["status"] == "done"


def test_worker_reports_video_as_unsupported(tmp_path):
    config, conn = _setup(tmp_path)
    _asset(config, conn, kind="video", suffix=".mp4")
    db.enqueue_ai_task(conn, "a1", "watermark", watermark.clean_params("x", "center", 16, 3))
    result = worker.AnalysisWorker(config).run_once(conn, log=lambda m: None)
    assert result == (0, 1)
    assert "動画" in db.ai_task_states(conn, ["a1"])["a1"]["watermark"]["error"]


def test_fanvue_drop_uploads_the_watermarked_file(tmp_path):
    from posting.jobs import run_fanvue_drop

    config, conn = _setup(tmp_path)
    src = _asset(config, conn)
    db.update_asset(conn, "a1", content_rating="sfw", content_rating_confirmed=1, fanvue_text="テストの投稿文")
    db.set_channels(conn, "a1", ["fanvue"])
    out = watermark.apply_to_file(src, "@ai_hiyo", "center", 16, 3)
    db.update_asset(conn, "a1", wm_path=str(out))
    client = MagicMock()
    client.upload_media.return_value = "m1"
    client.wait_for_media_ready.return_value = True

    result = run_fanvue_drop(config, conn, client, "h", "https://f.com/{handle}")

    assert result.executed is True
    assert client.upload_media.call_args[0][0] == out  # 透かし入りを投稿する


def test_grid_5x5_positions_and_multiple_selection():
    base = Image.new("RGB", (1000, 800), (40, 40, 120))

    def box(positions):
        return _bbox(base, watermark.render(base, "WM", positions, opacity=40, size=4))

    left_top, right_bottom = box(["r0c0"]), box(["r4c4"])
    assert left_top[0] < 100 and left_top[1] < 100
    assert right_bottom[2] > 900 and right_bottom[3] > 700
    # 5x5の途中の位置(上から2段目・左から4列目)は、3x3では指定できなかった場所
    mid = box(["r1c3"])
    assert 500 < mid[0] < 800 and 100 < mid[1] < 300
    # 複数選ぶと、それぞれの場所に入る(外接矩形が両方を含む)
    both = box(["r0c0", "r4c4"])
    assert both[0] < 100 and both[1] < 100 and both[2] > 900 and both[3] > 700
    # 25か所すべて + 全面に繰り返す も指定できる
    everything = box(watermark.GRID_KEYS + ["tile"])
    assert everything[0] < 100 and everything[2] > 900


def test_positions_normalize_legacy_names_and_describe():
    assert watermark.normalize_positions("bottom-right") == ["r4c4"]  # 旧3x3の名前は5x5の同じ場所へ
    assert watermark.normalize_positions("center,top-left,center") == ["r2c2", "r0c0"]  # 重複は除く
    assert watermark.normalize_positions(["r9c9", "bogus"]) == []  # 不正な位置は捨てる
    assert watermark.describe_positions("r2c2") == "中段・中央"
    assert watermark.describe_positions(["r0c0", "r1c1", "tile"]) == "2か所＋全面に繰り返す"
    assert watermark.describe_positions("tile") == "全面に繰り返す"
    with pytest.raises(watermark.WatermarkError):
        watermark.clean_params("x", [], 16, 3)  # 位置は1か所以上


def test_default_text_is_girlaidol_and_editable_in_settings(tmp_path):
    from core import settings as settings_module

    config, conn = _setup(tmp_path)
    assert settings_module.get_watermark_defaults(conn)["text"] == "@GirlAidol"  # 初期値

    settings_module.set_watermark_defaults(conn, "@Other", ["r0c0", "tile"], 22, 4.5)
    saved = settings_module.get_watermark_defaults(conn)
    assert saved == {"text": "@Other", "positions": ["r0c0", "tile"], "opacity": 22.0, "size": 4.5}

    with pytest.raises(watermark.WatermarkError):
        settings_module.set_watermark_defaults(conn, "", ["r0c0"], 22, 4.5)  # 不正な値は保存しない
    assert settings_module.get_watermark_defaults(conn)["text"] == "@Other"
