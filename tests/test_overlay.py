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
        {"type": "stamp", "stamp": "emoji:fire", "x": 0.2, "y": 0.2, "size": 0.2, "anim": {"type": "scroll_down", "cycle": 2, "loop": False, "start": 0.5, "end": 1.8, "fade_in": 0.3, "fade_out": 0.3}},
    ])
    dest = tmp_path / "out.mp4"
    ffmpeg.overlay_video(conn, src, dest, params)
    assert ffmpeg.probe_size(dest) == (320, 240) and abs(ffmpeg.probe_duration(dest) - 2.0) < 0.3


def test_saved_styles_and_templates_and_batch_apply(env):
    config, conn, client, tmp_path = env
    # スタイル(テロップ1つの見た目)
    res = client.post("/api/overlay/styles", json={"name": "宣伝・黄色", "layer": text_layer(fill="#ffe600", stroke_color="#e60012")})
    assert res.status_code == 201 and res.get_json()["style"]["fill"] == "#ffe600" and "text" not in res.get_json()["style"]
    assert client.post("/api/overlay/styles", json={"name": "", "layer": text_layer()}).status_code == 400
    assert client.post("/api/overlay/styles", json={"name": "x", "layer": {"type": "stamp", "stamp": "preset:bar-black"}}).status_code == 400
    # 同じ名前は、上書き
    client.post("/api/overlay/styles", json={"name": "宣伝・黄色", "layer": text_layer(fill="#00ff00")})
    options = client.get("/api/overlay/options").get_json()
    assert [s["style"]["fill"] for s in options["styles"]] == ["#00ff00"]

    # テンプレート(組み合わせ全体)を保存して、複数の作品へ一括適用
    layers = [text_layer(anim={"type": "scroll_left", "fade_in": 0.5, "fade_out": 0.5}), {"type": "stamp", "stamp": "emoji:fire", "x": 0.2, "y": 0.2}]
    tpl = client.post("/api/overlay/templates", json={"name": "宣伝セット", "layers": layers}).get_json()
    assert len(tpl["layers"]) == 2 and tpl["layers"][0]["anim"]["fade_in"] == 0.5
    asset = db.get_asset(conn, "a1")
    Image.new("RGB", (100, 80)).save(asset["file_path"], format="JPEG")
    res = client.post("/api/overlay/apply-batch", json={"asset_ids": ["a1", "nope"], "template_id": tpl["id"]}).get_json()
    assert res["queued"] == 1 and res["failed"][0]["id"] == "nope"
    assert client.post("/api/overlay/apply-batch", json={"asset_ids": ["a1"], "template_id": "zzz"}).status_code == 404
    assert client.delete(f"/api/overlay/templates/{tpl['id']}").status_code == 200
    assert client.delete(f"/api/overlay/styles/{options['styles'][0]['id']}").status_code == 200
    assert client.delete("/api/overlay/templates/zzz").status_code == 404


def test_fade_filters_use_alpha_and_duration():
    layer = {"x": 0.5, "y": 0.5, "anim": {"type": "none", "cycle": 6, "loop": True, "start": 1, "end": None, "fade_in": 0.5, "fade_out": 1}}
    graph, _ = ffmpeg.overlay_filter({"layers": [layer]}, [(10, 10)], 100, 100, duration=10)
    assert "fade=t=in:st=1:d=0.5:alpha=1" in graph and "fade=t=out:st=9:d=1:alpha=1" in graph


@pytest.mark.skipif(not ffmpeg.available(), reason="ffmpegなし")
def test_edit_of_an_edit_chain_video_to_frame_to_stamp(env):
    """動画から静止画を取り出し、その加工版に、スタンプ・ぼかしをつける(加工版の加工版)。"""
    import subprocess
    config, conn, client, tmp_path = env
    video = tmp_path / "v.mp4"
    subprocess.run([ffmpeg.find("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2", "-pix_fmt", "yuv420p", str(video)], check=True)
    db.update_asset(conn, "a1", kind="video", file_path=str(video))

    def run_all():
        while (edit := edits.claim_next(conn)) is not None:
            assert edits.run_edit(conn, edit, db.get_asset(conn, "a1")) is None, edit

    frame_id = edits.enqueue_frame(conn, db.get_asset(conn, "a1"), 1.0, "png", 2)
    run_all()
    key = f"edit:{frame_id}"
    # 静止画(加工版)に、スタンプ → 画像の加工版になる
    res = client.post("/api/assets/a1/edits", json={"kind": "overlay", "source": key, "layers": [{"type": "stamp", "stamp": "preset:bar-black", "x": 0.5, "y": 0.5, "size": 0.5}]})
    assert res.status_code == 201
    run_all()
    rows = {r["key"]: r for r in __import__("core.versions", fromlist=["x"]).library_rows(conn, db.get_asset(conn, "a1"))}
    stamped = rows[f"edit:{res.get_json()['id']}"]
    assert stamped["media_type"] == "image" and stamped["state"] == "done" and "静止画" in stamped["detail"] and "から" in stamped["detail"]
    path = edits.edit_path(db.get_asset(conn, "a1"), edits.get_edit(conn, "a1", res.get_json()["id"]))
    out = Image.open(path).convert("RGB")
    assert out.size == (320, 240) and out.getpixel((160, 120)) == (0, 0, 0)  # 2倍に拡大した静止画の中央に、黒帯
    # さらに、その加工版にぼかし(加工版の加工版の加工版)
    res2 = client.post("/api/assets/a1/edits", json={"kind": "mask", "source": f"edit:{res.get_json()['id']}", "regions": [{"x": 0, "y": 0, "w": 0.5, "h": 0.5}], "style": "mosaic", "strength": 3})
    assert res2.status_code == 201
    run_all()
    assert edits.get_edit(conn, "a1", res2.get_json()["id"])["status"] == "done"
    # 切り出しは、動画だけ。静止画のもとには、できない
    assert client.post("/api/assets/a1/edits", json={"kind": "trim", "source": key, "start": 0, "end": 1}).status_code == 400
    # 処理待ちの加工のもとになっている加工版は、削除できない。使えないもとは、拒否
    res3 = client.post("/api/assets/a1/edits", json={"kind": "mask", "source": key, "regions": [{"x": 0, "y": 0, "w": 0.5, "h": 0.5}], "style": "blur"})
    assert client.delete(f"/api/assets/a1/edits/{frame_id}").status_code == 409 and res3.status_code == 201
    assert client.post("/api/assets/a1/edits", json={"kind": "mask", "source": "edit:999", "regions": [{"x": 0, "y": 0, "w": 0.5, "h": 0.5}], "style": "blur"}).status_code == 400
