"""画像内容説明・Fanvue投稿文生成(ADR-0015/ADR-0017)。

生成AIプロバイダーを抽象化する。Claude API/OpenAI APIに加え、自前ホスト型VLM
(local、ADR-0017)を選択できる。Claude API/OpenAI APIはいずれも性的に露骨な
コンテンツの処理を拒否するため(利用ポリシー上の制約。ADR-0017参照)、
content_rating=explicit相当のアセットにはlocalプロバイダーの使用を推奨する。

重い依存関係(anthropic/openai/transformers/torch)はこのモジュールのimport時
には読み込まず、実際に生成AIを呼び出すタイミングまで遅延させる。
"""
from __future__ import annotations

import base64
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CLAUDE_MODEL = "claude-opus-5"
DEFAULT_OPENAI_MODEL = "gpt-4o"
LOCAL_VLM_MAX_IMAGE_SIDE = 768
DEFAULT_LOCAL_VLM_MODEL = "prithivMLmods/Qwen2-VL-2B-Abliterated-Caption-it"

MAX_SUGGESTED_TAGS = 12  # タグカテゴリ(設定)を複数指定した場合に1カテゴリ1タグ付くよう余裕を持たせる
_UNKNOWN_TAG_VALUES = {"不明", "なし", "無し", "該当なし", "判断不能"}

_IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

# 説明文生成の応答は「説明: ...」「タグ: ...」の2行形式を期待する(core/settings.pyの
# DEFAULT_DESCRIPTION_SYSTEM_PROMPTで指示)。形式に従わない応答が返っても、
# 全文を説明文・タグなしとして扱いエラーにはしない(ADR-0018参照)。
_DESCRIBE_USER_PROMPT = "この画像の内容を説明してください。"
_TAG_LINE_PATTERN = re.compile(r"タグ[:：]\s*(.+)")
_DESCRIPTION_LINE_PATTERN = re.compile(r"説明[:：]\s*(.+)", re.DOTALL)


class GenerationError(Exception):
    """画像解析・投稿文生成の呼び出しに失敗したことを示す。"""


@dataclass
class DescriptionResult:
    """`describe_image`の戻り値。内容説明と、AIが提案したタグ候補を保持する(ADR-0018)。"""

    description: str
    suggested_tags: list[str] = field(default_factory=list)
    # タグ生成(別呼び出し)に失敗した、またはタグが得られなかった場合の理由。説明文は有効
    tag_error: str | None = None


def _image_media_type(path: Path) -> str:
    return _IMAGE_MEDIA_TYPES.get(path.suffix.lower(), "image/jpeg")


