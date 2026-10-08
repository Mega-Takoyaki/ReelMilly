"""投稿文の生成と、形式の検査。

小さなモデル(ローカルの2B VLMなど)は、指示した形式(英語・「---」・日本語など)に従わないことがある。
そのまま公開されないよう、生成した文を検査し、外れていれば作り直す。それでもだめなら、失敗として返す
(自動投稿では、その作品の投稿を見送って通知する。「今すぐ投稿」では、手で書く/もう一度生成する)。
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from core import generation
from core import settings as settings_module

MAX_ATTEMPTS = 3
FANVUE_MAX_CHARS = 600
X_MAX_WEIGHT = 280  # Xの文字数: 全角(日本語)は2、半角は1として、280まで

_QUOTE_PAIRS = [('"', '"'), ("'", "'"), ("“", "”"), ("「", "」"), ("『", "』"), ("`", "`")]
_JAPANESE = re.compile(r"[぀-ヿ㐀-鿿]")
_LATIN = re.compile(r"[A-Za-z]")


class CaptionError(Exception):
    """投稿文を作れなかった(理由は、利用者に見せられる文)。"""


@dataclass
class CaptionResult:
    text: str
    attempts: int
    provider_note: str = ""


def clean(text: str) -> str:
    """前後の空白・コードブロック・全体を囲む引用符を取り除く。"""
    text = (text or "").strip()
    text = re.sub(r"^```[a-z]*\n?|\n?```$", "", text).strip()
    changed = True
    while changed and len(text) >= 2:
        changed = False
        for left, right in _QUOTE_PAIRS:
            if text.startswith(left) and text.endswith(right) and (left != right or text.count(left) == 2):
                text = text[len(left):-len(right)].strip()
                changed = True
    return text


def x_weight(text: str) -> int:
    """Xの文字数(全角=2、半角=1)。"""
    return sum(1 if ord(ch) < 0x2E80 else 2 for ch in text)


def check(text: str, channel: str) -> str | None:
    """形式の検査。問題が無ければNone、あれば理由。"""
    if not text:
        return "空です"
    if channel == "x":
        if x_weight(text) > X_MAX_WEIGHT:
            return f"Xの文字数の上限({X_MAX_WEIGHT})を超えています"
        return None
    # Fanvue: 「英語 / --- / 日本語」の形式
    if len(text) > FANVUE_MAX_CHARS:
        return "長すぎます"
    parts = [p.strip() for p in re.split(r"(?m)^\s*---\s*$", text)]
    if len(parts) != 2 or not all(parts):
        return "「英語 / --- / 日本語」の形式になっていません"
    english, japanese = parts
    if not _LATIN.search(english) or _JAPANESE.search(english):
        return "「---」の前が、英語になっていません"
    if not _JAPANESE.search(japanese):
        return "「---」の後が、日本語になっていません"
    return None


def channel_prompt(conn: sqlite3.Connection, channel: str) -> str:
    if channel == "x":
        return settings_module.get_x_caption_system_prompt(conn)
    return settings_module.get_caption_system_prompt(conn)


def generate(
    conn: sqlite3.Connection,
    assets: list[dict],
    channel: str = "fanvue",
    generator=None,
) -> CaptionResult:
    """作品(複数なら、まとめて1つの投稿として)の投稿文を、チャンネル(fanvue|x)のプロンプトで生成する。

    形式の検査に通るまで、最大`MAX_ATTEMPTS`回やり直す。作れなければ`CaptionError`。
    `generator`を渡すと、それを使う(テスト用)。無ければ、設定の「投稿文の生成に使うモデル」で作る
    (クラウドのモデルは、sfwの作品だけに使う)。
    """
    descriptions = [a.get("content_description") for a in assets if a.get("content_description")]
    if not descriptions:
        raise CaptionError("内容説明がありません。先に、AI処理（説明文生成）を実行してください")
    description = "\n".join(descriptions) if len(descriptions) == 1 else "\n".join(
        f"作品{i}: {d}" for i, d in enumerate(descriptions, 1)
    ) + "\n（上の作品を、まとめて1つの投稿にします）"
    if generator is None:
        ratings = [a.get("content_rating") if a.get("content_rating_confirmed") else None for a in assets]
        generator = generation.try_create_caption_generator(conn, ratings)
    if generator is None:
        raise CaptionError("生成AIが使えません（設定の「生成AI」で、APIキーやモデルを確認してください）")
    prompt = channel_prompt(conn, channel)

    last_problem = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = generator.generate_caption(description, prompt)
        except generation.GenerationError as exc:
            raise CaptionError(f"生成に失敗しました: {exc}") from exc
        text = clean(raw)
        problem = check(text, channel)
        if problem is None:
            return CaptionResult(text=text, attempts=attempt)
        last_problem = problem
    raise CaptionError(f"形式に合う文を作れませんでした（{MAX_ATTEMPTS}回試して、「{last_problem}」）。もう一度試すか、手で書いてください")
