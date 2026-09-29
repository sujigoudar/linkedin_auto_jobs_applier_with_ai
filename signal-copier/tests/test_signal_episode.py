"""app/signal_episode.py: SignalEpisode correlation across a realistic
revision sequence from one analyst (the GOLD walkthrough from the batch B
spec), the AMBIGUOUS-never-auto-correlates invariant, and the
identity-insufficient fallback (a source with no analyst always creates a
fresh episode rather than guessing).
"""
from __future__ import annotations

from app.db import SignalStore
from app.models import AssetClass, Side, Signal
from app.signal_episode import (
    EpisodeCorrelator,
    EpisodeLifecycleState,
    RevisionEventType,
    classify_revision_event,
)


def _signal(text: str, *, source: str = "telegram", analyst: str | None = "analyst_a", symbol: str = "GOLD") -> Signal:
    return Signal(source=source, symbol=symbol, side=Side.BUY, asset_class=AssetClass.FOREX, analyst=analyst, raw={"text": text})


# --- classify_revision_event ---------------------------------------------------


def test_classifies_new_entry():
    c = classify_revision_event("BUY GOLD 2350")
    assert c.event_type is RevisionEventType.NEW_ENTRY
    assert c.symbol == "GOLD"
    assert c.side is Side.BUY


def test_classifies_change_stop():
    c = classify_revision_event("SL 2342")
    assert c.event_type is RevisionEventType.CHANGE_STOP
    assert c.stop_loss == 2342.0


def test_classifies_move_to_breakeven():
    c = classify_revision_event("move SL to entry")
    assert c.event_type is RevisionEventType.MOVE_TO_BREAKEVEN
    assert c.stop_target == "breakeven"


def test_classifies_close_percent_half():
    c = classify_revision_event("close half")
    assert c.event_type is RevisionEventType.CLOSE_PERCENT
    assert c.close_fraction == 0.5


def test_classifies_close_percent_number():
    c = classify_revision_event("close 25%")
    assert c.event_type is RevisionEventType.CLOSE_PERCENT
    assert c.close_fraction == 0.25


def test_classifies_close_all():
    c = classify_revision_event("close all")
    assert c.event_type is RevisionEventType.CLOSE_ALL


def test_classifies_cancel_entry():
    c = classify_revision_event("cancel the order")
    assert c.event_type is RevisionEventType.CANCEL_ENTRY


def test_classifies_add_target():
    c = classify_revision_event("add tp 2360")
    assert c.event_type is RevisionEventType.ADD_TARGET
    assert c.take_profit == 2360.0


def test_classifies_add_entry():
    c = classify_revision_event("add more at 2340")
    assert c.event_type is RevisionEventType.ADD_ENTRY
    assert c.price == 2340.0


def test_classifies_trail():
    c = classify_revision_event("activate trailing stop")
    assert c.event_type is RevisionEventType.TRAIL


def test_classifies_no_action():
    c = classify_revision_event("hold")
    assert c.event_type is RevisionEventType.NO_ACTION


def test_classifies_commentary_for_unrecognized_chatter():
    c = classify_revision_event("still looks strong here, watching resistance")
    assert c.event_type is RevisionEventType.COMMENTARY


def test_classifies_ambiguous_for_two_side_keywords():
    c = classify_revision_event("BUY AAPL 10 and SELL MSFT 5")
    assert c.event_type is RevisionEventType.AMBIGUOUS


# --- EpisodeCorrelator: the GOLD walkthrough ------------------------------------


def test_gold_walkthrough_correlates_the_full_revision_sequence():
    correlator = EpisodeCorrelator()

    new_entry = correlator.ingest(_signal("BUY GOLD 2350"))
    assert new_entry.created_new is True
    episode_id = new_entry.episode.episode_id
    assert new_entry.episode.instrument == "GOLD"
    assert new_entry.episode.revision == 1

    stop_move = correlator.ingest(_signal("SL 2342"))
    assert stop_move.episode is not None
    assert stop_move.episode.episode_id == episode_id
    assert stop_move.episode.revision == 2
    assert stop_move.episode.stop_plan == 2342.0

    breakeven = correlator.ingest(_signal("move SL to entry"))
    assert breakeven.episode.episode_id == episode_id
    assert breakeven.episode.current_intent["stop"] == "breakeven"
    assert breakeven.episode.revision == 3

    close_half = correlator.ingest(_signal("close half"))
    assert close_half.episode.episode_id == episode_id
    assert close_half.classification.close_fraction == 0.5
    assert close_half.episode.revision == 4
    assert close_half.episode.lifecycle_state is EpisodeLifecycleState.OPEN  # a partial close doesn't end the episode

    # every message this analyst posted is on the one episode's history
    assert len(close_half.episode.source_message_ids) == 4


def test_new_entry_after_close_all_opens_a_second_episode():
    correlator = EpisodeCorrelator()
    first = correlator.ingest(_signal("BUY GOLD 2350"))
    closed = correlator.ingest(_signal("close all"))
    assert closed.episode.episode_id == first.episode.episode_id
    assert closed.episode.lifecycle_state is EpisodeLifecycleState.CLOSED

    second = correlator.ingest(_signal("BUY GOLD 2400"))
    assert second.created_new is True
    assert second.episode.episode_id != first.episode.episode_id

    # a revision posted now can only ever mean the SECOND (open) episode --
    # the first is closed and must never be a correlation candidate again.
    stop = correlator.ingest(_signal("SL 2390"))
    assert stop.episode.episode_id == second.episode.episode_id


