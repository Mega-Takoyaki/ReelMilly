import pytest

from core import db, settings


@pytest.fixture
def conn(tmp_path):
    connection = db.get_connection(tmp_path / "reelmilly.db")
    db.init_db(connection)
    yield connection
    connection.close()


def test_defaults_are_returned_when_unset(conn):
    assert settings.get_description_system_prompt(conn) == settings.DEFAULT_DESCRIPTION_SYSTEM_PROMPT
    assert settings.get_caption_system_prompt(conn) == settings.DEFAULT_CAPTION_SYSTEM_PROMPT
    assert settings.get_generation_provider(conn) == "claude"
    assert settings.get_generation_model(conn) == "claude-opus-5"
    assert settings.get_caption_mode(conn) == "auto"


def test_update_settings_overrides_defaults(conn):
    settings.update_settings(
        conn,
        description_system_prompt="カスタム説明プロンプト",
        caption_system_prompt="カスタムキャプションプロンプト",
        generation_provider="openai",
        generation_model="gpt-4o-mini",
        caption_mode="draft",
    )

    assert settings.get_description_system_prompt(conn) == "カスタム説明プロンプト"
    assert settings.get_caption_system_prompt(conn) == "カスタムキャプションプロンプト"
    assert settings.get_generation_provider(conn) == "openai"
    assert settings.get_generation_model(conn) == "gpt-4o-mini"
    assert settings.get_caption_mode(conn) == "draft"


def test_generation_model_follows_provider_default_when_model_unset(conn):
    settings.update_settings(conn, generation_provider="openai")

    assert settings.get_generation_model(conn) == "gpt-4o"


def test_get_all_settings_returns_dict(conn):
    result = settings.get_all_settings(conn)

    assert result == {
        "description_system_prompt": settings.DEFAULT_DESCRIPTION_SYSTEM_PROMPT,
        "caption_system_prompt": settings.DEFAULT_CAPTION_SYSTEM_PROMPT,
        "generation_provider": "claude",
        "generation_model": "claude-opus-5",
        "caption_mode": "auto",
    }


def test_update_settings_ignores_none_and_empty_values(conn):
    settings.update_settings(conn, caption_mode="draft")
    settings.update_settings(conn, caption_mode=None)
    settings.update_settings(conn, caption_mode="")

    assert settings.get_caption_mode(conn) == "draft"
