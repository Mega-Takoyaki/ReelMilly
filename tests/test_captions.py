"""投稿文の生成: 形式の検査・作り直し・クラウドのモデルはsfwだけ・API。"""
from unittest.mock import MagicMock, patch

import pytest

from core import captions, db, generation, settings
from core.web.app import create_app

from tests.test_worker import _insert, _setup  # noqa: F401

GOOD = "A red dress and a smile for you 💋✨\n---\n赤いドレスで、あなたに向けてにっこり💋✨"


def test_clean_strips_quotes_and_code_fences():
    assert captions.clean('"hello"') == "hello"
    assert captions.clean("「こんにちは」") == "こんにちは"
    assert captions.clean("```\nabc\n```") == "abc"
    assert captions.clean('"a" and "b"') == '"a" and "b"'  # 全体を囲んでいない引用符は、残す
    assert captions.clean("  x  ") == "x"


@pytest.mark.parametrize("text,expected_ok", [
    (GOOD, True),
    ("夜の街を歩く女性の魅力。手に握るリング。", False),                 # 英日併記になっていない(実機で出た文)
    ("---\n夜の街を背景に、赤いワンピースの女性が微笑んでいます。", False),  # 英語の行が抜けた(実機で出た文)
    ("Hello 💋\n---\nHello again", False),                              # 日本語が無い
    ("こんにちは\n---\nこんにちは", False),                              # 英語が無い
    ("A\n---\nB\n---\nC", False),                                        # 区切りが2つ
    ("", False),
])
def test_fanvue_format_check(text, expected_ok):
    assert (captions.check(text, "fanvue") is None) is expected_ok


def test_fanvue_length_limit():
    assert captions.check("A" + "a" * 700 + "\n---\nあ", "fanvue") == "長すぎます"


def test_x_check_counts_fullwidth_as_two():
    assert captions.x_weight("abc") == 3 and captions.x_weight("あい") == 4
    assert captions.check("あ" * 140, "x") is None            # 280ちょうど
    assert "上限" in captions.check("あ" * 141, "x")
    assert captions.check("Short post #AI ✨", "x") is None


@pytest.fixture
def env(tmp_path):
    config, conn = _setup(tmp_path)
    _insert(
        conn, tmp_path, "a1", content_description="赤いドレスの女性が微笑んでいる",
        content_rating="sfw", content_rating_confirmed=1,
    )
    return config, conn


def fake(*outputs):
    generator = MagicMock()
    generator.generate_caption.side_effect = list(outputs)
    return generator


def test_generate_retries_until_the_format_matches(env):
    config, conn = env
    generator = fake("悪い文", '"悪い文2"', GOOD)
    result = captions.generate(conn, [db.get_asset(conn, "a1")], "fanvue", generator=generator)
    assert result.text == GOOD and result.attempts == 3
    # 指定したプロンプト(設定のFanvue用)と、内容説明が渡る
    description, prompt = generator.generate_caption.call_args.args
    assert description == "赤いドレスの女性が微笑んでいる" and prompt == settings.get_caption_system_prompt(conn)


def test_generate_gives_up_after_three_attempts(env):
    config, conn = env
    generator = fake("悪い", "悪い", "悪い")
    with pytest.raises(captions.CaptionError, match="3回試して"):
        captions.generate(conn, [db.get_asset(conn, "a1")], "fanvue", generator=generator)


def test_generate_uses_the_x_prompt_and_limit_for_x(env):
    config, conn = env
    db.set_setting(conn, "x_caption_system_prompt", "Xのための指示")
    generator = fake("あ" * 200, "夜の街で、にっこり✨ #AI")
    result = captions.generate(conn, [db.get_asset(conn, "a1")], "x", generator=generator)
    assert result.text == "夜の街で、にっこり✨ #AI" and result.attempts == 2  # 文字数の上限を超えた文は、作り直す
    assert generator.generate_caption.call_args.args[1] == "Xのための指示"


def test_generate_combines_several_descriptions_and_requires_one(env, tmp_path):
    config, conn = env
    _insert(conn, tmp_path, "a2", content_description="花柄のワンピースの女性")
    generator = fake(GOOD)
    captions.generate(conn, [db.get_asset(conn, "a1"), db.get_asset(conn, "a2")], "fanvue", generator=generator)
    description = generator.generate_caption.call_args.args[0]
    assert "作品1: 赤いドレス" in description and "作品2: 花柄" in description and "まとめて1つの投稿" in description

    _insert(conn, tmp_path, "a3")
    with pytest.raises(captions.CaptionError, match="内容説明がありません"):
        captions.generate(conn, [db.get_asset(conn, "a3")], "fanvue", generator=fake(GOOD))


