"""Track 11 -- uptime/latency computation from real recorded
ServiceHealthSample rows, including the "insufficient sample history"
case, computed over a fixture set of samples (never fabricated)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.service_health import (
    InvalidHealthSampleError,
    InvalidServiceNameError,
    compute_all_services_uptime_latency,
    compute_uptime_latency,
    record_health_sample,
)

_NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)


def _record(session, *, success, latency_ms, minutes_ago, service_name="commercial"):
    record_health_sample(
        session,
        service_name=service_name,
        success=success,
        latency_ms=latency_ms,
        sampled_at=_NOW - timedelta(minutes=minutes_ago),
    )


def test_unknown_service_name_rejected(db_session):
    with pytest.raises(InvalidServiceNameError):
        record_health_sample(db_session, service_name="not_a_real_service", success=True, latency_ms=10)


def test_no_samples_is_insufficient_history(db_session):
    result = compute_uptime_latency(db_session, service_name="commercial", now=_NOW)
    assert result.sample_count == 0
    assert result.insufficient_history is True
    assert result.uptime_pct is None
    assert result.p50_latency_ms is None
    assert result.p95_latency_ms is None


def test_fewer_than_minimum_samples_is_insufficient_history(db_session):
    for i in range(4):  # below the 5-sample floor
        _record(db_session, success=True, latency_ms=50, minutes_ago=i)
    db_session.commit()

    result = compute_uptime_latency(db_session, service_name="commercial", now=_NOW)
    assert result.sample_count == 4
    assert result.insufficient_history is True
    assert result.uptime_pct is None


def test_uptime_and_percentiles_computed_from_real_samples(db_session):
    # 8 successes, 2 failures -> 80% uptime.
    latencies = [10, 20, 30, 40, 50, 60, 70, 80]
    for i, latency in enumerate(latencies):
        _record(db_session, success=True, latency_ms=latency, minutes_ago=i)
    _record(db_session, success=False, latency_ms=None, minutes_ago=8)
    _record(db_session, success=False, latency_ms=None, minutes_ago=9)
    db_session.commit()

    result = compute_uptime_latency(db_session, service_name="commercial", now=_NOW)
    assert result.sample_count == 10
    assert result.insufficient_history is False
    assert result.uptime_pct == 80.0
    # Failed samples never contribute a latency figure.
    assert result.p50_latency_ms in latencies
    assert result.p95_latency_ms in latencies
    # minutes_ago=0 (the success at the head of `latencies`) is the MOST
    # recent sample -- the two failures (minutes_ago=8,9) are older.
    assert result.last_sample_success is True


def test_samples_outside_the_window_are_excluded(db_session):
    for i in range(5):
        _record(db_session, success=True, latency_ms=10, minutes_ago=i)
    # Five OLD samples, well outside a 1-hour window.
    for i in range(5):
        _record(db_session, success=False, latency_ms=None, minutes_ago=120 + i)
    db_session.commit()

    result = compute_uptime_latency(db_session, service_name="commercial", window_hours=1.0, now=_NOW)
    assert result.sample_count == 5
    assert result.uptime_pct == 100.0


def test_services_are_kept_separate(db_session):
    for i in range(6):
        _record(db_session, success=True, latency_ms=10, minutes_ago=i, service_name="commercial")
    for i in range(6):
        _record(db_session, success=False, latency_ms=None, minutes_ago=i, service_name="signal_copier")
    db_session.commit()

    results = {r.service_name: r for r in compute_all_services_uptime_latency(db_session, now=_NOW)}
    assert results["commercial"].uptime_pct == 100.0
    assert results["signal_copier"].uptime_pct == 0.0


def test_success_sample_cannot_carry_a_failure_reason(db_session):
    with pytest.raises(InvalidHealthSampleError):
        record_health_sample(
            db_session, service_name="commercial", success=True, latency_ms=10, failure_reason="should not happen"
        )
