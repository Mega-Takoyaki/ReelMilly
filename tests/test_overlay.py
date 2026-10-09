"""テロップ・スタンプの挿入(静止画・動画)。"""
import io
from unittest.mock import patch

import pytest
from PIL import Image

from core import db, edits, ffmpeg, fonts, overlay, stamps

from tests.test_library import env  # noqa: F401


def text_layer(**kw):
    base = {"type": "text", "text": "新作公開！\nNEW", "x": 0.5, "y": 0.8, "size": 0.08}
    return {**base, **kw}


def test_fonts_include_japanese_system_fonts(env):
    config, conn, client, tmp_path = env
    ids = {f["id"] for f in fonts.available(fonts.state_dir(conn))}
    assert "yugothic" in ids and len(ids) >= 5
    for fid in ids:
        font, _ = fonts.load(fid, 40, bold=True, directory=fonts.state_dir(conn))
        assert font.getmask("あA").getbbox() is not None  # 実際に描ける


def test_clean_params_validation(env):
    config, conn, client, tmp_path = env
    p = overlay.clean_params(conn, [text_layer(fill="#FF0000", opacity=5), {"type": "stamp", "stamp": "preset:bar-black", "size": 0.3}])
    assert p["layers"][0]["fill"] == "#ff0000" and p["layers"][0]["opacity"] == 1.0
    for bad in (
        [], [text_layer(text="  ")], [text_layer(font="nope")], [text_layer(fill="red")],
        [{"type": "stamp", "stamp": "upload:000000000000"}], [text_layer(anim={"type": "zigzag"})],
        [text_layer(anim={"type": "none", "start": 5, "end": 2})], [{"type": "x"}], [text_layer()] * 13,
    ):
        with pytest.raises(overlay.OverlayError):
            overlay.clean_params(conn, bad)


def test_render_text_layer_has_pixels_and_options_change_it(env):
    config, conn, client, tmp_path = env
    plain = overlay.render_layer(conn, overlay.clean_layer(conn, text_layer(stroke_width=0)), 800, 600)
    fancy = overlay.render_layer(conn, overlay.clean_layer(conn, text_layer(bold=True, italic=True, shadow=True, bg=True, rotation=15)), 800, 600)
    assert plain.getchannel("A").getbbox() and fancy.size != plain.size
    # 文字の高さは、画面の高さに比例する
    big = overlay.render_layer(conn, overlay.clean_layer(conn, text_layer(text="あ", size=0.2)), 800, 600)
    small = overlay.render_layer(conn, overlay.clean_layer(conn, text_layer(text="あ", size=0.1)), 800, 600)
    assert big.height > small.height * 1.6


def test_presets_and_emoji_and_upload(env):
    config, conn, client, tmp_path = env
    items = stamps.list_all(conn)
    assert sum(i["group"] == "preset" for i in items) >= 15 and sum(i["group"] == "emoji" for i in items) >= 20
    for item in items:
        assert stamps.load(conn, item["id"]).getchannel("A").getbbox(), item["id"]
    buf = io.BytesIO()
    Image.new("RGB", (30, 20), (10, 200, 10)).save(buf, format="JPEG")
    res = client.post("/api/overlay/stamps", data={"file": (io.BytesIO(buf.getvalue()), "my logo.jpg")}, content_type="multipart/form-data")
    assert res.status_code == 201 and res.get_json()["label"] == "my logo"
    uid = res.get_json()["id"]
    assert any(i["id"] == uid for i in client.get("/api/overlay/options").get_json()["stamps"])
    assert client.get(f"/api/overlay/stamps/{uid}/image").status_code == 200
    assert client.post("/api/overlay/stamps", data={"file": (io.BytesIO(b"not an image"), "x.png")}, content_type="multipart/form-data").status_code == 400
    assert client.delete(f"/api/overlay/stamps/{uid}").status_code == 200 and not stamps.exists(conn, uid)


def test_layer_preview_api(env):
    config, conn, client, tmp_path = env
    res = client.post("/api/overlay/layer-preview", json={"layer": text_layer(), "width": 500, "height": 400})
    assert res.status_code == 200 and Image.open(io.BytesIO(res.data)).mode == "RGBA"
    assert client.post("/api/overlay/layer-preview", json={"layer": text_layer(text=""), "width": 500, "height": 400}).status_code == 400


def test_apply_image_places_stamp_and_text(env):
    config, conn, client, tmp_path = env
    src = tmp_path / "base.png"
    Image.new("RGB", (400, 300), (255, 255, 255)).save(src)
    params = overlay.clean_params(conn, [{"type": "stamp", "stamp": "preset:bar-black", "x": 0.5, "y": 0.5, "size": 0.5}, text_layer(x=0.5, y=0.9)])
    dest = tmp_path / "o" / "out.png"
    overlay.apply_image(conn, src, dest, params)
    out = Image.open(dest).convert("RGB")
    assert out.getpixel((200, 150)) == (0, 0, 0)       # 中央に黒帯
    assert out.getpixel((5, 5)) == (255, 255, 255)     # 端は変わらない
    assert any(out.getpixel((x, y)) != (255, 255, 255) for x in range(100, 300, 2) for y in range(235, 299, 2))  # 下にテロップ


def test_enqueue_overlay_for_image_runs(env):
    config, conn, client, tmp_path = env
    asset = db.get_asset(conn, "a1")
    Image.new("RGB", (200, 120), (30, 30, 200)).save(asset["file_path"], format="JPEG")
    eid = edits.enqueue_overlay(conn, asset, [text_layer()])
    edit = edits.claim_next(conn)
    assert edits.run_edit(conn, edit, asset) is None
    done = edits.get_edit(conn, "a1", eid)
    assert done["status"] == "done" and "テロップ" in done["summary"] and edits.media_type(asset, done) == "image"


def test_overlay_filter_expressions():
    params = {"layers": [
        {"x": 0.5, "y": 0.5, "anim": {"type": "scroll_left", "cycle": 6, "loop": True, "start": 0, "end": None}},
        {"x": 0.5, "y": 0.2, "anim": {"type": "scroll_down", "cycle": 4, "loop": False, "start": 1, "end": None}},
        {"x": 0.3, "y": 0.8, "anim": {"type": "none", "cycle": 6, "loop": True, "start": 2, "end": 5}},
    ]}
    graph, label = ffmpeg.overlay_filter(params, [(100, 50)] * 3, 640, 480)
    assert label == "o2" and "mod((t-0),6)/6" in graph and "clip((t-1)/4,0,1)" in graph
    assert "enable='between(t,1,5)'" in graph and "enable='between(t,2,5)'" in graph and "shortest=1" in graph


@pytest.mark.skipif(not ffmpeg.available(), reason="ffmpegなし")
def test_overlay_video_end_to_end(env):
    import subprocess
    config, conn, client, tmp_path = env
    src = tmp_path / "in.mp4"
    subprocess.run([ffmpeg.find("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=2", "-pix_fmt", "yuv420p", str(src)], check=True)
    params = overlay.clean_params(conn, [
        text_layer(anim={"type": "scroll_left", "cycle": 2, "loop": True}),
        {"type": "stamp", "stamp": "emoji:fire", "x": 0.2, "y": 0.2, "size": 0.2, "anim": {"type": "scroll_down", "cycle": 2, "loop": False, "start": 0.5, "end": 1.8}},
    ])
    dest = tmp_path / "out.mp4"
    ffmpeg.overlay_video(conn, src, dest, params)
    assert ffmpeg.probe_size(dest) == (320, 240) and abs(ffmpeg.probe_duration(dest) - 2.0) < 0.3
