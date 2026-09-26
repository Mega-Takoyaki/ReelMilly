from unittest.mock import Mock

from core import db
from core.analysis import analyze_asset
from core.config import load_config
from core.nsfw import NsfwResult


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
  fanvue: [sfw, suggestive, explicit]
platform_auto_post_ratings:
  fanvue: [sfw, suggestive, explicit]
web:
  host: 127.0.0.1
  port: 8420
"""


def _make_conn(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    config = load_config(base_dir=tmp_path)
    conn = db.get_connection(config.paths.db_path)
    db.init_db(conn)
    return conn


def test_analyze_asset_succeeds_when_both_available(tmp_path):
    conn = _make_conn(tmp_path)
    media_path = tmp_path / "look.jpg"
    media_path.write_bytes(b"fake-image-bytes")

    classifier = Mock()
    classifier.classify.return_value = NsfwResult(rating="sfw", confidence=0.2)
    generator = Mock()
    generator.describe_image.return_value = "笑顔の女性"

    result = analyze_asset(conn, classifier, generator, media_path)

    assert result.success is True
    assert result.nsfw_auto_rating == "sfw"
    assert result.content_description == "笑顔の女性"
    assert result.error is None


def test_analyze_asset_fails_when_classifier_missing(tmp_path):
    conn = _make_conn(tmp_path)
    media_path = tmp_path / "look.jpg"
    media_path.write_bytes(b"fake-image-bytes")

    generator = Mock()
    generator.describe_image.return_value = "笑顔の女性"

    result = analyze_asset(conn, None, generator, media_path)

    assert result.success is False
    assert result.content_description == "笑顔の女性"
    assert "nsfw classifier unavailable" in result.error


def test_analyze_asset_fails_when_generator_missing(tmp_path):
    conn = _make_conn(tmp_path)
    media_path = tmp_path / "look.jpg"
    media_path.write_bytes(b"fake-image-bytes")

    classifier = Mock()
    classifier.classify.return_value = NsfwResult(rating="sfw", confidence=0.2)

    result = analyze_asset(conn, classifier, None, media_path)

    assert result.success is False
    assert result.nsfw_auto_rating == "sfw"
    assert "generator unavailable" in result.error


def test_analyze_asset_records_error_when_description_raises(tmp_path):
    conn = _make_conn(tmp_path)
    media_path = tmp_path / "look.jpg"
    media_path.write_bytes(b"fake-image-bytes")

    classifier = Mock()
    classifier.classify.return_value = NsfwResult(rating="sfw", confidence=0.2)
    generator = Mock()
    generator.describe_image.side_effect = RuntimeError("api error")

    result = analyze_asset(conn, classifier, generator, media_path)

    assert result.success is False
    assert "description: api error" in result.error


def test_analyze_asset_uses_configured_description_system_prompt(tmp_path):
    conn = _make_conn(tmp_path)
    db.set_setting(conn, "description_system_prompt", "カスタムプロンプト")
    media_path = tmp_path / "look.jpg"
    media_path.write_bytes(b"fake-image-bytes")

    classifier = Mock()
    classifier.classify.return_value = NsfwResult(rating="sfw", confidence=0.2)
    generator = Mock()
    generator.describe_image.return_value = "説明"

    analyze_asset(conn, classifier, generator, media_path)

    generator.describe_image.assert_called_once_with(media_path, "カスタムプロンプト")
