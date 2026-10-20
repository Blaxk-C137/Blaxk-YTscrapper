from app.scraper_service import expanded_query, matches_preferences


STATS = {
    "subscribers": "84.2K",
    "videos": "136",
    "account_year": 2019,
}
EMAILS = ["hello@creator.example"]
BUSINESS_EMAILS = ["business@creator.example"]
SOCIALS = {"instagram": ["creator"]}


def matches(preferences, *, stats=STATS, business=BUSINESS_EMAILS, emails=EMAILS, socials=SOCIALS):
    return matches_preferences(
        stats, business, emails, socials, preferences, current_year=2026
    )


def test_search_query_expansions_keep_the_selected_niche():
    assert expanded_query("lifestyle", "Nigeria", "base") == "lifestyle"
    assert expanded_query("lifestyle", "Nigeria", "creator") == "lifestyle creator"
    assert expanded_query("lifestyle", "Nigeria", "region") == "lifestyle Nigeria"


def test_creator_matches_audience_upload_and_age_preferences():
    accepted, reason = matches({
        "min_subscribers": 10_000,
        "max_subscribers": 100_000,
        "min_videos": 50,
        "min_channel_age": 5,
        "contact_requirement": "email",
    })
    assert accepted is True
    assert reason == "matched"


def test_creator_below_subscriber_minimum_is_filtered():
    accepted, reason = matches({"min_subscribers": 100_000})
    assert accepted is False
    assert reason == "below_min_subscribers"


def test_creator_above_subscriber_maximum_is_filtered():
    accepted, reason = matches({"max_subscribers": 50_000})
    assert accepted is False
    assert reason == "above_max_subscribers"


def test_upload_and_channel_age_filters_are_enforced():
    assert matches({"min_videos": 200})[1] == "below_min_videos"
    assert matches({"min_channel_age": 10})[1] == "channel_too_new_or_age_unknown"


def test_contact_requirement_is_enforced():
    assert matches({"contact_requirement": "email"}, emails=[])[1] == "email_required"
    assert matches({"contact_requirement": "business_email"}, business=[])[1] == "revealed_business_email_required"
    assert matches({"contact_requirement": "social"}, socials={})[1] == "social_required"


def test_unknown_metrics_do_not_pass_bounded_preferences():
    accepted, reason = matches(
        {"min_subscribers": 1_000},
        stats={"videos": "10", "account_year": 2020},
    )
    assert accepted is False
    assert reason == "subscriber_count_unknown"