def _encode_image_base64(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode("utf-8")


def build_tag_prompt(categories: list[dict]) -> str:
    """タグ生成用のユーザー指示文。小型モデルはsystemの長い指示に従わないことがあるため、
    説明文生成とは別の呼び出しで、短い指示だけをユーザー発話として渡す(実機で確認済みの形式)。
    `categories`は[{"name", "options"}]。空なら自由なタグ5個を求める。
    """
    if not categories:
        return (
            "この画像に当てはまるタグを、日本語の短い単語で5個、カンマ区切りで書いてください。"
            "タグだけを出力し、説明は書かないでください。\n例: 屋外, 赤いドレス, 笑顔, 街並み, 夜"
        )
    lines = []
    for c in categories:
        hint = f"（候補: {c['options']}）" if c.get("options") else ""
        lines.append(f"- {c['name']}{hint}")
    return (
        "次のカテゴリごとに、この画像に当てはまる短いタグを答えてください。\n"
        "形式は「カテゴリ名=タグ」を1行に1つ。判断できないカテゴリは書かないでください。\n" + "\n".join(lines)
    )


_TAG_PREFIX = re.compile(r"^[\s\-\*・•\d.)）]+")


def parse_tag_response(text: str) -> list[str]:
    """タグ生成の応答から、タグ(値)だけを取り出す。

    「カテゴリ名=タグ」(区切りは=,＝,:,：)の行はタグ部分を、区切りの無い行はカンマ区切りの
    タグ列として扱う。「不明」「なし」などは除き、重複を除いてMAX_SUGGESTED_TAGSまでにする。
    """
    tags: list[str] = []
    for line in text.splitlines():
        line = _TAG_PREFIX.sub("", line.strip())
        if not line:
            continue
        if re.search(r"[=＝:：]", line):
            line = re.split(r"[=＝:：]", line, maxsplit=1)[-1]
        for part in re.split(r"[,、，/／]", line):
            tag = part.strip().strip("「」『』\"'。.")
            if tag and tag not in _UNKNOWN_TAG_VALUES and tag not in tags:
                tags.append(tag)
    return tags[:MAX_SUGGESTED_TAGS]


def _parse_description_response(text: str) -> DescriptionResult:
    """「説明: ...」「タグ: タグ1, タグ2」形式の応答を分解する。

    形式に従わない場合は応答全体を説明文として扱い、タグは空にする
    (フォーマット崩れでパイプライン全体を失敗させないため)。
    """
    text = text.strip()
    description = text
    tags: list[str] = []

    tag_match = _TAG_LINE_PATTERN.search(text)
    if tag_match:
        raw_tags = [t.strip() for t in re.split(r"[,、]", tag_match.group(1)) if t.strip()]
        # タグカテゴリ指定時は「カテゴリ名=タグ」形式で返るため、タグ部分だけを取り出す
        tags = [re.split(r"[=＝]", t, maxsplit=1)[-1].strip() for t in raw_tags]
        tags = [t for t in tags if t and t not in _UNKNOWN_TAG_VALUES]
        description = text[: tag_match.start()].strip()

    desc_match = _DESCRIPTION_LINE_PATTERN.match(description)
    if desc_match:
        description = desc_match.group(1).strip()

    return DescriptionResult(description=description, suggested_tags=tags[:MAX_SUGGESTED_TAGS])


class ClaudeGenerator:
    """Claude API(Vision対応)を使った画像内容説明・投稿文生成。"""

    def __init__(self, api_key: str, model: str = DEFAULT_CLAUDE_MODEL):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def describe_image(self, image_path: Path, system_prompt: str) -> DescriptionResult:
        image_b64 = _encode_image_base64(image_path)
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=system_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": _image_media_type(image_path),
                                    "data": image_b64,
                                },
                            },
                            {"type": "text", "text": _DESCRIBE_USER_PROMPT},
                        ],
                    }
                ],
            )
        except Exception as exc:  # noqa: BLE001 - 呼び出し元でイベント記録するため詳細を残す
            raise GenerationError(str(exc)) from exc
        return _parse_description_response(_extract_text(response))

    def suggest_tags(self, image_path: Path, categories: list[dict]) -> list[str]:
        image_b64 = _encode_image_base64(image_path)
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=256,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": _image_media_type(image_path),
                                    "data": image_b64,
                                },
                            },
                            {"type": "text", "text": build_tag_prompt(categories)},
                        ],
                    }
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise GenerationError(str(exc)) from exc
        return parse_tag_response(_extract_text(response))

    def generate_caption(self, content_description: str, system_prompt: str) -> str:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=system_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": f"以下は画像の内容説明です。これをもとに投稿文を作成してください。\n\n{content_description}",
                    }
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise GenerationError(str(exc)) from exc
        return _extract_text(response)


def _extract_text(response) -> str:
    parts = [block.text for block in response.content if getattr(block, "type", None) == "text"]
    return "".join(parts).strip()


class OpenAiGenerator:
    """OpenAI API(gpt-4o系)を使った画像内容説明・投稿文生成。

    実API疎通は未検証(ADR-0015)。Fanvueクライアントと同様、実行して失敗する
    場合はTODO.mdを参照して調整すること。
    """

    def __init__(self, api_key: str, model: str = DEFAULT_OPENAI_MODEL):
        import openai

        self._client = openai.OpenAI(api_key=api_key)
        self._model = model

    def describe_image(self, image_path: Path, system_prompt: str) -> DescriptionResult:
        image_b64 = _encode_image_base64(image_path)
        media_type = _image_media_type(image_path)
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _DESCRIBE_USER_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{media_type};base64,{image_b64}"},
                            },
                        ],
                    },
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise GenerationError(str(exc)) from exc
        return _parse_description_response(response.choices[0].message.content or "")

    def suggest_tags(self, image_path: Path, categories: list[dict]) -> list[str]:
        image_b64 = _encode_image_base64(image_path)
        media_type = _image_media_type(image_path)
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": build_tag_prompt(categories)},
                            {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_b64}"}},
                        ],
                    }
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise GenerationError(str(exc)) from exc
        return parse_tag_response(response.choices[0].message.content or "")

    def generate_caption(self, content_description: str, system_prompt: str) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": f"以下は画像の内容説明です。これをもとに投稿文を作成してください。\n\n{content_description}",
                    },
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise GenerationError(str(exc)) from exc
        return (response.choices[0].message.content or "").strip()


