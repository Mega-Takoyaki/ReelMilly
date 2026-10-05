"""投稿先(SNS等)の表示定義。投稿状態は`posts`テーブルに作品x投稿先ごとに記録し、
画面では投稿先ごとの小さなアイコン(フラグ)で表す。投稿先を増やすときはここに足す。
"""
from __future__ import annotations

POST_CHANNELS: dict[str, dict[str, str]] = {
    "fanvue": {"label": "Fanvue", "letter": "F", "color": "#4f6df5"},
    "x": {"label": "X", "letter": "X", "color": "#111827"},
}

POST_STATUS_LABELS = {"posted": "投稿済み", "failed": "投稿に失敗", "none": "未投稿"}
