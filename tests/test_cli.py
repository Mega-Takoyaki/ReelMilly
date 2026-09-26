from unittest.mock import patch

from core.cli import cmd_doctor, cmd_init
from core.config import load_config


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


def _load(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    return load_config(base_dir=tmp_path)


def test_doctor_reports_missing_before_init(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FANVUE_API_TOKEN", raising=False)
    config = _load(tmp_path)

    exit_code = cmd_doctor(config)

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "MISSING" in out


def test_init_then_doctor_is_ok(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FANVUE_API_TOKEN", raising=False)
    config = _load(tmp_path)

    init_exit_code = cmd_init(config)
    assert init_exit_code == 0

    doctor_exit_code = cmd_doctor(config)
    assert doctor_exit_code == 0
    out = capsys.readouterr().out
    assert "MISSING" not in out
    assert "database" in out
    assert "トークン未設定のためスキップ" in out

    assert config.paths.events_path.exists()


def test_doctor_checks_fanvue_when_token_present(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FANVUE_API_TOKEN", "test-token")
    config = _load(tmp_path)
    cmd_init(config)
    capsys.readouterr()

    with patch("posting.fanvue.FanvueClient.get_me", return_value={"id": "u1"}):
        exit_code = cmd_doctor(config)

    assert exit_code == 0
    assert "Fanvue API: OK" in capsys.readouterr().out


def test_doctor_reports_fanvue_failure(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FANVUE_API_TOKEN", "bad-token")
    config = _load(tmp_path)
    cmd_init(config)
    capsys.readouterr()

    with patch("posting.fanvue.FanvueClient.get_me", side_effect=RuntimeError("401 unauthorized")):
        exit_code = cmd_doctor(config)

    assert exit_code == 1
    assert "Fanvue API: NG" in capsys.readouterr().out
