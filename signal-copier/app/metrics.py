"""C23/E10: private aggregate operational metrics (prometheus_client),
exposed at GET /metrics behind owner auth -- never a public endpoint.

Deliberately narrow, per the adoption plan's own acceptance obligations:

- Bounded labels only (broker name, account id) -- never a broker order
  id, a signal id, or any secret. Nothing here can grow an unbounded
  cardinality series from a growing number of distinct symbols/orders.
- These describe ACTUAL work done, not "the loop iterated" -- the same
  distinction OPS-01's health endpoint already draws (see app/main.py):
  a reconciliation pass that ran but corrected nothing still needs its
  own age tracked, but a feed that's silently stopped updating must show
  up as stale, not as a healthy zero.
- A monitoring/metrics outage must never grant trading authority or
  cause a duplicate writer -- this module only ever reads existing
  in-memory/store state, never mutates anything.
"""
from __future__ import annotations

from datetime import datetime, timezone

from prometheus_client import CollectorRegistry, Gauge, generate_latest

from app.db import SignalStore
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import ProtectionStatus
from app.pricing import PriceMonitor
from app.reconciliation import OrderReconciler


def _age_seconds(last_success_at: datetime | None) -> float | None:
    if last_success_at is None:
        return None
    return (datetime.now(timezone.utc) - last_success_at).total_seconds()


def render_metrics(
    *,
    store: SignalStore,
    price_monitor: PriceMonitor,
    reconciler: OrderReconciler,
    lifecycle_manager: PositionLifecycleManager | None,
) -> bytes:
    registry = CollectorRegistry()

    # prometheus_client's Gauge reports a real sample (default 0.0) the
    # instant it's constructed, whether or not .set() is ever called --
    # for these two "age since last success" gauges, a phantom 0.0 would
    # read as "just succeeded" when the true state is "never has." Only
    # construct (and so only ever emit) these when there's a genuine
    # measurement to report.
    price_age = _age_seconds(price_monitor.last_success_at)
    if price_age is not None:
        Gauge(
            "signal_copier_price_observation_age_seconds",
            "Seconds since PriceMonitor's last pass with at least one usable price read. "
            "Absent (no sample) if no successful pass has ever completed.",
            registry=registry,
        ).set(price_age)

    reconciler_cycle_age = _age_seconds(reconciler.last_success_at)
    if reconciler_cycle_age is not None:
        Gauge(
            "signal_copier_reconciler_cycle_age_seconds",
            "Seconds since OrderReconciler's last completed pass. Absent if no pass has ever completed.",
            registry=registry,
        ).set(reconciler_cycle_age)

    pending_entries = Gauge(
        "signal_copier_pending_entries",
        "Managed-lifecycle entries with an unresolved broker outcome.",
        registry=registry,
    )
    pending_exits = Gauge(
        "signal_copier_pending_exits",
        "Managed-lifecycle exits with an unresolved broker outcome.",
        registry=registry,
    )
    protection_deficit = Gauge(
        "signal_copier_protection_deficit_positions",
        "Open managed-lifecycle positions with owned quantity > 0 whose protective stop is "
        "not confirmed standing (unprotected or a resize/placement still in flight).",
        registry=registry,
    )
    open_positions = Gauge(
        "signal_copier_open_positions",
        "Non-flat tracked positions across all accounts (this service's own record, not a "
        "live broker read).",
        registry=registry,
    )

    if lifecycle_manager is not None:
        pending_entries.set(len(lifecycle_manager.list_pending_entries()))
        pending_exits.set(len(lifecycle_manager.list_pending_exits()))
        deficit = sum(
            1
            for lifecycle in lifecycle_manager.list_open_lifecycles()
            if lifecycle.confirmed_owned_quantity > 0
            and lifecycle.stop.status != ProtectionStatus.STOP_CONFIRMED
        )
        protection_deficit.set(deficit)

    open_positions.set(len(store.list_open_positions()))

    return generate_latest(registry)
