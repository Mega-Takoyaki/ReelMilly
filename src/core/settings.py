"""ユーザー調整可能な生成設定(ADR-0015)・watch動作設定(ADR-0020)。

初期値はこのモジュールの定数として持ち、本体UIの設定画面(`/settings`)から
`settings`テーブル(`core.db`)へ保存した値があればそちらを優先する。
APIキーなどの秘密情報はここでは扱わない(`.env`のみ、環境変数から読む)。
"""
from __future__ import annotations

import sqlite3

from core import db

DEFAULT_DESCRIPTION_SYSTEM_PROMPT = (
    "あなたは画像の内容を客観的に説明するアシスタントです。"
    "写っている人物の服装・表情・ポーズ・背景・シーンの雰囲気を、"
    "日本語の簡潔な文章で説明してください。過度な性的表現は避け、事実の描写に徹してください。"
    "続けて、画像を分類・検索する際に役立つ日本語のタグを3〜5個、カンマ区切りで挙げてください。"
    "出力は必ず次の2行の形式にしてください（他の文章は含めないでください）。\n"
    "説明: <内容説明>\n"
    "タグ: <タグ1>, <タグ2>, <タグ3>"
)

DEFAULT_CAPTION_SYSTEM_PROMPT = (
    "あなたはFanvueクリエイターの投稿文を作成するアシスタントです。"
    "与えられた画像の内容説明をもとに、ファンに向けた魅力的で自然な日本語の投稿文を1〜3文程度で作成してください。"
    "絵文字は控えめにし、内容説明にない事実を作り話しないでください。"
)

DEFAULT_GENERATION_PROVIDER = "claude"
DEFAULT_GENERATION_MODEL = {
    "claude": "claude-opus-5",
    "openai": "gpt-4o",
    "local": "prithivMLmods/Qwen2-VL-2B-Abliterated-Caption-it",
}
DEFAULT_CAPTION_MODE = "auto"  # "auto" | "draft"
DEFAULT_AUTO_INGEST = False  # ADR-0020: watchループでのフォルダ自動取り込み

_KEY_DESCRIPTION_PROMPT = "description_system_prompt"
_KEY_CAPTION_PROMPT = "caption_system_prompt"
_KEY_PROVIDER = "generation_provider"
_KEY_MODEL = "generation_model"
_KEY_CAPTION_MODE = "caption_mode"
_KEY_AUTO_INGEST = "watch_auto_ingest"


def get_description_system_prompt(conn: sqlite3.Connection) -> str:
    return db.get_setting(conn, _KEY_DESCRIPTION_PROMPT) or DEFAULT_DESCRIPTION_SYSTEM_PROMPT


def get_caption_system_prompt(conn: sqlite3.Connection) -> str:
    return db.get_setting(conn, _KEY_CAPTION_PROMPT) or DEFAULT_CAPTION_SYSTEM_PROMPT


def get_generation_provider(conn: sqlite3.Connection) -> str:
    return db.get_setting(conn, _KEY_PROVIDER) or DEFAULT_GENERATION_PROVIDER


def get_generation_model(conn: sqlite3.Connection) -> str:
    stored = db.get_setting(conn, _KEY_MODEL)
    if stored:
        return stored
    provider = get_generation_provider(conn)
    return DEFAULT_GENERATION_MODEL.get(provider, "")


def get_caption_mode(conn: sqlite3.Connection) -> str:
    return db.get_setting(conn, _KEY_CAPTION_MODE) or DEFAULT_CAPTION_MODE


def get_auto_ingest(conn: sqlite3.Connection) -> bool:
    stored = db.get_setting(conn, _KEY_AUTO_INGEST)
    if stored is None:
        return DEFAULT_AUTO_INGEST
    return stored == "1"


def set_auto_ingest(conn: sqlite3.Connection, enabled: bool) -> None:
    db.set_setting(conn, _KEY_AUTO_INGEST, "1" if enabled else "0")


def get_all_settings(conn: sqlite3.Connection) -> dict:
    return {
        "description_system_prompt": get_description_system_prompt(conn),
        "caption_system_prompt": get_caption_system_prompt(conn),
        "generation_provider": get_generation_provider(conn),
        "generation_model": get_generation_model(conn),
        "caption_mode": get_caption_mode(conn),
        "auto_ingest": get_auto_ingest(conn),
    }


_KEY_MAP = {
    "description_system_prompt": _KEY_DESCRIPTION_PROMPT,
    "caption_system_prompt": _KEY_CAPTION_PROMPT,
    "generation_provider": _KEY_PROVIDER,
    "generation_model": _KEY_MODEL,
    "caption_mode": _KEY_CAPTION_MODE,
}


def update_settings(conn: sqlite3.Connection, **kwargs) -> None:
    for name, value in kwargs.items():
        if value is None or value == "":
            continue
        db.set_setting(conn, _KEY_MAP[name], value)
