"""Track 11: private aggregate operational metrics (prometheus_client),
exposed at GET /metrics behind owner/publisher_operator auth -- never a
public endpoint. Mirrors signal-copier/app/metrics.py's own conventions
verbatim (see that module's docstring for the reasoning this one
reuses):

- Bounded labels only -- `service_name` is one of
  app.services.service_health.KNOWN_SERVICES (two values), never an
  arbitrary or growing label.
- These describe ACTUAL recorded samples/rows, not "the process is
  running" -- a service with zero recent health samples reports as
  absent/stale here, never a fabricated "up."
- A monitoring/metrics outage must never grant any authority or mutate
  anything -- this module only ever reads existing database state.
"""
from __future__ import annotations

from datetime import datetime, timezone

from prometheus_client import CollectorRegistry, Gauge, generate_latest
from sqlalchemy.orm import Session

from app.services.operating_cost import list_operating_costs
from app.services.service_health import KNOWN_SERVICES, compute_uptime_latency


def _age_seconds(last_at: datetime | None, *, now: datetime | None = None) -> float | None:
    if last_at is None:
        return None
    now = now or datetime.now(timezone.utc)
    return (now - last_at).total_seconds()


def render_metrics(session: Session, *, tenant_id: str | None = None) -> bytes:
    """`tenant_id`: when set, `commercial_operating_cost_rows` is scoped
    to that one tenant (the normal owner-session case, where RLS already
    restricts the session to it). When None (e.g. a superuser/test
    session with no tenant context set), that gauge is simply not
    emitted rather than silently summing across tenants -- multi-tenant
    aggregate counts are exactly the kind of cross-tenant leak this
    endpoint must never produce."""
    registry = CollectorRegistry()
    now = datetime.now(timezone.utc)

    for service_name in KNOWN_SERVICES:
        result = compute_uptime_latency(session, service_name=service_name, window_hours=24.0, now=now)

        age = _age_seconds(result.last_sample_at, now=now)
        if age is not None:
            Gauge(
                "commercial_service_health_sample_age_seconds",
                "Seconds since the last recorded health sample for this service. "
                "Absent if no sample has ever been recorded.",
                labelnames=["service_name"],
                registry=registry,
            ).labels(service_name=service_name).set(age)

        if not result.insufficient_history:
            assert result.uptime_pct is not None  # guaranteed once history is sufficient
            Gauge(
                "commercial_service_uptime_ratio_24h",
                "Fraction (0..1) of recorded health samples over the trailing 24h window that "
                "succeeded. Absent when fewer than the minimum sample count exists for that window "
                "-- never a fabricated ratio from too little history.",
                labelnames=["service_name"],
                registry=registry,
            ).labels(service_name=service_name).set(result.uptime_pct / 100.0)

            if result.p50_latency_ms is not None:
                Gauge(
                    "commercial_service_latency_p50_ms_24h",
                    "p50 health-check latency (ms) over the trailing 24h window, successful "
                    "samples only. Absent when insufficient history exists.",
                    labelnames=["service_name"],
                    registry=registry,
                ).labels(service_name=service_name).set(result.p50_latency_ms)
            if result.p95_latency_ms is not None:
                Gauge(
                    "commercial_service_latency_p95_ms_24h",
                    "p95 health-check latency (ms) over the trailing 24h window, successful "
                    "samples only. Absent when insufficient history exists.",
                    labelnames=["service_name"],
                    registry=registry,
                ).labels(service_name=service_name).set(result.p95_latency_ms)

    if tenant_id is not None:
        cost_rows = Gauge(
            "commercial_operating_cost_rows",
            "Recorded OperatingCost rows for the current tenant -- describes real entered cost "
            "data, not a live billing-API total (this build has no such integration).",
            registry=registry,
        )
        cost_rows.set(len(list_operating_costs(session, tenant_id=tenant_id)))

    return generate_latest(registry)
