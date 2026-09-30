"""ServiceHealthSample: Track 11 "Infrastructure uptime/latency metrics"
-- a real, recorded sample of one service's own `/health` endpoint at
one point in time, backing AD-22's "Workers and queues" panel's
computed uptime %/p50/p95 latency (app/services/service_health.py).

NOT tenant-scoped: this is the PLATFORM's own infrastructure (this
process and, cross-service, signal-copier's own process), not customer
data -- there is exactly one of each service per deployment, not one
per tenant, and nothing here carries a `tenant_id` for the same reason
`app/models/incident.py`'s own precedent (a bounded, non-customer,
operator-only record) already sets. Read access is still owner-gated
(the same `view_deployment_status` permission AD-22 already requires),
just not tenant-filtered.

Bounded cardinality by construction: `service_name` is one of a small,
fixed set this deployment knows about ("commercial", "signal_copier")
-- see app/services/service_health.py's own `_KNOWN_SERVICES` -- never
an arbitrary caller-supplied string that could grow this table's label
space unboundedly the way a per-account or per-symbol label would (the
same "bounded labels only" discipline signal-copier/app/metrics.py's
own docstring already documents).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ServiceHealthSample(Base):
    __tablename__ = "service_health_samples"

    sample_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    #: One of app/services/service_health.py's `_KNOWN_SERVICES` -- see
    #: this module's own docstring on bounded cardinality.
    service_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    #: True only when the target's own `/health` responded 200 with a
    #: body reporting `status == "ok"` -- a reachable-but-degraded
    #: response counts as a failure here, same as an unreachable one:
    #: this table answers "was the service actually healthy", not
    #: merely "did something answer the socket."
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    #: Wall-clock round trip of the health check itself, in
    #: milliseconds. None when the request could not complete at all
    #: (timeout/connection error) -- never a fabricated number standing
    #: in for "we don't actually know how long it took."
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Free-text reason for a failed sample (exception class/message,
    #: non-200 status, non-"ok" body) -- operator-only detail, never
    #: shown to an anonymous caller.
    failure_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, index=True)
