"""Track 37: wires Track 13's phone-escalation gate
(`app/phone_escalation.py`'s `covered_by_direct_source` point 1) to
Track 12's real cross-transport correlation layer
(`app/signal_correlation.py`, `SignalStore.find_correlation_candidates`)
instead of the `covered_by_direct_source=False` stopgap
`app/main.py`'s `_evaluate_phone_escalation_for_event` used to pass
unconditionally.

Covers `app.main._resolve_direct_source_coverage`'s real tri-state
computation directly (`True`/`False`/`None` -- see its own docstring)
and the end-to-end wiring through `_evaluate_phone_escalation_for_event`.
"""
from datetime import datetime, timedelta, timezone

import pytest

import app.main as main_module
from app.db import SignalStore
from app.models import AssetClass, Side, Signal
from app.notification_bridge import ContentCompleteness


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", s)
    monkeypatch.setattr(main_module.engine, "store", s)
    return s


def _direct_source_signal(**overrides) -> Signal:
    defaults = dict(
        source="telegram_provider",
        symbol="BTCUSDT",
        side=Side.BUY,
        asset_class=AssetClass.CRYPTO,
        price=65000.0,
        received_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        channel_id="telegram:chan-1",
        message_id="msg-1",
    )
    defaults.update(overrides)
    return Signal(**defaults)


def _escalation_signal(**overrides) -> Signal:
    defaults = dict(
        source="telegram_provider",
        symbol="BTCUSDT",
        side=Side.BUY,
        asset_class=AssetClass.CRYPTO,
        price=65010.0,  # within 0.5% default tolerance of 65000.0
        received_at=datetime(2026, 1, 1, 12, 2, 0, tzinfo=timezone.utc),
        channel_id="phone-1:com.whop.whop",
        message_id="notif-key-1",
    )
    defaults.update(overrides)
    return Signal(**defaults)


# ---------------------------------------------------------------------------
# _resolve_direct_source_coverage -- the real tri-state computation
# ---------------------------------------------------------------------------


def test_coverage_resolves_true_when_a_real_corroborating_candidate_exists(store):
    direct = _direct_source_signal()
    store.save_signal(direct)

    escalation_signal = _escalation_signal()
    coverage, direct_source_name, reason = main_module._resolve_direct_source_coverage(escalation_signal)

    assert coverage is True
    assert direct_source_name == "telegram_provider"
    assert "corroborating" in reason


def test_coverage_resolves_false_when_no_correlation_candidate_exists(store):
    # Nothing else in the store at all -- a genuine, checked "no".
    escalation_signal = _escalation_signal()
    coverage, direct_source_name, reason = main_module._resolve_direct_source_coverage(escalation_signal)

    assert coverage is False
    assert direct_source_name is None
    assert "none corroborating" in reason


def test_coverage_resolves_false_when_candidate_exists_but_outside_price_tolerance(store):
    # A same-fingerprint candidate exists, but price disagrees materially
    # (CONFLICTING, not CORROBORATING) -- still not treated as "covered".
    direct = _direct_source_signal(price=50000.0)
    store.save_signal(direct)

    escalation_signal = _escalation_signal(price=65010.0)
    coverage, direct_source_name, reason = main_module._resolve_direct_source_coverage(escalation_signal)

    assert coverage is False
    assert direct_source_name is None


def test_coverage_is_not_computable_when_there_is_no_parsed_signal_at_all(store):
    # The genuine structural gap: a bare-pointer notification with
    # nothing parseable has no Signal to fingerprint from at all.
    coverage, direct_source_name, reason = main_module._resolve_direct_source_coverage(None)

    assert coverage is None
    assert direct_source_name is None
    assert "no parsed_signal" in reason


def test_coverage_is_not_computable_when_parsed_signal_has_no_channel_id(store):
    orphan = _escalation_signal(channel_id=None, message_id=None)
    coverage, direct_source_name, reason = main_module._resolve_direct_source_coverage(orphan)

    assert coverage is None
    assert direct_source_name is None
    assert "channel_id" in reason


def test_coverage_true_requires_a_different_channel_than_the_escalation_signal_own(store):
    # A same-channel row must never count as cross-transport coverage --
    # that's exact-identity dedup's job (find_signal_id_by_provider_
    # identity), not this layer's. find_correlation_candidates already
    # excludes it, so this proves that exclusion actually takes effect
    # end to end through this call site.
    same_channel = _direct_source_signal(channel_id="phone-1:com.whop.whop", message_id="other-notif-key")
    store.save_signal(same_channel)

    escalation_signal = _escalation_signal()
    coverage, _, _ = main_module._resolve_direct_source_coverage(escalation_signal)

    assert coverage is False


# ---------------------------------------------------------------------------
# End-to-end: _evaluate_phone_escalation_for_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_phone_escalation_skips_retrieval_when_coverage_resolves_true(store):
    direct = _direct_source_signal()
    store.save_signal(direct)
    escalation_signal = _escalation_signal()

    result = await main_module._evaluate_phone_escalation_for_event(
        device_id="phone-1",
        app_package="com.whop.whop",
        notification_key="notif-key-1",
        content_hash="hash-1",
        completeness=ContentCompleteness.TRUNCATED,
        parsed_signal=escalation_signal,
    )

    assert result is not None
    assert result["disposition"] == "covered_by_direct_source"
    assert result["direct_source_coverage"] == "covered"
    assert result["direct_source_name"] == "telegram_provider"


@pytest.mark.asyncio
async def test_evaluate_phone_escalation_proceeds_when_coverage_resolves_false(store):
    # No config row registered at all -- capability is DISABLED by
    # default, so this still ends in CAPABILITY_DISABLED, but it must
    # get PAST the covered-by-direct-source gate first and report an
    # honest, checked "not_covered", not "not_computable".
    escalation_signal = _escalation_signal()

    result = await main_module._evaluate_phone_escalation_for_event(
        device_id="phone-1",
        app_package="com.whop.whop",
        notification_key="notif-key-1",
        content_hash="hash-1",
        completeness=ContentCompleteness.TRUNCATED,
        parsed_signal=escalation_signal,
    )

    assert result is not None
    assert result["disposition"] == "capability_disabled"
    assert result["direct_source_coverage"] == "not_covered"
    assert result["direct_source_name"] is None


@pytest.mark.asyncio
async def test_evaluate_phone_escalation_reports_not_computable_without_a_parsed_signal(store):
    # The genuinely-missing-data case (e.g. a bare "New trade posted"
    # pointer with nothing parseable at all) -- never silently reported
    # as a checked "not_covered".
    result = await main_module._evaluate_phone_escalation_for_event(
        device_id="phone-1",
        app_package="com.whop.whop",
        notification_key="notif-key-2",
        content_hash="hash-2",
        completeness=ContentCompleteness.POINTER_ONLY,
        parsed_signal=None,
    )

    assert result is not None
    assert result["direct_source_coverage"] == "not_computable"
    assert result["direct_source_name"] is None
    # Still falls back to the existing safe default for the actual gate
    # decision (proceeds past COVERED_BY_DIRECT_SOURCE, same behavior as
    # the old hardcoded covered_by_direct_source=False) -- never skips
    # evaluation outright just because coverage is unknown.
    assert result["disposition"] != "covered_by_direct_source"
