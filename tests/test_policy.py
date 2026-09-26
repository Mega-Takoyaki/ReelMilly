from core.policy import can_auto_post

PLATFORM_CONTENT_RULES = {
    "fanvue": ["sfw", "suggestive", "explicit"],
    "x": ["sfw", "suggestive", "explicit"],
    "instagram": ["sfw"],
}

PLATFORM_AUTO_POST_RATINGS = {
    "fanvue": ["sfw", "suggestive", "explicit"],
    "x": ["sfw"],
}


def _asset(**overrides):
    asset = {
        "content_rating": "sfw",
        "content_rating_confirmed": 1,
    }
    asset.update(overrides)
    return asset


def test_unconfirmed_asset_is_never_auto_posted():
    asset = _asset(content_rating_confirmed=0)

    decision = can_auto_post(asset, "fanvue", PLATFORM_CONTENT_RULES, PLATFORM_AUTO_POST_RATINGS)

    assert decision.allowed is False
    assert "未承認" in decision.reason


def test_missing_content_rating_is_rejected():
    asset = _asset(content_rating=None)

    decision = can_auto_post(asset, "fanvue", PLATFORM_CONTENT_RULES, PLATFORM_AUTO_POST_RATINGS)

    assert decision.allowed is False


def test_fanvue_allows_all_ratings_automatically():
    for rating in ["sfw", "suggestive", "explicit"]:
        asset = _asset(content_rating=rating)
        decision = can_auto_post(asset, "fanvue", PLATFORM_CONTENT_RULES, PLATFORM_AUTO_POST_RATINGS)
        assert decision.allowed is True, rating


def test_x_only_auto_posts_sfw():
    sfw_asset = _asset(content_rating="sfw")
    assert can_auto_post(sfw_asset, "x", PLATFORM_CONTENT_RULES, PLATFORM_AUTO_POST_RATINGS).allowed is True

    explicit_asset = _asset(content_rating="explicit")
    decision = can_auto_post(explicit_asset, "x", PLATFORM_CONTENT_RULES, PLATFORM_AUTO_POST_RATINGS)
    assert decision.allowed is False
    assert "自動投稿対象外" in decision.reason


def test_instagram_rejects_content_not_allowed_at_all():
    asset = _asset(content_rating="explicit")

    decision = can_auto_post(asset, "instagram", PLATFORM_CONTENT_RULES, PLATFORM_AUTO_POST_RATINGS)

    assert decision.allowed is False
    assert "許可していません" in decision.reason


def test_unknown_platform_defaults_to_disallowed():
    asset = _asset(content_rating="sfw")

    decision = can_auto_post(asset, "unknown-platform", PLATFORM_CONTENT_RULES, PLATFORM_AUTO_POST_RATINGS)

    assert decision.allowed is False