def test_generation_errors_are_reported(env):
    config, conn = env
    generator = MagicMock()
    generator.generate_caption.side_effect = generation.GenerationError("API error")
    with pytest.raises(captions.CaptionError, match="API error"):
        captions.generate(conn, [db.get_asset(conn, "a1")], "fanvue", generator=generator)


def test_cloud_model_is_used_only_for_confirmed_sfw(env):
    """クラウド(Claude/OpenAI)は、承認済みのsfwの作品だけ。それ以外は、自前VLMで作る(クラウドへは送らない)。"""
    config, conn = env
    settings.set_caption_model(conn, "claude", "")
    created = []

    def fake_create(provider, model):
        created.append((provider, model))
        return object()

    with patch("core.generation._create_for", side_effect=fake_create):
        generation.try_create_caption_generator(conn, ["sfw", "sfw"])
        generation.try_create_caption_generator(conn, ["sfw", "suggestive"])
        generation.try_create_caption_generator(conn, ["sfw", None])  # 未承認も、クラウドには送らない
        generation.try_create_caption_generator(conn, ["explicit"])
    assert created[0] == ("claude", "claude-haiku-4-5-20251001")  # 既定の小さいモデル
    assert [c[0] for c in created[1:]] == ["local", "local", "local"]

    created.clear()
    settings.set_caption_model(conn, "claude", "claude-opus-5-5")  # モデルを指定
    with patch("core.generation._create_for", side_effect=fake_create):
        generation.try_create_caption_generator(conn, ["sfw"])
    assert created == [("claude", "claude-opus-5-5")]

    created.clear()
    settings.set_caption_model(conn, "same", "")  # 画像内容説明と同じ
    db.set_setting(conn, "generation_provider", "local")
    with patch("core.generation._create_for", side_effect=fake_create):
        generation.try_create_caption_generator(conn, ["explicit"])
    assert created[0][0] == "local"


def test_caption_api_and_settings_page(env):
    config, conn = env
    client = create_app(config).test_client()
    with patch("core.captions.generate", return_value=captions.CaptionResult(text=GOOD, attempts=2)) as gen:
        res = client.post("/api/captions/generate", json={"asset_ids": ["a1"], "channel": "fanvue"})
    assert res.status_code == 200 and res.get_json() == {"text": GOOD, "attempts": 2}
    assert gen.call_args.args[2] == "fanvue"

    with patch("core.captions.generate", side_effect=captions.CaptionError("形式に合う文を作れませんでした")):
        res = client.post("/api/captions/generate", json={"asset_ids": ["a1"], "channel": "x"})
    assert res.status_code == 422 and "形式" in res.get_json()["error"]
    assert client.post("/api/captions/generate", json={"asset_ids": [], "channel": "fanvue"}).status_code == 400
    assert client.post("/api/captions/generate", json={"asset_ids": ["nope"], "channel": "fanvue"}).status_code == 404
    assert client.post("/api/captions/generate", json={"asset_ids": ["a1"], "channel": "tiktok"}).status_code == 400

    page = client.get("/settings").get_data(as_text=True)
    assert 'name="x_caption_system_prompt"' in page and 'name="caption_provider"' in page and 'name="caption_model"' in page
    client.post("/settings", data={
        "x_caption_system_prompt": "Xの新しい指示", "caption_provider": "claude", "caption_model": "claude-haiku-4-5-20251001",
    })
    assert settings.get_x_caption_system_prompt(conn) == "Xの新しい指示"
    assert (settings.get_caption_provider(conn), settings.get_caption_model(conn)) == ("claude", "claude-haiku-4-5-20251001")
    client.post("/settings", data={"caption_provider": "same", "caption_model": ""})  # 空欄にして、既定に戻せる
    assert (settings.get_caption_provider(conn), settings.get_caption_model(conn)) == ("same", "")


def test_post_now_dialog_has_the_generate_button(env):
    config, conn = env
    client = create_app(config).test_client()
    html = client.get("/").get_data(as_text=True)
    assert 'id="pn-generate"' in html and 'id="pn-undo"' in html
    assert "/api/captions/generate" in client.get("/static/post-now.js").get_data(as_text=True)
