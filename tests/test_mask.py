"""ぼかし・モザイク(静止画・動画)。"""
from unittest.mock import patch

import pytest
from PIL import Image

from core import db, edits, ffmpeg, mask, versions

from tests.test_library import env  # noqa: F401


def test_clean_params_validates_and_clamps():
    p = mask.clean_params([{"x": -0.2, "y": 0.1, "w": 0.5, "h": 0.4}, {"x": 0.5, "y": 0.5, "w": 0.001, "h": 0.5}], "blur", 7)
    assert p["strength"] == 7 and len(p["regions"]) == 1 and p["regions"][0]["x"] == 0.0 and p["regions"][0]["w"] == 0.3
    for bad in (
        lambda: mask.clean_params([], "blur", 5),
        lambda: mask.clean_params([{"x": 0, "y": 0, "w": 1, "h": 1}], "x", 5),
        lambda: mask.clean_params([{"x": 0, "y": 0, "w": 1, "h": 1}], "blur", 11),
        lambda: mask.clean_params([{"x": 0.5, "y": 0.5, "w": 0.001, "h": 0.001}], "blur", 5),
        lambda: mask.clean_params([{"x": 0, "y": 0, "w": 0.2, "h": 0.2}] * 9, "blur", 5),
    ):
        with pytest.raises(mask.MaskError):
            bad()


@pytest.mark.parametrize("style", ["blur", "mosaic"])
def test_apply_image_changes_only_the_region(tmp_path, style):
    src = tmp_path / "src.png"
    img = Image.new("RGB", (200, 100))
    for x in range(200):
        for y in range(100):
            img.putpixel((x, y), ((x * 7) % 256, (y * 13) % 256, ((x + y) * 5) % 256))
    img.save(src)
    dest = tmp_path / "out" / "dest.png"
    params = mask.clean_params([{"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}], style, 5)
    mask.apply_image(src, dest, params)
    out, base = Image.open(dest), Image.open(src)
    assert out.size == base.size
    assert out.getpixel((5, 5)) == base.getpixel((5, 5))        # 範囲の外は、変わらない
    assert out.getpixel((195, 95)) == base.getpixel((195, 95))
    inside = [(x, y) for x in range(55, 145, 7) for y in range(30, 70, 7)]
    assert any(out.getpixel(p) != base.getpixel(p) for p in inside)  # 範囲の中は、変わる


def test_enqueue_mask_for_image_runs_and_appears_in_library(env):
    config, conn, client, tmp_path = env
    asset = db.get_asset(conn, "a1")
    Image.new("RGB", (120, 80), (200, 30, 30)).save(asset["file_path"], format="JPEG")
    edit_id = edits.enqueue_mask(conn, asset, [{"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.4}], "mosaic", 4)
    edit = edits.claim_next(conn)
    assert edit["id"] == edit_id and edits.run_edit(conn, edit, asset) is None
    done = edits.get_edit(conn, "a1", edit_id)
    assert done["status"] == "done" and done["filename"].endswith(asset["file_path"][asset["file_path"].rfind("."):])
    rows = {r["key"]: r for r in versions.library_rows(conn, asset)}
    row = rows[f"edit:{edit_id}"]
    assert row["label"] == "モザイク" and row["media_type"] == "image" and row["exists"]
    assert any(v["key"] == f"edit:{edit_id}" and v["media_type"] == "image" for v in versions.list_versions(conn, asset))
    path, mt = versions.resolve(conn, asset, f"edit:{edit_id}")
    assert mt == "image" and path.exists()


def test_api_creates_image_mask_without_ffmpeg(env, monkeypatch):
    config, conn, client, tmp_path = env
    asset = db.get_asset(conn, "a1")
    Image.new("RGB", (60, 40)).save(asset["file_path"], format="JPEG")
    monkeypatch.setattr(ffmpeg, "available", lambda: False)
    res = client.post("/api/assets/a1/edits", json={"kind": "mask", "regions": [{"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5}], "style": "blur", "strength": 5})
    assert res.status_code == 201
    bad = client.post("/api/assets/a1/edits", json={"kind": "mask", "regions": [], "style": "blur"})
    assert bad.status_code == 400 and "範囲" in bad.get_json()["error"]


def test_mask_filter_graph_is_even_aligned():
    params = mask.clean_params([{"x": 0.11, "y": 0.13, "w": 0.3, "h": 0.2}], "mosaic", 5)
    graph, label = ffmpeg.mask_filter(params, 464, 688)
    assert label == "o0" and "overlay=" in graph and "flags=neighbor" in graph
    import re
    nums = [int(n) for n in re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", graph)[0]]
    assert all(n % 2 == 0 for n in nums)


@pytest.mark.skipif(not ffmpeg.available(), reason="ffmpegなし")
def test_mask_video_end_to_end(tmp_path):
    import subprocess
    src = tmp_path / "in.mp4"
    subprocess.run([ffmpeg.find("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1", "-pix_fmt", "yuv420p", str(src)], check=True)
    dest = tmp_path / "out.mp4"
    for style in ("blur", "mosaic"):
        ffmpeg.mask_video(src, dest, mask.clean_params([{"x": 0.2, "y": 0.2, "w": 0.5, "h": 0.5}], style, 5))
        assert dest.exists() and ffmpeg.probe_size(dest) == (160, 120)
        dest.unlink()


def test_frame_validation_and_summary(env):
    config, conn, client, tmp_path = env
    video = db.get_asset(conn, "a1")
    conn.execute("UPDATE assets SET kind = 'video' WHERE id = 'a1'")
    conn.commit()
    video = db.get_asset(conn, "a1")
    for bad in (dict(at=-1, fmt="jpg", scale=1), dict(at=1, fmt="gif", scale=1), dict(at=1, fmt="png", scale=9), dict(at="x", fmt="jpg", scale=1)):
        with pytest.raises(edits.EditError):
            edits.enqueue_frame(conn, video, **bad)
    with patch("core.ffmpeg.probe_duration", return_value=10.0):
        edit_id = edits.enqueue_frame(conn, video, at=2.5, fmt="png", scale=2)
    edit = edits.get_edit(conn, "a1", edit_id)
    assert edit["summary"] == "静止画 0:02.5 PNG（2倍に拡大）" and edits.media_type(video, edit) == "image"
    assert edits.kind_label(edit) == "静止画"


@pytest.mark.skipif(not ffmpeg.available(), reason="ffmpegなし")
@pytest.mark.parametrize("suffix,scale,expected", [(".jpg", 1, (160, 120)), (".png", 2, (320, 240))])
def test_extract_frame_end_to_end(tmp_path, suffix, scale, expected):
    import subprocess
    src = tmp_path / "in.mp4"
    subprocess.run([ffmpeg.find("ffmpeg"), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2", "-pix_fmt", "yuv420p", str(src)], check=True)
    dest = tmp_path / f"frame{suffix}"
    ffmpeg.extract_frame(src, dest, 1.0, scale)
    assert Image.open(dest).size == expected
    with pytest.raises(ffmpeg.FfmpegError):
        ffmpeg.extract_frame(src, tmp_path / f"late{suffix}", 99.0)
