"""画像内容説明・Fanvue投稿文生成(ADR-0015)。

生成AIプロバイダーを抽象化する。既定はClaude API(Vision対応)。OpenAI APIも
選択できるが実API疎通は未検証(README/TODO.md参照)。将来的なAWS Bedrock対応は
未実装(ADR-0015)。

重い依存関係(anthropic/openai)はこのモジュールのimport時には読み込まず、
実際に生成AIを呼び出すタイミングまで遅延させる。
"""
from __future__ import annotations

import base64
import os
import sqlite3
from pathlib import Path

DEFAULT_CLAUDE_MODEL = "claude-opus-5"
DEFAULT_OPENAI_MODEL = "gpt-4o"

_IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

_DESCRIBE_USER_PROMPT = "この画像の内容を説明してください。"


class GenerationError(Exception):
    """画像解析・投稿文生成の呼び出しに失敗したことを示す。"""


def _image_media_type(path: Path) -> str:
    return _IMAGE_MEDIA_TYPES.get(path.suffix.lower(), "image/jpeg")


def _encode_image_base64(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode("utf-8")


class ClaudeGenerator:
    """Claude API(Vision対応)を使った画像内容説明・投稿文生成。"""

    def __init__(self, api_key: str, model: str = DEFAULT_CLAUDE_MODEL):
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def describe_image(self, image_path: Path, system_prompt: str) -> str:
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
        return _extract_text(response)

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

    def describe_image(self, image_path: Path, system_prompt: str) -> str:
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
        return (response.choices[0].message.content or "").strip()

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

    return None
