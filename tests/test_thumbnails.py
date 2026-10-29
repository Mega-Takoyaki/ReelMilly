import os

import numpy as np
from PIL import Image

from core import thumbnails


def _image(path, size=(1200, 800), mode="RGB"):
    Image.new(mode, size, (200, 30, 30) if mode == "RGB" else (200, 30, 30, 128)).save(path)


def test_image_thumbnail_is_small_jpeg(tmp_path):
    src = tmp_path / "a.png"
    _image(src)
    out = thumbnails.ensure(tmp_path / "state", "a1", "image", src)
    assert out == tmp_path / "state" / "thumbs" / "a1.jpg"
    with Image.open(out) as im:
        assert im.format == "JPEG" and max(im.size) == thumbnails.MAX_EDGE


def test_transparent_png_becomes_rgb(tmp_path):
    src = tmp_path / "a.png"
    _image(src, mode="RGBA")
    out = thumbnails.ensure(tmp_path / "state", "a2", "image", src)
    with Image.open(out) as im:
        assert im.mode == "RGB"


def test_video_thumbnail_and_regenerates_when_source_is_newer(tmp_path):
    import cv2

    src = tmp_path / "v.mp4"
    writer = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 240))
    for _ in range(20):
        writer.write(np.full((240, 320, 3), 128, dtype=np.uint8))
    writer.release()
    out = thumbnails.ensure(tmp_path / "state", "v1", "video", src)
    assert out is not None and out.exists()
    old = out.stat().st_mtime
    os.utime(out, (old - 100, old - 100))
    assert not thumbnails.is_fresh(out, src)
    assert thumbnails.ensure(tmp_path / "state", "v1", "video", src) is not None
    assert thumbnails.is_fresh(out, src)


def test_missing_or_broken_source_returns_none(tmp_path):
    assert thumbnails.ensure(tmp_path / "s", "x", "image", tmp_path / "nope.png") is None
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    assert thumbnails.ensure(tmp_path / "s", "y", "image", bad) is None
