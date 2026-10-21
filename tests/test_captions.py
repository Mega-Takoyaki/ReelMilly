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


def test_quantize_linear_int8_replaces_big_linear_layers_and_keeps_outputs_close():
    """CPUで大きなモデルを動かすための、int8の動的量子化。大きな全結合層だけを置き換え、出力はほぼ変わらない。"""
    import torch
    from torch import nn

    from core.generation import quantize_linear_int8

    torch.manual_seed(0)

    class Tiny(nn.Module):
        def __init__(self):
            super().__init__()
            self.embed = nn.Embedding(50, 512)
            self.block = nn.Sequential(nn.Linear(512, 1024), nn.GELU(), nn.Linear(1024, 512))
            self.small = nn.Linear(4, 4)          # 小さな層は、そのまま
            self.lm_head = nn.Linear(512, 50)     # 出力層は、そのまま

        def forward(self, ids):
            return self.lm_head(self.block(self.embed(ids)))

    model = Tiny().eval()
    ids = torch.randint(0, 50, (1, 6))
    expected = model(ids)
    model = model.to(torch.bfloat16)  # 読み込みは、bf16。量子化の関数が、1層ずつ、fp32にして量子化する
    count = quantize_linear_int8(model, min_params=100_000)

    assert count == 2 and type(model.block[0]).__name__ == "Linear" and "quantized" in type(model.block[0]).__module__
    assert type(model.small) is nn.Linear and type(model.lm_head) is nn.Linear
    assert all(p.dtype == torch.float32 for p in model.parameters())  # 量子化しない部品は、fp32にそろう(bf16のまま残らない)
    got = model(ids)
    assert torch.allclose(got, expected, atol=0.15, rtol=0.15)


# ---------------------------------------------------------------- 英訳

def test_compose_translation_modes():
    assert captions.compose_translation("原文", "English", "append", "english_first") == "English\n---\n原文"
    assert captions.compose_translation("原文", "English", "append", "original_first") == "原文\n---\nEnglish"
    assert captions.compose_translation("原文", "English", "replace", "english_first") == "English"


def _translator(output="Hello 💋"):
    generator = MagicMock()
    generator.translate.return_value = output
    return generator


def test_translate_appends_english_above_by_default_and_follows_settings(env):
    config, conn = env
    assets = [db.get_asset(conn, "a1")]
    generator = _translator()
    out = captions.translate(conn, assets, "  こんにちは💋 ", generator=generator)
    assert out == {"text": "Hello 💋\n---\nこんにちは💋", "english": "Hello 💋"}
    assert generator.translate.call_args.args[0] == "こんにちは💋"
    settings.set_translate_settings(conn, "append", "original_first")
    assert captions.translate(conn, assets, "こんにちは", generator=_translator("Hi"))["text"] == "こんにちは\n---\nHi"
    settings.set_translate_settings(conn, "replace", "english_first")
    assert captions.translate(conn, assets, "こんにちは", generator=_translator("Hi"))["text"] == "Hi"
    settings.set_translate_settings(conn, "bogus", "bogus")  # 不正な値は、保存しない
    assert settings.get_translate_settings(conn) == {"mode": "replace", "order": "english_first"}


def test_translate_errors(env):
    config, conn = env
    with pytest.raises(captions.CaptionError, match="空"):
        captions.translate(conn, [], "  ", generator=_translator())
    broken = MagicMock()
    broken.translate.side_effect = generation.GenerationError("API error")
    with pytest.raises(captions.CaptionError, match="API error"):
        captions.translate(conn, [], "こんにちは", generator=broken)
    with pytest.raises(captions.CaptionError, match="空"):
        captions.translate(conn, [], "こんにちは", generator=_translator("  "))


def test_translate_uses_local_model_for_non_sfw(env):
    """承認済みのsfwでない作品の投稿文は、クラウドのAIへは送らない(投稿文の生成と同じ決まり)。"""
    config, conn = env
    settings.set_caption_model(conn, "claude", "")
    created = []
    with patch("core.generation._create_for", side_effect=lambda p, m: created.append(p) or _translator()):
        captions.translate(conn, [db.get_asset(conn, "a1")], "こんにちは")  # a1: 承認済みsfw
        db.update_asset(conn, "a1", content_rating="explicit")
        captions.translate(conn, [db.get_asset(conn, "a1")], "こんにちは")
    assert created == ["claude", "local"]


def test_translate_api(env):
    config, conn = env
    client = create_app(config).test_client()
    with patch("core.generation.try_create_caption_generator", return_value=_translator("Hi")):
        res = client.post("/api/captions/translate", json={"asset_ids": ["a1"], "text": "こんにちは"})
        assert res.status_code == 200 and res.get_json()["text"] == "Hi\n---\nこんにちは"
        assert client.post("/api/captions/translate", json={"asset_ids": ["nope"], "text": "x"}).status_code == 404
        assert client.post("/api/captions/translate", json={"asset_ids": ["a1"], "text": ""}).status_code == 422


def test_local_vlm_is_reused_until_idle_release(env, monkeypatch):
    """読み込みに数十秒かかる自前VLMは、使い回す(押すたびに読み込み直さない)。使われなければ手放す。"""
    config, conn = env
    created = []

    class FakeLocal(generation.LocalVlmGenerator):
        def __init__(self, model_id):
            created.append(model_id)

    monkeypatch.setattr(generation, "_create_for", lambda provider, model: FakeLocal(model or "m"))
    generation._release_local()
    first = generation._create_cached("local", "m")
    assert generation._create_cached("local", "m") is first and created == ["m"]
    assert generation._create_cached("local", "other") is not first and created == ["m", "other"]  # モデルが違えば、作り直す
    generation._release_local()
    assert generation._create_cached("local", "other") is not None and created == ["m", "other", "other"]
    assert generation._create_cached("claude", "x") is not None  # クラウドは、保持しない
    generation._release_local()
