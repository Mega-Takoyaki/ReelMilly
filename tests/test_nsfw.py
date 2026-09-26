from unittest.mock import patch

import cv2
import numpy as np
import pytest
from PIL import Image

from core.config import NsfwConfig
from core.nsfw import NsfwClassifier, NsfwResult, frame_sample_interval, rating_from_score


def test_rating_from_score():
    assert rating_from_score(0.9, threshold=0.5) == "nsfw"
    assert rating_from_score(0.4, threshold=0.5) == "sfw"
    assert rating_from_score(0.5, threshold=0.5) == "nsfw"  # 閾値と同値はnsfw扱い


def test_frame_sample_interval_uses_fps():
    assert frame_sample_interval(fps=30, interval_seconds=2) == 60


def test_frame_sample_interval_falls_back_when_fps_unknown():
    assert frame_sample_interval(fps=0, interval_seconds=2) == 60


def test_frame_sample_interval_never_below_one():
    assert frame_sample_interval(fps=1, interval_seconds=0.1) == 1


@pytest.fixture
def classifier():
    config = NsfwConfig(model="marqo/nsfw-image-detection-384", threshold=0.5, video_frame_interval_seconds=2)
    return NsfwClassifier(config)


def test_classify_image_path_delegates_to_pil_classification(tmp_path, classifier):
    image_path = tmp_path / "test.jpg"
    Image.new("RGB", (10, 10), color="white").save(image_path)

    with patch.object(
        classifier, "_classify_pil_image", return_value=NsfwResult(rating="nsfw", confidence=0.8)
    ) as mocked:
        result = classifier.classify_image_path(image_path)

    assert result.rating == "nsfw"
    assert result.confidence == 0.8
    mocked.assert_called_once()


def _write_test_video(path, num_frames=10, fps=10, size=(32, 32)):
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, size)
    for i in range(num_frames):
        frame = np.full((size[1], size[0], 3), i * 10 % 256, dtype=np.uint8)
        writer.write(frame)
    writer.release()


def test_classify_video_path_takes_max_score_across_sampled_frames(tmp_path):
    # fps=10, interval_seconds=0.3 -> サンプリング間隔3フレームごと。9フレームでidx=0,3,6の3回サンプルされる
    config = NsfwConfig(model="marqo/nsfw-image-detection-384", threshold=0.5, video_frame_interval_seconds=0.3)
    classifier = NsfwClassifier(config)
    video_path = tmp_path / "clip.mp4"
    _write_test_video(video_path, num_frames=9, fps=10)

    scores = iter([0.1, 0.9, 0.3])
    with patch.object(
        classifier,
        "_classify_pil_image",
        side_effect=lambda img: NsfwResult(rating="sfw", confidence=next(scores)),
    ):
        result = classifier.classify_video_path(video_path)

    assert result.confidence == 0.9
    assert result.rating == "nsfw"


def test_classify_dispatches_by_extension(tmp_path, classifier):
    image_path = tmp_path / "a.png"
    Image.new("RGB", (5, 5)).save(image_path)
    with patch.object(classifier, "classify_image_path", return_value=NsfwResult("sfw", 0.1)) as mocked:
        classifier.classify(image_path)
    mocked.assert_called_once_with(image_path)

    video_path = tmp_path / "b.mp4"
    video_path.write_bytes(b"not-a-real-video")
    with patch.object(classifier, "classify_video_path", return_value=NsfwResult("sfw", 0.1)) as mocked:
        classifier.classify(video_path)
    mocked.assert_called_once_with(video_path)
