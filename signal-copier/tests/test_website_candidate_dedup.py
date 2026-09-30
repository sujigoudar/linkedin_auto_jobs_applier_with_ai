"""Track 9: URL-based dedup/revision-update for article trade candidates
-- an article revision (same canonical URL, later modified_at) must
update the EXISTING candidate record, never create a duplicate. Mirrors
`find_signal_id_by_provider_identity`'s own dedup convention, keyed on
this source's (channel_id, message_id) == (site_id, canonical URL)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.db import SignalStore


@pytest.fixture
def store(tmp_path: Path) -> SignalStore:
    return SignalStore(tmp_path / "test.db")


def test_first_observation_creates_a_new_candidate_row(store):
    row = store.upsert_website_candidate(
        channel_id="investors_com_swingtrader",
        message_id="https://example.com/articles/we-are-buying-xyz",
        classification="actionable_recommendation",
        resolved=True,
        candidate_json={"symbol": "XYZ", "side": "buy"},
        published_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        modified_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    assert row["channel_id"] == "investors_com_swingtrader"
    assert row["message_id"] == "https://example.com/articles/we-are-buying-xyz"
    assert row["candidate"]["symbol"] == "XYZ"

    found = store.find_website_candidate_by_url(
        channel_id="investors_com_swingtrader", message_id="https://example.com/articles/we-are-buying-xyz"
    )
    assert found is not None
    assert found["id"] == row["id"]


def test_same_url_later_modified_at_updates_existing_row_not_a_duplicate(store):
    url = "https://example.com/articles/we-are-buying-xyz"
    first = store.upsert_website_candidate(
        channel_id="site",
        message_id=url,
        classification="actionable_recommendation",
        resolved=True,
        candidate_json={"symbol": "XYZ", "stop_loss": 48.0},
        modified_at=datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
    )
    second = store.upsert_website_candidate(
        channel_id="site",
        message_id=url,
        classification="actionable_recommendation",
        resolved=True,
        candidate_json={"symbol": "XYZ", "stop_loss": 46.0},  # the article was revised
        modified_at=datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc),
    )

    assert second["id"] == first["id"]  # same row, not a new one
    assert second["candidate"]["stop_loss"] == 46.0

    all_rows = _all_candidate_rows(store)
    assert len(all_rows) == 1


def test_redelivery_of_the_same_unmodified_article_is_a_no_op(store):
    url = "https://example.com/articles/unchanged"
    first = store.upsert_website_candidate(
        channel_id="site",
        message_id=url,
        classification="actionable_recommendation",
        resolved=True,
        candidate_json={"symbol": "ABC"},
        modified_at=datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
    )
    redelivered = store.upsert_website_candidate(
        channel_id="site",
        message_id=url,
        classification="actionable_recommendation",
        resolved=True,
        candidate_json={"symbol": "ABC", "stray_field": "should not apply"},
        modified_at=datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),  # same, not newer
    )
    assert redelivered["id"] == first["id"]
    assert "stray_field" not in redelivered["candidate"]


def test_different_urls_never_collide(store):
    store.upsert_website_candidate(
        channel_id="site", message_id="https://example.com/a1", classification="actionable_recommendation",
        resolved=True, candidate_json={"symbol": "A"},
    )
    store.upsert_website_candidate(
        channel_id="site", message_id="https://example.com/a2", classification="actionable_recommendation",
        resolved=True, candidate_json={"symbol": "B"},
    )
    assert len(_all_candidate_rows(store)) == 2


def _all_candidate_rows(store: SignalStore) -> list[dict]:
    with store._connect() as conn:
        rows = conn.execute("SELECT id FROM website_article_candidates").fetchall()
    return rows
