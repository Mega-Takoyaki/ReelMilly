from unittest.mock import patch

from core import db
from core.cli import _today_str, cmd_doctor, cmd_init, cmd_run_drop
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


def test_run_drop_requires_token(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("FANVUE_API_TOKEN", raising=False)
    config = _load(tmp_path)
    cmd_init(config)
    capsys.readouterr()

    exit_code = cmd_run_drop(config)

    assert exit_code == 1
    assert "FANVUE_API_TOKEN" in capsys.readouterr().out


def test_run_drop_skips_if_already_run_today(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FANVUE_API_TOKEN", "token")
    config = _load(tmp_path)
    cmd_init(config)
    capsys.readouterr()

    conn = db.get_connection(config.paths.db_path)
    db.set_last_run_date(conn, "drop", _today_str(config.timezone))
    conn.close()

    exit_code = cmd_run_drop(config)

    assert exit_code == 0
    assert "既に実行済み" in capsys.readouterr().out


def test_run_drop_executes_job_and_records_last_run(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FANVUE_API_TOKEN", "token")
    monkeypatch.setenv("FANVUE_HANDLE", "creator")
    config = _load(tmp_path)
    cmd_init(config)
    capsys.readouterr()

    from posting.jobs import DropResult

    fake_result = DropResult(executed=True, asset_id="a1", fanvue_url="https://f.com/creator")
    with patch("posting.jobs.run_fanvue_drop", return_value=fake_result) as mocked:
        exit_code = cmd_run_drop(config)

    assert exit_code == 0
    assert "投稿成功" in capsys.readouterr().out
    mocked.assert_called_once()

    conn = db.get_connection(config.paths.db_path)
    assert db.get_last_run_date(conn, "drop") == _today_str(config.timezone)
    conn.close()


def test_run_drop_failure_returns_nonzero(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FANVUE_API_TOKEN", "token")
    config = _load(tmp_path)
    cmd_init(config)
    capsys.readouterr()

    from posting.jobs import DropResult

    fake_result = DropResult(executed=False, asset_id="a1", error="upload failed")
    with patch("posting.jobs.run_fanvue_drop", return_value=fake_result):
        exit_code = cmd_run_drop(config)

    assert exit_code == 1
    assert "投稿失敗" in capsys.readouterr().out
