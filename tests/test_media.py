import cv2
import numpy as np
from PIL import Image

from core.media import get_media_properties, human_readable_size


def test_human_readable_size_bytes():
    assert human_readable_size(500) == "500B"


def test_human_readable_size_kb():
    assert human_readable_size(2048) == "2.0KB"


def test_human_readable_size_mb():
    assert human_readable_size(5 * 1024 * 1024) == "5.0MB"


def test_get_media_properties_missing_file_returns_none_fields(tmp_path):
    props = get_media_properties(tmp_path / "missing.jpg", "image")
    assert props["file_size_bytes"] is None
    assert props["width"] is None


def test_get_media_properties_image(tmp_path):
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (320, 240), color="blue").save(image_path)

    props = get_media_properties(image_path, "image")

    assert props["width"] == 320
    assert props["height"] == 240
    assert props["file_size_bytes"] > 0
    assert props["file_size_human"]


def _write_test_video(path, num_frames=15, fps=5, size=(64, 48)):
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, size)
    for i in range(num_frames):
        frame = np.full((size[1], size[0], 3), i % 256, dtype=np.uint8)
        writer.write(frame)
    writer.release()


def test_get_media_properties_video(tmp_path):
    video_path = tmp_path / "clip.mp4"
    _write_test_video(video_path, num_frames=15, fps=5, size=(64, 48))

    props = get_media_properties(video_path, "video")

    assert props["width"] == 64
    assert props["height"] == 48
    assert props["duration_seconds"] == 3.0
    assert props["file_size_bytes"] > 0
