import os

from core import env_settings


def test_read_connection_status_missing_file_returns_all_unset(tmp_path):
    env_path = tmp_path / ".env"

    result = env_settings.read_connection_status(env_path)

    assert result["FANVUE_API_TOKEN"]["is_set"] is False
    assert result["FANVUE_API_TOKEN"]["value"] == ""
    assert result["FANVUE_HANDLE"]["is_set"] is False


def test_read_connection_status_hides_secret_values(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("FANVUE_API_TOKEN=super-secret\nFANVUE_HANDLE=creator\n", encoding="utf-8")

    result = env_settings.read_connection_status(env_path)

    assert result["FANVUE_API_TOKEN"]["secret"] is True
    assert result["FANVUE_API_TOKEN"]["is_set"] is True
    assert result["FANVUE_API_TOKEN"]["value"] == ""  # 秘密情報は値を返さない
    assert result["FANVUE_HANDLE"]["secret"] is False
    assert result["FANVUE_HANDLE"]["value"] == "creator"


def test_update_connection_values_writes_new_file(tmp_path):
    env_path = tmp_path / ".env"

    env_settings.update_connection_values(env_path, {"FANVUE_API_TOKEN": "abc123", "FANVUE_HANDLE": "creator"})

    assert env_path.exists()
    result = env_settings.read_connection_status(env_path)
    assert result["FANVUE_API_TOKEN"]["is_set"] is True
    assert result["FANVUE_HANDLE"]["value"] == "creator"


def test_update_connection_values_blank_does_not_overwrite_existing(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("FANVUE_API_TOKEN=existing-token\n", encoding="utf-8")

    env_settings.update_connection_values(env_path, {"FANVUE_API_TOKEN": "", "FANVUE_HANDLE": "creator"})

    result = env_settings.read_connection_status(env_path)
    assert result["FANVUE_API_TOKEN"]["is_set"] is True  # 空欄送信では消えない
    assert result["FANVUE_HANDLE"]["value"] == "creator"


def test_update_connection_values_ignores_unknown_keys(tmp_path):
    env_path = tmp_path / ".env"

    env_settings.update_connection_values(env_path, {"NOT_A_CONNECTION_KEY": "value"})

    assert env_settings.read_connection_status(env_path)["FANVUE_API_TOKEN"]["is_set"] is False


def test_update_connection_values_reloads_into_process_environ(tmp_path, monkeypatch):
    monkeypatch.delenv("FANVUE_HANDLE", raising=False)
    env_path = tmp_path / ".env"

    env_settings.update_connection_values(env_path, {"FANVUE_HANDLE": "creator"})

    assert os.environ.get("FANVUE_HANDLE") == "creator"
