from unittest.mock import patch

import cv2
import numpy as np
import pytest
from PIL import Image

from core.config import NsfwConfig
from core.nsfw import (
    NsfwClassifier, NsfwResult, combine_exposure, decide_rating, frame_sample_interval, needs_review, rating_from_score, top_k_mean,
)


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


def test_classify_image_path_combines_score_and_exposure(tmp_path, classifier):
    image_path = tmp_path / "test.jpg"
    Image.new("RGB", (10, 10), color="white").save(image_path)

    with patch.object(classifier, "_analyze_frame", return_value=(0.8, (False, False))) as mocked:
        result = classifier.classify_image_path(image_path)
    assert (result.rating, result.confidence, result.exposure) == ("suggestive", 0.8, None)  # 露出なし・スコア高 → suggestive
    mocked.assert_called_once()

    with patch.object(classifier, "_analyze_frame", return_value=(0.3, (True, False))):
        assert classifier.classify_image_path(image_path).rating == "explicit"  # 性器・肛門 → スコアが低くてもexplicit
    with patch.object(classifier, "_analyze_frame", return_value=(0.3, None)):  # 検出器なし → スコアだけ
        assert classifier.classify_image_path(image_path).rating == "sfw"


def test_decide_rating_matrix():
    t = 0.5
    assert decide_rating(0.1, None, t) == "sfw"
    assert decide_rating(0.5, None, t) == "suggestive"
    assert decide_rating(0.99, None, t) == "suggestive"      # 露出が見つからなければ、スコアが高くてもsuggestive
    assert decide_rating(0.1, "genital", t) == "explicit"
    assert decide_rating(0.9, "chest", t) == "explicit"
    assert decide_rating(0.6, "chest", t) == "suggestive"    # 胸・臀部で、スコアが高くなければsuggestive


def test_needs_review_band():
    assert needs_review(0.2) and needs_review(0.79) and needs_review(0.5)
    assert not needs_review(0.19) and not needs_review(0.8) and not needs_review(None)


def test_top_k_mean_and_combine_exposure():
    assert top_k_mean([0.1, 0.9, 0.3]) == pytest.approx((0.9 + 0.3 + 0.1) / 3)
    assert top_k_mean([0.1, 0.9, 0.3, 0.2, 0.8]) == pytest.approx((0.9 + 0.8 + 0.3) / 3)  # 上位3つ
    assert top_k_mean([]) == 0.0
    assert combine_exposure([(True, False), (False, False)]) == "genital"            # 少ないコマ数: 1コマで
    assert combine_exposure([(True, False)] + [(False, False)] * 5) is None            # 多いコマ数: 1コマだけなら、誤検出とみなす
    assert combine_exposure([(True, False)] * 2 + [(False, False)] * 5) == "genital"
    assert combine_exposure([(False, True)] * 2 + [(False, False)] * 5) == "chest"
    assert combine_exposure([]) is None


def _write_test_video(path, num_frames=10, fps=10, size=(32, 32)):
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, size)
    for i in range(num_frames):
        frame = np.full((size[1], size[0], 3), i * 10 % 256, dtype=np.uint8)
        writer.write(frame)
    writer.release()


def test_classify_video_path_uses_top_frames_mean_not_the_max(tmp_path):
    # fps=10, interval_seconds=0.3 -> サンプリング間隔3フレームごと。9フレームでidx=0,3,6の3回サンプルされる
    config = NsfwConfig(model="marqo/nsfw-image-detection-384", threshold=0.5, video_frame_interval_seconds=0.3)
    classifier = NsfwClassifier(config)
    video_path = tmp_path / "clip.mp4"
    _write_test_video(video_path, num_frames=9, fps=10)

    scores = iter([0.1, 0.9, 0.3])
    with patch.object(
        classifier,
        "_analyze_frame",
        side_effect=lambda img: (next(scores), (False, False)),
    ):
        result = classifier.classify_video_path(video_path)

    assert result.confidence == pytest.approx((0.1 + 0.9 + 0.3) / 3)  # 1コマだけ高くても、動画全体は引きずられない(最大ではなく上位の平均)
    assert result.rating == "sfw"


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


def test_set_auto_rating_tag_replaces_previous_auto_tags(tmp_path):
    from core import db
    from tests.test_worker import _insert, _setup

    config, conn = _setup(tmp_path)
    _insert(conn, tmp_path, "a1")
    db.add_tag_to_asset(conn, "a1", "nsfw")      # 旧形式の自動タグ
    db.add_tag_to_asset(conn, "a1", "red dress")  # 利用者・AIが付けたタグは、残す
    db.set_auto_rating_tag(conn, "a1", "suggestive")
    assert sorted(db.list_tags_for_asset(conn, "a1")) == ["red dress", "suggestive"]
    db.set_auto_rating_tag(conn, "a1", "sfw")
    assert sorted(db.list_tags_for_asset(conn, "a1")) == ["red dress", "sfw"]


def test_review_filter_and_facet(tmp_path):
    from core import db
    from tests.test_worker import _insert, _setup

    config, conn = _setup(tmp_path)
    for i, (rating, conf) in enumerate([("sfw", 0.05), ("suggestive", 0.5), ("explicit", 0.95), ("suggestive", 0.3)]):
        _insert(conn, tmp_path, f"a{i}", nsfw_auto_rating=rating, nsfw_auto_confidence=conf)
    ids = lambda **kw: sorted(a["id"] for a in db.list_assets(conn, **kw))
    assert ids(nsfw_auto=["review"]) == ["a1", "a3"]                     # スコアが迷う帯(0.2〜0.8)
    assert ids(nsfw_auto=["explicit", "review"]) == ["a1", "a2", "a3"]   # 他の値との組み合わせ(OR)
    assert ids(nsfw_auto=["sfw"]) == ["a0"]
    assert ("review", 2) in db.facets(conn, ["fanvue", "x"])["nsfw_auto"]