class LocalVlmGenerator:
    """自前ホスト型VLM(Vision-Language Model)による画像内容説明・投稿文生成(ADR-0017)。

    Claude API/OpenAI APIはいずれも性的に露骨なコンテンツの処理・説明を拒否する
    可能性が高く、AWS Bedrock経由でも同じ制約(+AWS自身の利用規約)がかかる
    (ADR-0017)。本クラスはHugging Face上の公開モデルをローカルで推論することで、
    外部サービスの利用ポリシーに縛られずにexplicit判定のアセットも処理できる
    ようにする。

    GPU(CUDA)が利用可能ならモデルをGPUに載せて自動的に高速化する。GPUが無い
    環境ではCPU推論となり、1枚あたり数秒〜数十秒程度かかる見込み(実機未検証、
    TODO.md参照)。モデルの重み(数GB)は初回呼び出し時にHugging Face Hubから
    自動ダウンロードされる。

    load_in_...等の量子化オプションは実装していない。transformers/モデルの
    バージョンによりロード・生成APIの細部が変わる可能性があるため、実機での
    動作検証が必要(TODO.md参照)。
    """

    def __init__(self, model_id: str = DEFAULT_LOCAL_VLM_MODEL):
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor

        self._torch = torch
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._dtype = torch.float16 if self._device == "cuda" else torch.float32

        self._processor = AutoProcessor.from_pretrained(model_id)
        model = AutoModelForImageTextToText.from_pretrained(model_id)
        self._model = model.to(device=self._device, dtype=self._dtype)
        self._model.eval()

    def _generate(self, messages: list, max_new_tokens: int = 256) -> str:
        inputs = self._processor.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_tensors="pt",
            return_dict=True,
        ).to(self._device)

        with self._torch.no_grad():
            output_ids = self._model.generate(**inputs, max_new_tokens=max_new_tokens)

        generated_ids = output_ids[:, inputs["input_ids"].shape[-1] :]
        text = self._processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
        return text.strip()

    def describe_image(self, image_path: Path, system_prompt: str) -> DescriptionResult:
        from PIL import Image

        image = Image.open(image_path).convert("RGB")
        # 大きい画像は画像トークンが多すぎて処理が遅くなり、プロセッサのmax_length切り詰めで
        # 「image token count mismatch」エラーになるため、長辺を縮小してから渡す
        image.thumbnail((LOCAL_VLM_MAX_IMAGE_SIDE, LOCAL_VLM_MAX_IMAGE_SIDE))
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": image},
                    {"type": "text", "text": _DESCRIBE_USER_PROMPT},
                ],
            },
        ]
        try:
            text = self._generate(messages)
        except Exception as exc:  # noqa: BLE001
            raise GenerationError(str(exc)) from exc
        return _parse_description_response(text)

    def suggest_tags(self, image_path: Path, categories: list[dict]) -> list[str]:
        """タグを別の呼び出しで生成する。説明文用のsystemプロンプトに混ぜると、小型モデルは
        「タグ:」行を出力しないことが実機で確認されたため、短い指示だけを単独で渡す。"""
        from PIL import Image

        image = Image.open(image_path).convert("RGB")
        image.thumbnail((LOCAL_VLM_MAX_IMAGE_SIDE, LOCAL_VLM_MAX_IMAGE_SIDE))
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": image},
                    {"type": "text", "text": build_tag_prompt(categories)},
                ],
            }
        ]
        try:
            text = self._generate(messages, max_new_tokens=96)
        except Exception as exc:  # noqa: BLE001
            raise GenerationError(str(exc)) from exc
        return parse_tag_response(text)

    def generate_caption(self, content_description: str, system_prompt: str) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"以下は画像の内容説明です。これをもとに投稿文を作成してください。\n\n{content_description}",
            },
        ]
        try:
            return self._generate(messages)
        except Exception as exc:  # noqa: BLE001
            raise GenerationError(str(exc)) from exc


_OPENAI_EXCLUDE = (
    "embedding", "tts", "whisper", "transcribe", "realtime", "audio",
    "image", "moderation", "search", "dall-e", "davinci", "babbage", "instruct",
)


