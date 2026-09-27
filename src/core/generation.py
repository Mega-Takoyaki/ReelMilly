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
DEFAULT_LOCAL_VLM_MODEL = "prithivMLmods/Qwen2-VL-2B-Abliterated-Caption-it"

MAX_SUGGESTED_TAGS = 5

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


def _image_media_type(path: Path) -> str:
    return _IMAGE_MEDIA_TYPES.get(path.suffix.lower(), "image/jpeg")


def _encode_image_base64(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode("utf-8")


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
        tags = [t.strip() for t in re.split(r"[,、]", tag_match.group(1)) if t.strip()]
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

    def _generate(self, messages: list) -> str:
        inputs = self._processor.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_tensors="pt",
            return_dict=True,
        ).to(self._device)

        with self._torch.no_grad():
            output_ids = self._model.generate(**inputs, max_new_tokens=256)

        generated_ids = output_ids[:, inputs["input_ids"].shape[-1] :]
        text = self._processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
        return text.strip()

    def describe_image(self, image_path: Path, system_prompt: str) -> DescriptionResult:
        from PIL import Image

        image = Image.open(image_path).convert("RGB")
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
