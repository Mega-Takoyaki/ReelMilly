from pathlib import Path

from core.config import ensure_directories, load_config


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
  x: [sfw, suggestive, explicit]
platform_auto_post_ratings:
  fanvue: [sfw, suggestive, explicit]
  x: [sfw]
web:
  host: 127.0.0.1
  port: 8420
"""


def _write_config(tmp_path: Path) -> Path:
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    return tmp_path


def test_load_config_parses_paths_and_sections(tmp_path):
    base = _write_config(tmp_path)

    config = load_config(base_dir=base)

    assert config.timezone == "Asia/Tokyo"
    assert config.paths.root == base / "data" / "library"
    assert config.paths.inbox == base / "data" / "library" / "inbox"
    assert config.paths.db_path == base / "data" / "state" / "reelmilly.db"
    assert config.nsfw.threshold == 0.5
    assert config.platform_auto_post_ratings["x"] == ["sfw"]
    assert config.web.port == 8420
    assert config.cadence == {}


def test_load_config_parses_cadence(tmp_path):
    base = _write_config(tmp_path)
    (base / "config.yaml").write_text(CONFIG_YAML + '\ncadence:\n  drop: "21:00"\n', encoding="utf-8")

    config = load_config(base_dir=base)

    assert config.cadence == {"drop": "21:00"}


def test_load_config_missing_file_raises(tmp_path):
    try:
        load_config(base_dir=tmp_path)
    except FileNotFoundError:
        return
    raise AssertionError("expected FileNotFoundError")


def test_ensure_directories_creates_all_paths(tmp_path):
    base = _write_config(tmp_path)
    config = load_config(base_dir=base)

    created = ensure_directories(config)

    assert len(created) == len(config.paths.all_dirs())
    for path in config.paths.all_dirs():
        assert path.is_dir()

    # 2回目は何も作らない
    assert ensure_directories(config) == []
