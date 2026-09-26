from unittest.mock import Mock

from core import db
from core.config import load_config
from core.ingest import ingest_inbox
from core.nsfw import NsfwResult


def _mock_generator(description="赤いドレスの女性が微笑んでいる"):
    generator = Mock()
    generator.describe_image.return_value = description
    return generator


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


def _setup(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    config = load_config(base_dir=tmp_path)
    config.paths.inbox.mkdir(parents=True, exist_ok=True)
    conn = db.get_connection(config.paths.db_path)
    db.init_db(conn)
    return config, conn


def test_ingest_moves_file_without_sidecar(tmp_path):
    config, conn = _setup(tmp_path)
    (config.paths.inbox / "look-a.jpg").write_bytes(b"fake-image-bytes")

    results = ingest_inbox(config, conn)

    assert len(results) == 1
    result = results[0]
    assert result.kind == "image"
    assert result.dest_path.exists()
    assert not (config.paths.inbox / "look-a.jpg").exists()

    asset = db.get_asset(conn, result.asset_id)
    assert asset["status"] == "analyzing"  # NSFW仕分け・内容説明とも未実行のため(ADR-0015)
    assert asset["kind"] == "image"
    assert set(db.get_channels(conn, result.asset_id)) == {"fanvue", "x"}


def test_ingest_merges_sidecar_and_removes_it(tmp_path):
    config, conn = _setup(tmp_path)
    (config.paths.inbox / "clip.mp4").write_bytes(b"fake-video-bytes")
    (config.paths.inbox / "clip.yaml").write_text(
        """
caption: "テストキャプション"
x_caption: "Xの紹介文"
audience: subscribers
price_cents: 499
channels: [x]
tags: [推し, 夏]
""",
        encoding="utf-8",
    )

    results = ingest_inbox(config, conn)

    assert len(results) == 1
    asset_id = results[0].asset_id
    asset = db.get_asset(conn, asset_id)
    assert asset["caption"] == "テストキャプション"
    assert asset["x_caption"] == "Xの紹介文"
    assert asset["price_cents"] == 499
    assert set(db.get_channels(conn, asset_id)) == {"x"}
    assert set(db.list_tags_for_asset(conn, asset_id)) == {"推し", "夏"}

    assert not (config.paths.inbox / "clip.yaml").exists()


def test_ingest_records_nsfw_auto_rating_when_classifier_given(tmp_path):
    config, conn = _setup(tmp_path)
    (config.paths.inbox / "look-a.jpg").write_bytes(b"fake-image-bytes")

    classifier = Mock()
    classifier.classify.return_value = NsfwResult(rating="nsfw", confidence=0.87)

    results = ingest_inbox(config, conn, nsfw_classifier=classifier)

    asset = db.get_asset(conn, results[0].asset_id)
    assert asset["nsfw_auto_rating"] == "nsfw"
    assert asset["nsfw_auto_confidence"] == 0.87
    classifier.classify.assert_called_once_with(results[0].dest_path)


def test_ingest_without_classifier_leaves_nsfw_fields_null(tmp_path):
    config, conn = _setup(tmp_path)
    (config.paths.inbox / "look-a.jpg").write_bytes(b"fake-image-bytes")

    results = ingest_inbox(config, conn)

    asset = db.get_asset(conn, results[0].asset_id)
    assert asset["nsfw_auto_rating"] is None
    assert asset["nsfw_auto_confidence"] is None
    assert asset["status"] == "analyzing"


def test_ingest_becomes_ready_when_nsfw_and_description_both_succeed(tmp_path):
    config, conn = _setup(tmp_path)
    (config.paths.inbox / "look-a.jpg").write_bytes(b"fake-image-bytes")

    classifier = Mock()
    classifier.classify.return_value = NsfwResult(rating="sfw", confidence=0.12)
    generator = _mock_generator("赤いドレスの女性が微笑んでいる")

    results = ingest_inbox(config, conn, nsfw_classifier=classifier, generator=generator)

    asset = db.get_asset(conn, results[0].asset_id)
    assert asset["status"] == "ready"
    assert asset["content_description"] == "赤いドレスの女性が微笑んでいる"


def test_ingest_stays_analyzing_when_only_description_succeeds(tmp_path):
    config, conn = _setup(tmp_path)
    (config.paths.inbox / "look-a.jpg").write_bytes(b"fake-image-bytes")

    generator = _mock_generator()

    results = ingest_inbox(config, conn, nsfw_classifier=None, generator=generator)

    asset = db.get_asset(conn, results[0].asset_id)
    assert asset["status"] == "analyzing"
    assert asset["content_description"] is not None


def test_ingest_stays_analyzing_when_only_nsfw_succeeds(tmp_path):
    config, conn = _setup(tmp_path)
    (config.paths.inbox / "look-a.jpg").write_bytes(b"fake-image-bytes")

    classifier = Mock()
    classifier.classify.return_value = NsfwResult(rating="sfw", confidence=0.12)

    results = ingest_inbox(config, conn, nsfw_classifier=classifier, generator=None)

    asset = db.get_asset(conn, results[0].asset_id)
    assert asset["status"] == "analyzing"
    assert asset["content_description"] is None


def test_ingest_ignores_unknown_extensions(tmp_path):
    config, conn = _setup(tmp_path)
    (config.paths.inbox / "notes.txt").write_text("これはメディアではない", encoding="utf-8")

    results = ingest_inbox(config, conn)

    assert results == []
    assert (config.paths.inbox / "notes.txt").exists()
