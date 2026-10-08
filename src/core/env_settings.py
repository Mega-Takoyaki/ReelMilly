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
    ("FANVUE_OAUTH_CLIENT_ID", "Fanvue OAuthアプリのClient ID", False),
    ("FANVUE_OAUTH_CLIENT_SECRET", "Fanvue OAuthアプリのClient Secret", True),
    ("FANVUE_OAUTH_REDIRECT_URI", "Fanvue OAuthリダイレクトURI（既定のままで通常は変更不要）", False),
    ("FANVUE_HANDLE", "Fanvueハンドル（アカウント名）", False),
    ("FANVUE_POST_URL_TEMPLATE", "Fanvue投稿URLテンプレート", False),
    ("FANVUE_API_BASE_URL", "Fanvue APIベースURL（既定のままで通常は変更不要）", False),
    ("FANVUE_API_VERSION", "Fanvue APIバージョン（既定のままで通常は変更不要）", False),
    ("TELEGRAM_BOT_TOKEN", "Telegram Botトークン（Telegram連携は未実装、値の保存のみ可能）", True),
    ("TELEGRAM_ALLOWED_CHAT_ID", "Telegram許可チャットID", False),
    ("ANTHROPIC_API_KEY", "Anthropic(Claude) APIキー", True),
    ("OPENAI_API_KEY", "OpenAI APIキー", True),
]

# 設定画面の「ⓘ」アイコンに表示する、初見でも分かる項目の説明
CONNECTION_HELP = {
    "FANVUE_OAUTH_CLIENT_ID": (
        "FanvueのDeveloper画面でOAuthアプリを作成すると発行される「Client ID」です。"
        "このアプリがFanvueへ投稿する許可をもらうための、アプリ側の名前札のようなものです。"
    ),
    "FANVUE_OAUTH_CLIENT_SECRET": (
        "OAuthアプリと一緒に発行される「Client Secret」です。アプリのパスワードにあたるので、他人に見せないでください。"
        "画面には表示されず、空欄のまま保存すると今の値は変わりません。"
    ),
    "FANVUE_OAUTH_REDIRECT_URI": (
        "Fanvueでログインを許可したあと、このアプリへ戻ってくるアドレスです。"
        "Fanvue側のOAuthアプリに登録した値と完全に同じである必要があります。"
        "空欄なら、設定のhost/portから決まる http://127.0.0.1:8420/settings/fanvue/oauth/callback が使われます(「Fanvue」タブに、いまの値を表示しています)。通常は変更不要です。"
    ),
    "FANVUE_HANDLE": (
        "Fanvueのプロフィールのアドレス https://www.fanvue.com/○○ の「○○」の部分（アカウント名）です。"
        "下の「投稿URLテンプレート」の {handle} に入ります。"
    ),
    "FANVUE_POST_URL_TEMPLATE": (
        "投稿後に記録する「公開URL」の組み立て方です（FanvueのAPIは投稿の公開URLを返さないため、自分で組み立てます）。"
        "{handle} はハンドル、{uuid} は投稿のIDに置き換わります。"
        "例: https://www.fanvue.com/{handle}（プロフィールへのリンク。迷ったらこのままで大丈夫です）。"
        "投稿1件ごとのURLにしたい場合は、Fanvueで実際の投稿を開いてURLを確認し、その形に合わせてください。"
        "今はXへの紹介投稿が未実装のため、このURLは記録されるだけです。"
    ),
    "FANVUE_API_BASE_URL": "FanvueのAPIの接続先です。通常は https://api.fanvue.com のままで変更不要です。",
    "FANVUE_API_VERSION": "Fanvue APIのバージョン指定（X-Fanvue-API-Versionヘッダ）です。通常は既定値のままで変更不要です。",
    "TELEGRAM_BOT_TOKEN": (
        "TelegramのBotFatherでBotを作ると発行されるトークンです。Telegram連携は未実装のため、今は保存だけできます。"
        "秘密の値なので画面には表示されません。"
    ),
    "TELEGRAM_ALLOWED_CHAT_ID": (
        "操作を許可する自分のTelegramチャットIDです。このID以外からの操作は無視する用途です（連携は未実装）。"
    ),
    "ANTHROPIC_API_KEY": (
        "Claude APIを使うためのAPIキーです（AnthropicのConsoleで発行）。従量課金になります。"
        "画像の説明文生成でClaudeを選んだときに使います。秘密の値なので画面には表示されません。"
    ),
    "OPENAI_API_KEY": (
        "OpenAI APIを使うためのAPIキーです（OpenAIのダッシュボードで発行）。従量課金になります。"
        "OpenAIを選んだときのみ使います。秘密の値なので画面には表示されません。"
    ),
}

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
            "help": CONNECTION_HELP.get(key, ""),
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


def read_env_value(env_path: Path, key: str) -> str:
    """`.env`から1項目の実際の値を読む(サーバー内部での利用専用。画面には返さない)。"""
    values = dotenv_values(env_path) if env_path.exists() else {}
    return values.get(key) or ""