def test_cancel_entry_marks_episode_cancelled():
    correlator = EpisodeCorrelator()
    correlator.ingest(_signal("BUY GOLD 2350"))
    result = correlator.ingest(_signal("cancel"))
    assert result.episode.lifecycle_state is EpisodeLifecycleState.CANCELLED


# --- AMBIGUOUS: never auto-correlate --------------------------------------------


def test_ambiguous_when_analyst_has_two_open_episodes_and_message_names_no_symbol():
    correlator = EpisodeCorrelator()
    correlator.ingest(_signal("BUY GOLD 2350", symbol="GOLD"))
    correlator.ingest(_signal("BUY EURUSD 1.08", symbol="EURUSD"))

    result = correlator.ingest(_signal("SL 2342", symbol="GOLD"))  # signal.symbol is irrelevant -- the TEXT names nothing
    assert result.classification.event_type is RevisionEventType.AMBIGUOUS
    assert result.episode is None
    assert result.created_new is False
    assert len(result.candidate_episode_ids) == 2


def test_not_ambiguous_when_message_explicitly_names_the_symbol():
    correlator = EpisodeCorrelator()
    gold = correlator.ingest(_signal("BUY GOLD 2350", symbol="GOLD"))
    correlator.ingest(_signal("BUY EURUSD 1.08", symbol="EURUSD"))

    result = correlator.ingest(_signal("SL 2342 GOLD", symbol="GOLD"))
    assert result.episode is not None
    assert result.episode.episode_id == gold.episode.episode_id


def test_ambiguous_when_no_open_episode_to_revise():
    correlator = EpisodeCorrelator()
    result = correlator.ingest(_signal("close half"))
    assert result.episode is None
    assert result.classification.event_type is RevisionEventType.AMBIGUOUS
    assert result.candidate_episode_ids == []
    assert result.reason == "no open episode to revise"


def test_ambiguous_never_creates_or_mutates_an_episode():
    correlator = EpisodeCorrelator()
    correlator.ingest(_signal("BUY GOLD 2350", symbol="GOLD"))
    correlator.ingest(_signal("BUY EURUSD 1.08", symbol="EURUSD"))
    before = {ep.episode_id: ep.revision for ep in correlator.store.all_open()}

    correlator.ingest(_signal("SL 2342"))  # ambiguous: two open episodes, no symbol named

    after = {ep.episode_id: ep.revision for ep in correlator.store.all_open()}
    assert before == after  # neither candidate was touched
    assert len(correlator.store.all_open()) == 2  # and nothing new was created either


# --- identity-insufficient fallback ---------------------------------------------


def test_no_analyst_identity_always_creates_a_new_episode():
    """A source that can't identify the poster (signal.analyst is None) --
    e.g. a webhook payload with no 'analyst' field -- must never guess a
    correlation; every message becomes its own fresh episode."""
    correlator = EpisodeCorrelator()
    first = correlator.ingest(_signal("BUY GOLD 2350", analyst=None))
    second = correlator.ingest(_signal("SL 2342", analyst=None))  # would be CHANGE_STOP if correlation were attempted

    assert first.created_new is True
    assert second.created_new is True
    assert first.episode.episode_id != second.episode.episode_id
    # the second message's own classification still ran (it's just never
    # correlated) -- confirm it wasn't silently coerced into a NEW_ENTRY.
    assert second.classification.event_type is RevisionEventType.CHANGE_STOP


def test_different_analysts_on_same_provider_never_share_an_episode():
    correlator = EpisodeCorrelator()
    a = correlator.ingest(_signal("BUY GOLD 2350", analyst="alice"))
    b = correlator.ingest(_signal("BUY GOLD 2360", analyst="bob"))
    assert a.episode.episode_id != b.episode.episode_id

    # bob's stop revision must never land on alice's episode
    bob_stop = correlator.ingest(_signal("SL 2350", analyst="bob"))
    assert bob_stop.episode.episode_id == b.episode.episode_id


# --- persistence round-trip (app/db.py's SignalStore) ---------------------------


def test_episode_persists_and_reloads_through_signal_store(tmp_path):
    store = SignalStore(tmp_path / "episodes.db")
    correlator = EpisodeCorrelator(sqlite_store=store)
    result = correlator.ingest(_signal("BUY GOLD 2350"))
    correlator.ingest(_signal("SL 2342"))
    episode_id = result.episode.episode_id

    reloaded_row = store.get_episode(episode_id)
    assert reloaded_row is not None
    assert reloaded_row.instrument == "GOLD"
    assert reloaded_row.revision == 2
    assert reloaded_row.stop_plan == 2342.0

    # a fresh correlator against the same store resumes with the same open
    # episode as a correlation candidate -- not memory-only.
    resumed = EpisodeCorrelator(sqlite_store=store)
    assert len(resumed.store.open_episodes_for("telegram", "analyst_a")) == 1

    breakeven = resumed.ingest(_signal("move SL to entry"))
    assert breakeven.episode.episode_id == episode_id
    assert breakeven.episode.revision == 3


def test_closed_episode_is_not_reloaded_as_a_correlation_candidate(tmp_path):
    store = SignalStore(tmp_path / "episodes2.db")
    correlator = EpisodeCorrelator(sqlite_store=store)
    correlator.ingest(_signal("BUY GOLD 2350"))
    correlator.ingest(_signal("close all"))

    resumed = EpisodeCorrelator(sqlite_store=store)
    assert resumed.store.open_episodes_for("telegram", "analyst_a") == []
