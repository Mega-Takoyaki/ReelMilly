"""投稿ポリシー: プラットフォーム別コンテンツルールの検証(ADR-0006/0008/0009)。

`content_rating_confirmed`(人間の明示的承認)、プラットフォームが許可する
コンテンツ区分(`platform_content_rules`)、自動投稿してよい区分
(`platform_auto_post_ratings`)の3つをすべて満たした場合のみ、そのプラット
フォームへの自動投稿対象とする。「投稿先チャンネルとして登録されているか」
の判定はジョブ選定ロジック(Phase 4)側の責務とし、ここでは含めない。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AutoPostDecision:
    allowed: bool
    reason: str | None = None


def can_auto_post(
    asset: dict,
    platform: str,
    platform_content_rules: dict[str, list[str]],
    platform_auto_post_ratings: dict[str, list[str]],
) -> AutoPostDecision:
    """指定プラットフォームへの自動投稿可否を判定する。"""
    if not asset.get("content_rating_confirmed"):
        return AutoPostDecision(False, "content_rating が未承認のため自動投稿できません")

    content_rating = asset.get("content_rating")
    if not content_rating:
        return AutoPostDecision(False, "content_rating が未設定です")

    allowed_ratings = platform_content_rules.get(platform, [])
    if content_rating not in allowed_ratings:
        return AutoPostDecision(
            False, f"{platform} は content_rating={content_rating} を許可していません"
        )

    auto_post_ratings = platform_auto_post_ratings.get(platform, [])
    if content_rating not in auto_post_ratings:
        return AutoPostDecision(
            False,
            f"{platform} では content_rating={content_rating} は自動投稿対象外です（手動確認が必要）",
        )

    return AutoPostDecision(True)
