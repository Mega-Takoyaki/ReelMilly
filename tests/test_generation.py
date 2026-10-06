from pathlib import Path
from unittest.mock import MagicMock, patch

from core import db, generation
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


def _make_conn(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    config = load_config(base_dir=tmp_path)
    conn = db.get_connection(config.paths.db_path)
    db.init_db(conn)
    return conn


def _fake_text_response(text):
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


def test_claude_generator_describe_image_returns_text(tmp_path):
    image_path = tmp_path / "look.jpg"
    image_path.write_bytes(b"fake-image-bytes")

    with patch("anthropic.Anthropic") as mock_anthropic_cls:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _fake_text_response("赤いドレスを着た女性が微笑んでいる")
        mock_anthropic_cls.return_value = mock_client

        generator = generation.ClaudeGenerator(api_key="test-key", model="claude-opus-5")
        result = generator.describe_image(image_path, "説明してください")

    assert result.description == "赤いドレスを着た女性が微笑んでいる"
    assert result.suggested_tags == []
    mock_client.messages.create.assert_called_once()
    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-opus-5"
    assert call_kwargs["system"] == "説明してください"


def test_claude_generator_generate_caption_returns_text(tmp_path):
    with patch("anthropic.Anthropic") as mock_anthropic_cls:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _fake_text_response("今日の一枚です")
        mock_anthropic_cls.return_value = mock_client

        generator = generation.ClaudeGenerator(api_key="test-key")
        result = generator.generate_caption("赤いドレスの女性", "投稿文を作って")

    assert result == "今日の一枚です"


def test_parse_description_response_extracts_description_and_tags():
    text = "説明: 赤いドレスを着た女性が屋外で微笑んでいる。\nタグ: 赤, ドレス, 屋外, 笑顔"

    result = generation._parse_description_response(text)

    assert result.description == "赤いドレスを着た女性が屋外で微笑んでいる。"
    assert result.suggested_tags == ["赤", "ドレス", "屋外", "笑顔"]


def test_parse_description_response_handles_full_width_comma():
    text = "説明: 説明文\nタグ: タグ1、タグ2"

    result = generation._parse_description_response(text)

    assert result.suggested_tags == ["タグ1", "タグ2"]


def test_parse_description_response_limits_to_max_tags():
    text = "説明: 説明文\nタグ: " + ", ".join("t%d" % i for i in range(20))

    result = generation._parse_description_response(text)

    assert len(result.suggested_tags) == generation.MAX_SUGGESTED_TAGS


def test_parse_description_response_extracts_values_from_category_format():
    text = "説明: 説明文\nタグ: 服装=水着, 性別＝女性, 背景=不明"

    result = generation._parse_description_response(text)

    assert result.suggested_tags == ["水着", "女性"]  # 「不明」は除外し、カテゴリ名は落とす


def test_parse_description_response_without_tag_line_returns_whole_text_as_description():
    text = "特にフォーマットに従わない自由な説明文です。"

    result = generation._parse_description_response(text)

    assert result.description == text
    assert result.suggested_tags == []


def test_claude_generator_describe_image_parses_tags_from_response(tmp_path):
    image_path = tmp_path / "look.jpg"
    image_path.write_bytes(b"fake-image-bytes")

    with patch("anthropic.Anthropic") as mock_anthropic_cls:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _fake_text_response(
            "説明: 赤いドレスの女性が微笑んでいる\nタグ: 赤, ドレス, 屋外"
        )
        mock_anthropic_cls.return_value = mock_client

        generator = generation.ClaudeGenerator(api_key="test-key")
        result = generator.describe_image(image_path, "説明してください")

    assert result.description == "赤いドレスの女性が微笑んでいる"
    assert result.suggested_tags == ["赤", "ドレス", "屋外"]


def test_claude_generator_wraps_errors_as_generation_error(tmp_path):
    image_path = tmp_path / "look.jpg"
    image_path.write_bytes(b"fake-image-bytes")

    with patch("anthropic.Anthropic") as mock_anthropic_cls:
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("500 server error")
        mock_anthropic_cls.return_value = mock_client

        generator = generation.ClaudeGenerator(api_key="test-key")
        try:
            generator.describe_image(image_path, "説明してください")
            assert False, "expected GenerationError"
        except generation.GenerationError as exc:
            assert "500 server error" in str(exc)


def test_try_create_generator_returns_none_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    conn = _make_conn(tmp_path)

    assert generation.try_create_generator(conn) is None


def test_try_create_generator_returns_claude_generator_with_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    conn = _make_conn(tmp_path)

    with patch("anthropic.Anthropic"):
        result = generation.try_create_generator(conn)

    assert isinstance(result, generation.ClaudeGenerator)


def test_try_create_generator_returns_none_for_unknown_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    conn = _make_conn(tmp_path)
    db.set_setting(conn, "generation_provider", "bedrock")

    assert generation.try_create_generator(conn) is None


def _make_local_generator():
    """torch/transformersの実インストールなしに、ロード済みインスタンスを模倣する。

    実際のモデルロード(__init__)はtorch/transformersを要求するため、実機での
    動作検証が別途必要(TODO.md参照)。ここでは`_generate`以降のロジック
    (メッセージ構築・テンソルのスライス・エラーハンドリング)のみを検証する。
    """
    generator = generation.LocalVlmGenerator.__new__(generation.LocalVlmGenerator)
    generator._device = "cpu"
    generator._torch = MagicMock()

    fake_input_ids = MagicMock()
    fake_input_ids.shape = (1, 5)

    class _FakeBatchEncoding(dict):
        def to(self, device):
            return self

    class _FakeOutputIds:
        def __getitem__(self, key):
            return "sliced-output-ids"

    generator._processor = MagicMock()
    generator._processor.apply_chat_template.return_value = _FakeBatchEncoding({"input_ids": fake_input_ids})
    generator._model = MagicMock()
    generator._model.generate.return_value = _FakeOutputIds()

    return generator


def test_local_vlm_generator_describe_image_sends_image_and_system_prompt(tmp_path):
    image_path = tmp_path / "look.jpg"
    image_path.write_bytes(b"fake-image-bytes")

    generator = _make_local_generator()
    generator._processor.batch_decode.return_value = ["赤いドレスの女性が微笑んでいる"]

    with patch("PIL.Image.open") as mock_open:
        fake_image = mock_open.return_value.convert.return_value
        result = generator.describe_image(image_path, "説明してください")

    # 大きい画像はトークン過多で失敗・低速になるため、長辺を縮小してから渡す
    fake_image.thumbnail.assert_called_once_with((768, 768))
    assert result.description == "赤いドレスの女性が微笑んでいる"
    messages = generator._processor.apply_chat_template.call_args[0][0]
    assert messages[0] == {"role": "system", "content": "説明してください"}
    assert messages[1]["content"][0] == {"type": "image", "image": fake_image}


def test_local_vlm_generator_describe_image_parses_tags_from_response(tmp_path):
    image_path = tmp_path / "look.jpg"
    image_path.write_bytes(b"fake-image-bytes")

    generator = _make_local_generator()
    generator._processor.batch_decode.return_value = ["説明: 赤いドレスの女性\nタグ: 赤, ドレス"]

    with patch("PIL.Image.open") as mock_open:
        result = generator.describe_image(image_path, "説明してください")

    assert result.description == "赤いドレスの女性"
    assert result.suggested_tags == ["赤", "ドレス"]


def test_local_vlm_generator_generate_caption_sends_description_text(tmp_path):
    generator = _make_local_generator()
    generator._processor.batch_decode.return_value = ["今日の一枚です"]

    result = generator.generate_caption("赤いドレスの女性", "投稿文を作って")

    assert result == "今日の一枚です"
    messages = generator._processor.apply_chat_template.call_args[0][0]
    assert messages[0] == {"role": "system", "content": "投稿文を作って"}
    assert "赤いドレスの女性" in messages[1]["content"]


def test_local_vlm_generator_wraps_errors_as_generation_error(tmp_path):
    generator = _make_local_generator()
    generator._model.generate.side_effect = RuntimeError("out of memory")

    try:
        generator.generate_caption("説明", "プロンプト")
        assert False, "expected GenerationError"
    except generation.GenerationError as exc:
        assert "out of memory" in str(exc)


def test_try_create_generator_returns_local_generator(tmp_path):
    conn = _make_conn(tmp_path)
    db.set_setting(conn, "generation_provider", "local")

    with patch("core.generation.LocalVlmGenerator") as mock_cls:
        mock_cls.return_value = "fake-local-generator"
        result = generation.try_create_generator(conn)

    assert result == "fake-local-generator"
    mock_cls.assert_called_once_with(generation.DEFAULT_LOCAL_VLM_MODEL)


def test_try_create_generator_returns_none_for_local_when_packages_missing(tmp_path):
    conn = _make_conn(tmp_path)
    db.set_setting(conn, "generation_provider", "local")

    with patch("core.generation.LocalVlmGenerator", side_effect=ImportError("no torch")):
        result = generation.try_create_generator(conn)

    assert result is None


def _fake_urlopen(payload):
    import io
    import json

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return lambda request, timeout=None: _Resp(json.dumps(payload).encode("utf-8"))


def test_list_available_models_claude():
    from unittest.mock import patch

    from core.generation import list_available_models

    payload = {"data": [{"id": "claude-b"}, {"id": "claude-a"}]}
    with patch("urllib.request.urlopen", _fake_urlopen(payload)):
        assert list_available_models("claude", "key") == ["claude-a", "claude-b"]


def test_list_available_models_openai_filters_non_chat_models():
    from unittest.mock import patch

    from core.generation import list_available_models

    payload = {"data": [{"id": i} for i in ["gpt-4o", "text-embedding-3-small", "o3", "whisper-1", "gpt-4o-mini-tts"]]}
    with patch("urllib.request.urlopen", _fake_urlopen(payload)):
        assert list_available_models("openai", "key") == ["gpt-4o", "o3"]


def test_list_available_models_local_uses_hub_order():
    from unittest.mock import patch

    from core.generation import list_available_models

    payload = [{"id": "org/b"}, {"id": "org/a"}]
    with patch("urllib.request.urlopen", _fake_urlopen(payload)):
        assert list_available_models("local", query="vl") == ["org/b", "org/a"]


def test_list_available_models_wraps_errors():
    from unittest.mock import patch

    import pytest

    from core.generation import GenerationError, list_available_models

    def boom(request, timeout=None):
        raise OSError("down")

    with patch("urllib.request.urlopen", boom), pytest.raises(GenerationError):
        list_available_models("claude", "key")


def test_parse_tag_response_handles_category_lines_and_lists():
    text = "- 服装=レッドドレス\n- 性別=女性\n背景・ロケーション：夜の都市\n- 日本人か=不明\n- 胸の大きさ=大きい"
    assert generation.parse_tag_response(text) == ["レッドドレス", "女性", "夜の都市", "大きい"]
    assert generation.parse_tag_response("夜, 城市, 紅いドレス, 夜") == ["夜", "城市", "紅いドレス"]
    assert generation.parse_tag_response("") == []


def test_build_tag_prompt_lists_categories_and_options():
    prompt = generation.build_tag_prompt([{"name": "性別", "options": "女, 男"}, {"name": "服装", "options": ""}])
    assert "- 性別（候補: 女, 男）" in prompt and "- 服装" in prompt and "カテゴリ名=タグ" in prompt
    assert "5個" in generation.build_tag_prompt([])


def test_local_vlm_suggest_tags_uses_separate_user_prompt(tmp_path):
    image_path = tmp_path / "look.jpg"
    image_path.write_bytes(b"x")
    generator = _make_local_generator()
    generator._processor.batch_decode.return_value = ["- 服装=水着\n- 性別=女性"]

    with patch("PIL.Image.open") as mock_open:
        tags = generator.suggest_tags(image_path, [{"name": "服装", "options": ""}])

    assert tags == ["水着", "女性"]
    messages = generator._processor.apply_chat_template.call_args[0][0]
    assert len(messages) == 1 and messages[0]["role"] == "user"  # systemを使わない
    assert "- 服装" in messages[0]["content"][1]["text"]