def list_available_models(provider: str, api_key: str = "", query: str = "") -> list[str]:
    """実際に接続し、利用可能なモデルID一覧を取得する。

    claude/openaiはAPIキーで各社のAPIに、localはHugging Face Hub(APIキー不要)に問い合わせる。
    localは画像+テキスト入力モデル(image-text-to-text)をダウンロード数順に最大50件、
    `query`があれば名前で絞り込んで返す。

    失敗時はGenerationErrorを送出する。
    """
    import json
    import urllib.request

    if provider == "claude":
        request = urllib.request.Request(
            "https://api.anthropic.com/v1/models?limit=1000",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        )
    elif provider == "openai":
        request = urllib.request.Request(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
    elif provider == "local":
        from urllib.parse import urlencode

        params = {"pipeline_tag": "image-text-to-text", "library": "transformers",
                  "sort": "downloads", "limit": "50"}
        if query.strip():
            params["search"] = query.strip()
        request = urllib.request.Request("https://huggingface.co/api/models?" + urlencode(params))
    else:
        raise GenerationError(f"モデル一覧の取得に対応していないプロバイダーです: {provider}")

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except Exception as exc:  # noqa: BLE001
        raise GenerationError(f"モデル一覧の取得に失敗しました: {exc}") from exc

    if provider == "local":
        return [item["id"] for item in payload if item.get("id")]

    ids = [item["id"] for item in payload.get("data", []) if item.get("id")]
    if provider == "openai":
        ids = [
            i for i in ids
            if (i.startswith(("gpt-", "chatgpt-")) or (i[:1] == "o" and i[1:2].isdigit()))
            and not any(word in i for word in _OPENAI_EXCLUDE)
        ]
    return sorted(set(ids))


def _create_for(provider: str, model: str):
    """プロバイダー名とモデル名から、Generatorを作る。APIキー未設定/未インストールならNone。"""
    if provider == "claude":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None
        try:
            return ClaudeGenerator(api_key, model or DEFAULT_CLAUDE_MODEL)
        except ImportError:
            return None
    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None
        try:
            return OpenAiGenerator(api_key, model or DEFAULT_OPENAI_MODEL)
        except ImportError:
            return None
    if provider == "local":
        try:
            return LocalVlmGenerator(model or DEFAULT_LOCAL_VLM_MODEL)
        except ImportError:
            return None
    return None


_CLOUD_PROVIDERS = ("claude", "openai")


def try_create_caption_generator(conn: sqlite3.Connection, ratings: list[str | None] | None = None):
    """投稿文の生成に使うGeneratorを返す(設定の「投稿文の生成に使うモデル」)。

    クラウド(Claude/OpenAI)のモデルは、利用ポリシー上、性的に露骨な内容に向かないため、対象の作品が
    **すべて承認済みのsfw**のときだけ使う。それ以外(suggestive/explicit・未承認)は、自前ホスト型VLMで作る。
    """
    from core import settings as settings_module

    provider = settings_module.get_caption_provider(conn)
    model = settings_module.get_caption_model(conn)
    if provider == settings_module.CAPTION_PROVIDER_SAME:
        provider = settings_module.get_generation_provider(conn)
        model = model or settings_module.get_generation_model(conn)
    else:
        model = model or settings_module.DEFAULT_CAPTION_MODEL.get(provider, "")
    if provider in _CLOUD_PROVIDERS and ratings is not None and not all(r == "sfw" for r in ratings):
        local_model = (
            settings_module.get_generation_model(conn) if settings_module.get_generation_provider(conn) == "local" else ""
        )
        return _create_for("local", local_model)  # 使えなければNone(sfwでない作品を、クラウドへは送らない)
    return _create_for(provider, model)


def try_create_generator(conn: sqlite3.Connection):
    """`settings`の設定に基づきGeneratorを返す。APIキー未設定/未インストールならNoneを返す。"""
    from core import settings as settings_module

    provider = settings_module.get_generation_provider(conn)
    model = settings_module.get_generation_model(conn)

    if provider == "claude":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None
        try:
            return ClaudeGenerator(api_key, model or DEFAULT_CLAUDE_MODEL)
        except ImportError:
            return None

    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None
        try:
            return OpenAiGenerator(api_key, model or DEFAULT_OPENAI_MODEL)
        except ImportError:
            return None

    if provider == "local":
        try:
            return LocalVlmGenerator(model or DEFAULT_LOCAL_VLM_MODEL)
        except ImportError:
            return None

    return None
