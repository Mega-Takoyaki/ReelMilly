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
# 設定画面のモデル選択肢(プロバイダー別)。既定値は先頭に置く
GENERATION_MODEL_CHOICES = {
    "claude": [
        "claude-opus-5",
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "claude-haiku-4-5-20251001",
    ],
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4.1"],
    "local": [
        "prithivMLmods/Qwen2-VL-2B-Abliterated-Caption-it",
        "Minthy/ToriiGate-v0.4-2B",
    ],
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


def get_model_choices(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """プロバイダー別のモデル選択肢。claude/openaiは取得済みキャッシュがあればそれを使う。"""
    import json

    choices = {provider: list(models) for provider, models in GENERATION_MODEL_CHOICES.items()}
    for provider in ("claude", "openai", "local"):
        raw = db.get_setting(conn, f"model_cache_{provider}")
        if raw:
            try:
                cached = json.loads(raw)
            except ValueError:
                continue
            if isinstance(cached, list) and cached:
                choices[provider] = [str(m) for m in cached]
    return choices


def set_model_cache(conn: sqlite3.Connection, provider: str, models: list[str]) -> None:
    import json

    db.set_setting(conn, f"model_cache_{provider}", json.dumps(models))


# --- AI処理の定期実行・タグカテゴリ -------------------------------------------

AI_SCHEDULE_KINDS = ("nsfw", "describe")
AI_KIND_LABELS = {"nsfw": "sfw/nsfw判定", "describe": "説明文生成・タグ付与"}
DEFAULT_SCHEDULE = {"enabled": False, "time": "03:00", "scope": "all", "days": 7}


def _clean_schedule(entry: dict) -> dict | None:
    import re
    import secrets

    kind = entry.get("kind")
    if kind not in AI_SCHEDULE_KINDS:
        return None
    time = entry.get("time") or ""
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time):
        time = DEFAULT_SCHEDULE["time"]
    scope = entry.get("scope") if entry.get("scope") in ("all", "days") else "all"
    try:
        days = max(1, int(entry.get("days")))
    except (TypeError, ValueError):
        days = DEFAULT_SCHEDULE["days"]
    return {
        "id": str(entry.get("id") or secrets.token_hex(4)),
        "kind": kind,
        "enabled": bool(entry.get("enabled")),
        "time": time,
        "scope": scope,
        "days": days,
    }


def get_ai_schedules(conn: sqlite3.Connection) -> list[dict]:
    """AI処理の定期実行スケジュール(複数登録できる)。

    各要素は{"id", "kind"("nsfw"/"describe"), "enabled", "time"("HH:MM"),
    "scope"("all"=全未処理 / "days"=直近`days`日以内に登録された未処理), "days"}。
    旧形式(種別ごとに1つ)の設定が残っていれば、初回に一覧形式へ引き継ぐ。
    """
    import json

    raw = db.get_setting(conn, "schedules")
    if raw is not None:
        try:
            return [e for e in (_clean_schedule(x) for x in json.loads(raw)) if e]
        except (ValueError, TypeError, AttributeError):
            return []
    legacy = []
    for kind in AI_SCHEDULE_KINDS:
        old = db.get_setting(conn, f"schedule_{kind}")
        if old:
            try:
                legacy.append(_clean_schedule({**json.loads(old), "kind": kind, "id": f"legacy-{kind}"}))
            except ValueError:
                pass
    return [e for e in legacy if e]


def set_ai_schedules(conn: sqlite3.Connection, entries: list[dict]) -> None:
    import json

    cleaned = [e for e in (_clean_schedule(x) for x in entries) if e]
    db.set_setting(conn, "schedules", json.dumps(cleaned))


def get_tag_categories(conn: sqlite3.Connection) -> list[dict]:
    """タグ生成のカテゴリ一覧。各要素は{"name": 例"服装", "options": 例"水着, 制服"(任意の候補)}。"""
    import json

    raw = db.get_setting(conn, "tag_categories")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    return [
        {"name": str(c.get("name", "")).strip(), "options": str(c.get("options", "")).strip()}
        for c in data
        if isinstance(c, dict) and str(c.get("name", "")).strip()
    ]


def set_tag_categories(conn: sqlite3.Connection, categories: list[dict]) -> None:
    import json

    cleaned = [
        {"name": (c.get("name") or "").strip(), "options": (c.get("options") or "").strip()}
        for c in categories
        if (c.get("name") or "").strip()
    ]
    db.set_setting(conn, "tag_categories", json.dumps(cleaned, ensure_ascii=False))


def tag_category_instructions(conn: sqlite3.Connection) -> str:
    """説明文生成のシステムプロンプトへ追記する、タグカテゴリの指示文(カテゴリ未設定なら空)。"""
    categories = get_tag_categories(conn)
    if not categories:
        return ""
    lines = []
    for c in categories:
        hint = f"（候補: {c['options']}）" if c["options"] else ""
        lines.append(f"- {c['name']}{hint}")
    return (
        "\n\n【タグのカテゴリ】次の各カテゴリについて、画像に当てはまるタグを1つずつ選び、"
        "最後の「タグ:」行に「カテゴリ名=タグ」の形式でカンマ区切りで並べてください"
        "（判断できないカテゴリは省略してよい）。\n" + "\n".join(lines)
    )
