"""Track 9: the persistent website collector registry -- CRUD,
qualification-evidence recording, checkpoint persistence, and every
declared health state (point 4/8 of the brief)."""
from pathlib import Path

import pytest

from app.db import SignalStore
from app.website_collectors import (
    AllowedUse,
    CollectorHealth,
    WebsiteCollectorError,
    validate_registration,
)


@pytest.fixture
def store(tmp_path: Path) -> SignalStore:
    return SignalStore(tmp_path / "test.db")


def test_register_feed_collector_defaults_to_unqualified_and_private_trading_only(store):
    row = store.register_website_collector(
        collector_id="ibd_swingtrader",
        site_format="feed",
        site_id="investors_com_swingtrader",
        provider_name="investors_com",
        feed_url="https://example.com/swingtrader/feed/",
    )
    assert row["id"] == "ibd_swingtrader"
    assert row["site_format"] == "feed"
    assert row["allowed_uses"] == ["private_trading"]
    assert row["health_state"] == "unqualified"
    assert row["checkpoint_article_url"] is None
    assert row["checkpoint_seen_urls"] == []


def test_register_rejects_unknown_site_format(store):
    with pytest.raises(WebsiteCollectorError):
        store.register_website_collector(
            collector_id="x",
            site_format="carrier_pigeon",
            site_id="x",
            provider_name="x",
            feed_url="https://example.com/feed/",
        )


def test_register_feed_format_requires_feed_url(store):
    with pytest.raises(WebsiteCollectorError):
        store.register_website_collector(
            collector_id="x", site_format="feed", site_id="x", provider_name="x"
        )


def test_register_article_list_format_requires_article_list_url(store):
    with pytest.raises(WebsiteCollectorError):
        store.register_website_collector(
            collector_id="x", site_format="article_list", site_id="x", provider_name="x"
        )


def test_register_article_list_collector(store):
    row = store.register_website_collector(
        collector_id="ibd_options",
        site_format="article_list",
        site_id="investors_com_options_column",
        provider_name="investors_com",
        article_list_url="https://example.com/options-column/",
    )
    assert row["site_format"] == "article_list"
    assert row["article_list_url"] == "https://example.com/options-column/"


def test_never_defaults_to_commercial_redistribution(store):
    row = store.register_website_collector(
        collector_id="x",
        site_format="feed",
        site_id="x",
        provider_name="x",
        feed_url="https://example.com/feed/",
    )
    assert AllowedUse.COMMERCIAL_REDISTRIBUTION.value not in row["allowed_uses"]


def test_reregistering_preserves_qualification_and_checkpoint(store):
    store.register_website_collector(
        collector_id="x", site_format="feed", site_id="x", provider_name="x", feed_url="https://example.com/feed/"
    )
    store.record_website_collector_qualification_evidence(
        "x", evidence={"observed_url": "https://example.com/a1", "method": "live_poll"}
    )
    store.advance_website_collector_checkpoint("x", article_url="https://example.com/a1")

    row = store.register_website_collector(
        collector_id="x",
        site_format="feed",
        site_id="x",
        provider_name="x",
        feed_url="https://example.com/new-feed/",
    )
    assert row["feed_url"] == "https://example.com/new-feed/"
    assert row["health_state"] == "healthy_qualified"
    assert row["checkpoint_article_url"] == "https://example.com/a1"


@pytest.mark.parametrize(
    "health",
    [
        CollectorHealth.MISSING_CONFIGURATION,
        CollectorHealth.NO_NEW_ARTICLES_OBSERVED,
        CollectorHealth.ACCESS_DENIED_OR_PAYWALLED,
        CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED,
        CollectorHealth.CLASSIFIER_UNCERTAIN,
        CollectorHealth.PARSER_FAILURE,
        CollectorHealth.HEALTHY_QUALIFIED,
    ],
)
def test_every_declared_health_state_is_settable_and_visible(store, health):
    store.register_website_collector(
        collector_id="x", site_format="feed", site_id="x", provider_name="x", feed_url="https://example.com/feed/"
    )
    store.update_website_collector_health("x", health.value, detail="test detail")
    row = store.get_website_collector("x")
    assert row["health_state"] == health.value
    assert row["health_detail"] == "test detail"


def test_update_health_rejects_unrecognized_state(store):
    store.register_website_collector(
        collector_id="x", site_format="feed", site_id="x", provider_name="x", feed_url="https://example.com/feed/"
    )
    with pytest.raises(ValueError):
        store.update_website_collector_health("x", "definitely_not_a_real_state")


def test_checkpoint_accumulates_seen_urls(store):
    store.register_website_collector(
        collector_id="x",
        site_format="article_list",
        site_id="x",
        provider_name="x",
        article_list_url="https://example.com/list/",
    )
    store.advance_website_collector_checkpoint("x", article_url="https://example.com/a1")
    store.advance_website_collector_checkpoint("x", article_url="https://example.com/a2")
    seen = store.get_website_collector_seen_urls("x")
    assert seen == {"https://example.com/a1", "https://example.com/a2"}


def test_registering_a_collector_never_touches_commercial_rights(store):
    """Isolation mirror of the Telegram registry's own identically-named
    test -- registering here must never grant, or be able to imply,
    eligibility for signal-portfolio-commercial's publication pipeline."""
    row = store.register_website_collector(
        collector_id="x", site_format="feed", site_id="x", provider_name="x", feed_url="https://example.com/feed/"
    )
    assert row["allowed_uses"] == [AllowedUse.PRIVATE_TRADING.value]


def test_validate_registration_rejects_blank_required_fields():
    with pytest.raises(WebsiteCollectorError):
        validate_registration(
            collector_id="",
            site_format="feed",
            site_id="x",
            provider_name="x",
            feed_url="https://example.com/feed/",
            article_list_url=None,
            allowed_uses=None,
        )
