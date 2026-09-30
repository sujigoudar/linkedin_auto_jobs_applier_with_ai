"""Real uptime/latency tracking -- Track 11. Samples are recorded by
`record_health_sample` (called from the background sampler wired in
app/main.py, or directly by a test/fixture); `compute_uptime_latency`
turns a real set of recorded samples into AD-22's "Workers and queues"
uptime %/p50/p95 figures, and is the ONLY thing that panel calls --
never a fabricated percentage.

Bounded, honest window semantics, matching signal-copier/app/metrics.py's
own "describes actual work done" philosophy: with fewer than
`_MIN_SAMPLES_FOR_UPTIME` real samples in the requested window, this
reports `insufficient_history=True` and leaves uptime/latency as None,
rather than computing a "100% uptime" from e.g. one lucky sample.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.service_health_sample import ServiceHealthSample

#: The only two services this deployment knows how to sample -- see
#: app/models/service_health_sample.py's own docstring on bounded
#: cardinality. "commercial" is this process's own `/health`; "signal_copier"
#: is the separate signal-copier process's own `/health`, reachable only
#: when `config.SIGNAL_COPIER_BASE_URL` is configured.
KNOWN_SERVICES: tuple[str, ...] = ("commercial", "signal_copier")

#: Below this many real samples in the requested window, uptime/latency
#: are reported as genuinely unavailable rather than computed from too
#: thin a sample to mean anything (e.g. a single success looking like
#: "100% uptime").
_MIN_SAMPLES_FOR_UPTIME = 5


class InvalidServiceNameError(Exception):
    pass


class InvalidHealthSampleError(Exception):
    pass


def record_health_sample(
    session: Session,
    *,
    service_name: str,
    success: bool,
    latency_ms: float | None,
    failure_reason: str | None = None,
    sampled_at: datetime | None = None,
) -> ServiceHealthSample:
    if service_name not in KNOWN_SERVICES:
        raise InvalidServiceNameError(
            f"{service_name!r} is not a known service -- see service_health.KNOWN_SERVICES"
        )
    if success and failure_reason:
        raise InvalidHealthSampleError("a successful sample must not carry a failure_reason")
    row = ServiceHealthSample(
        service_name=service_name,
        success=success,
        latency_ms=latency_ms,
        failure_reason=failure_reason,
        sampled_at=sampled_at or datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    return row


@dataclass(frozen=True)
class UptimeLatencyResult:
    service_name: str
    window_hours: float
    sample_count: int
    insufficient_history: bool
    uptime_pct: float | None
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    last_sample_at: datetime | None
    last_sample_success: bool | None


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile over already-sorted values -- no
    interpolation-library dependency needed for a bounded, small sample
    set."""
    if not sorted_values:
        raise ValueError("cannot compute a percentile of an empty sequence")
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = max(0, min(len(sorted_values) - 1, round(pct * (len(sorted_values) - 1))))
    return sorted_values[rank]


def compute_uptime_latency(
    session: Session, *, service_name: str, window_hours: float = 24.0, now: datetime | None = None
) -> UptimeLatencyResult:
    if service_name not in KNOWN_SERVICES:
        raise InvalidServiceNameError(
            f"{service_name!r} is not a known service -- see service_health.KNOWN_SERVICES"
        )
    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(hours=window_hours)

    samples = list(
        session.scalars(
            select(ServiceHealthSample)
            .where(
                ServiceHealthSample.service_name == service_name,
                ServiceHealthSample.sampled_at >= window_start,
                ServiceHealthSample.sampled_at <= now,
            )
            .order_by(ServiceHealthSample.sampled_at.asc())
        ).all()
    )
    sample_count = len(samples)
    last_sample = samples[-1] if samples else None

    if sample_count < _MIN_SAMPLES_FOR_UPTIME:
        return UptimeLatencyResult(
            service_name=service_name,
            window_hours=window_hours,
            sample_count=sample_count,
            insufficient_history=True,
            uptime_pct=None,
            p50_latency_ms=None,
            p95_latency_ms=None,
            last_sample_at=last_sample.sampled_at if last_sample else None,
            last_sample_success=last_sample.success if last_sample else None,
        )

    success_count = sum(1 for s in samples if s.success)
    uptime_pct = round(100.0 * success_count / sample_count, 2)

    #: Only successful samples carry a real latency figure worth
    #: aggregating -- a failed/timed-out check's None latency must never
    #: be treated as 0ms (that would drag the percentile down and hide
    #: exactly the slow-or-broken periods this metric exists to surface).
    latencies = sorted(s.latency_ms for s in samples if s.success and s.latency_ms is not None)
    p50 = _percentile(latencies, 0.50) if latencies else None
    p95 = _percentile(latencies, 0.95) if latencies else None

    return UptimeLatencyResult(
        service_name=service_name,
        window_hours=window_hours,
        sample_count=sample_count,
        insufficient_history=False,
        uptime_pct=uptime_pct,
        p50_latency_ms=p50,
        p95_latency_ms=p95,
        last_sample_at=last_sample.sampled_at if last_sample else None,
        last_sample_success=last_sample.success if last_sample else None,
    )


def compute_all_services_uptime_latency(
    session: Session, *, window_hours: float = 24.0, now: datetime | None = None
) -> list[UptimeLatencyResult]:
    return [
        compute_uptime_latency(session, service_name=name, window_hours=window_hours, now=now)
        for name in KNOWN_SERVICES
    ]
