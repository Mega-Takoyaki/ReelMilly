"""SNS/生成AIの接続設定(APIキー・アカウント名等)を`.env`で読み書きする。

秘密情報は`.env`のみに置くという方針（README参照）を維持するため、DBの
`settings`テーブル（`core/settings.py`、システムプロンプト等の非秘密設定用）
とは別に、このモジュールで`.env`ファイルを直接読み書きする。
"""
from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values, load_dotenv, set_key

# (env変数名, 表示ラベル, 秘密情報かどうか)
CONNECTION_FIELDS = [
    ("FANVUE_API_TOKEN", "Fanvue APIトークン", True),
    ("FANVUE_HANDLE", "Fanvueハンドル（アカウント名）", False),
    ("FANVUE_POST_URL_TEMPLATE", "Fanvue投稿URLテンプレート", False),
    ("FANVUE_API_BASE_URL", "Fanvue APIベースURL（既定のままで通常は変更不要）", False),
    ("FANVUE_API_VERSION", "Fanvue APIバージョン（既定のままで通常は変更不要）", False),
    ("TELEGRAM_BOT_TOKEN", "Telegram Botトークン（Telegram連携は未実装、値の保存のみ可能）", True),
    ("TELEGRAM_ALLOWED_CHAT_ID", "Telegram許可チャットID", False),
    ("ANTHROPIC_API_KEY", "Anthropic(Claude) APIキー", True),
    ("OPENAI_API_KEY", "OpenAI APIキー", True),
]

CONNECTION_ENV_KEYS = [key for key, _label, _secret in CONNECTION_FIELDS]
SECRET_ENV_KEYS = {key for key, _label, secret in CONNECTION_FIELDS if secret}


def read_connection_status(env_path: Path) -> dict[str, dict]:
    """各接続設定項目の現在値(非秘密)・設定有無(秘密)を返す。

    戻り値: {key: {"label": str, "secret": bool, "value": str, "is_set": bool}}
    秘密項目は`value`を常に空文字にし、画面に実際のトークンを表示しない。
    """
    values = dotenv_values(env_path) if env_path.exists() else {}
    result = {}
    for key, label, is_secret in CONNECTION_FIELDS:
        raw_value = values.get(key) or ""
        result[key] = {
            "label": label,
            "secret": is_secret,
            "value": "" if is_secret else raw_value,
            "is_set": bool(raw_value),
        }
    return result


def update_connection_values(env_path: Path, updates: dict[str, str]) -> None:
    """`.env`に接続設定を書き込む。

    空文字が渡された項目は既存の値を変更しない（フォームを空欄のまま送信して
    既存のトークンを誤って消してしまうことを防ぐため）。対象外のキーは無視する。
    """
    env_path.parent.mkdir(parents=True, exist_ok=True)
    if not env_path.exists():
        env_path.touch()

    changed = False
    for key, value in updates.items():
        if key not in CONNECTION_ENV_KEYS:
            continue
        if not value:
            continue
        set_key(str(env_path), key, value)
        changed = True

    if changed:
        load_dotenv(env_path, override=True)
