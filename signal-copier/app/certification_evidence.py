"""Track 17: computes every `app.certification.CheckKind.AUTOMATED`
check's real, current PASS/FAIL/NOT_RUN status FROM EXISTING DATA, at
READ time -- never stored, never a rubber-stamp. See
`app/certification.py`'s own module docstring for why each of these six
checks (CONNECTION, HISTORICAL_RETRIEVAL, PARSER, DUPLICATE_HANDLING,
CROSS_CHANNEL_CORRELATION, PAPER_EXECUTION) is classified AUTOMATED and
every other check is not.

Every function here takes a `SignalStore` (or a raw sqlite3 connection
via `store._connect()`, matching this codebase's existing convention of
reading through the store's own connection helper rather than opening a
second one) and returns an `AutomatedCheckResult` -- `status` is
`CheckStatus.NOT_RUN`, never a guessed PASS, whenever the underlying
data this check would need simply doesn't exist yet for this scope. The
`evidence` dict is always the REAL data point(s) this verdict was
computed from (a row count, a health_score, a real signal id) -- never a
bare boolean with no backing, matching `app.certification.
validate_check_record`'s own hard rule for the manual/attestation path.

None of these functions ever writes to `certification_checks` --
`app/db.py`'s `SignalStore.compute_certification_check` (the one place
that calls into this module) is a READ path; the persisted
`certification_checks` row for an AUTOMATED check name is only ever a
cache-free, freshly-recomputed view (see that method's own docstring for
why there is no `last_computed_status` column this could go stale
against).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.certification import CheckStatus

#: `main.py`'s own `brokers = {"paper": PaperBroker(), ...}` registration
#: key -- the one string `orders.broker` actually carries for an order
#: routed through the paper adapter (see that dict literal). Read here,
#: not re-derived, since `orders` has no adapter-class column to check
#: `isinstance(..., PaperBroker)` against directly -- a real, structural
#: limitation of the persisted schema, not something this module works
#: around by guessing.
PAPER_BROKER_KEY = "paper"


@dataclass
class AutomatedCheckResult:
    status: CheckStatus
    evidence: dict[str, Any] = field(default_factory=dict)
    detail: str = ""


def connection_check(store: Any, *, source_id: str) -> AutomatedCheckResult:
    """CONNECTION: real evidence from Track 14's own
    `connections.connection_state`/`health_score` for the `connections`
    row this `sources.id` points at (`sources.connection_id`). `NOT_RUN`
    when the source has no `connection_id` wired at all, or that
    connection has never reported a `health_score` (no real heartbeat/
    health signal has ever come back for it -- never treated as passing
    by default)."""
    source_row = store.get_source(source_id)
    if source_row is None:
        return AutomatedCheckResult(CheckStatus.NOT_RUN, detail=f"no source registered with id={source_id!r}")
    connection_id = source_row.get("connection_id")
    if not connection_id:
        return AutomatedCheckResult(
            CheckStatus.NOT_RUN, evidence={"source_id": source_id}, detail="source has no connection_id wired"
        )
    connection = store.get_connection(connection_id)
    if connection is None:
        return AutomatedCheckResult(
            CheckStatus.NOT_RUN,
            evidence={"connection_id": connection_id},
            detail="source's connection_id does not resolve to a real connections row",
        )
    state = connection.get("connection_state")
    health_score = connection.get("health_score")
    evidence = {"connection_id": connection_id, "connection_state": state, "health_score": health_score}
    if state == "connected" and health_score is not None and health_score > 0:
        return AutomatedCheckResult(CheckStatus.PASS, evidence=evidence)
    if state in ("error", "disconnected"):
        return AutomatedCheckResult(CheckStatus.FAIL, evidence=evidence)
    return AutomatedCheckResult(CheckStatus.NOT_RUN, evidence=evidence, detail="connection not yet confirmed healthy")


def historical_retrieval_check(store: Any, *, provider_id: str) -> AutomatedCheckResult:
    """HISTORICAL_RETRIEVAL: whether this provider has EVER had a signal
    with `signals.import_batch` set -- Track 5's own real, honest marker
    that a historical-backfill import (`POST /sources/{source}/
    import-signals`) actually ran for it, distinct from an ordinary live
    receipt. See `app.models.Signal.import_batch`'s own docstring."""
    with store._connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE source = ? AND import_batch IS NOT NULL", (provider_id,)
        ).fetchone()
    count = row[0] if row else 0
    evidence = {"provider_id": provider_id, "historical_import_signal_count": count}
    if count > 0:
        return AutomatedCheckResult(CheckStatus.PASS, evidence=evidence)
    return AutomatedCheckResult(
        CheckStatus.NOT_RUN, evidence=evidence, detail="no historical-import batch recorded for this provider yet"
    )


def parser_check(store: Any, *, provider_id: str) -> AutomatedCheckResult:
    """PARSER: Track 15 (parser accuracy tooling) was running in
    parallel to this track and had NOT landed on this branch as of this
    module's writing (see this repo's own Track 17 report for the
    verified merge-base check). This function probes for it rather than
    assuming: if `store` exposes a `get_parser_accuracy_metrics` method
    (the shape a landed Track 15 would plausibly add, following this
    codebase's own `getattr`-probe convention elsewhere for an
    optional/not-yet-landed capability), its real numbers are used;
    otherwise this is honestly reported `NOT_RUN` with `detail` saying
    so, NEVER a fabricated accuracy percentage. Once Track 15 lands for
    real, this is the one function to update -- see this module's own
    docstring and app/certification.py's PARSER entry."""
    probe = getattr(store, "get_parser_accuracy_metrics", None)
    if probe is None:
        return AutomatedCheckResult(
            CheckStatus.NOT_RUN,
            evidence={"provider_id": provider_id},
            detail=(
                "no parser-accuracy tooling (Track 15) is wired into this SignalStore yet -- this check requires "
                "either Track 15 landing (integrate here) or manual owner attestation via POST "
                "/provider-certification/checks/{check_id}/record"
            ),
        )
    metrics = probe(provider_id=provider_id)  # pragma: no cover -- exercised only once Track 15 lands
    if not metrics:
        return AutomatedCheckResult(CheckStatus.NOT_RUN, evidence={"provider_id": provider_id})
    accuracy = metrics.get("accuracy_pct")
    evidence = dict(metrics)
    if accuracy is not None and accuracy >= 0.95:
        return AutomatedCheckResult(CheckStatus.PASS, evidence=evidence)
    if accuracy is not None:
        return AutomatedCheckResult(CheckStatus.FAIL, evidence=evidence)
    return AutomatedCheckResult(CheckStatus.NOT_RUN, evidence=evidence)


def _correlation_evidence_rows(store: Any, *, provider_id: str) -> list[tuple]:
    with store._connect() as conn:
        return conn.execute(
            "SELECT canonical_signal_id, evidence_signal_id, channel_id, match_type "
            "FROM signal_correlation_evidence WHERE source = ?",
            (provider_id,),
        ).fetchall()


def duplicate_handling_check(store: Any, *, provider_id: str) -> AutomatedCheckResult:
    """DUPLICATE_HANDLING: this provider has been through Track 12's
    real correlation/dedup path at least once (ANY recorded
    `signal_correlation_evidence` row, corroborating or conflicting --
    both prove the dedup/correlation machinery actually ran against this
    provider's real signals, which is what this check is verifying)."""
    rows = _correlation_evidence_rows(store, provider_id=provider_id)
    evidence = {"provider_id": provider_id, "correlation_evidence_row_count": len(rows)}
    if rows:
        return AutomatedCheckResult(CheckStatus.PASS, evidence=evidence)
    return AutomatedCheckResult(
        CheckStatus.NOT_RUN, evidence=evidence, detail="no signal_correlation_evidence recorded for this provider yet"
    )


def cross_channel_correlation_check(store: Any, *, provider_id: str) -> AutomatedCheckResult:
    """CROSS_CHANNEL_CORRELATION: same underlying table as
    DUPLICATE_HANDLING, but requires at least one row whose own
    `channel_id` differs from ANOTHER row sharing the same
    `canonical_signal_id` -- i.e. a genuine cross-transport match (two
    different channels corroborating/conflicting on the same underlying
    trade), not merely within-channel evidence. Mirrors
    `app/signal_correlation.py`'s own "candidates are further restricted
    to a DIFFERENT channel_id" scoping."""
    rows = _correlation_evidence_rows(store, provider_id=provider_id)
    by_canonical: dict[str, set[str]] = {}
    for canonical_signal_id, _evidence_signal_id, channel_id, _match_type in rows:
        by_canonical.setdefault(canonical_signal_id, set()).add(channel_id or "")
    cross_channel_groups = {k: v for k, v in by_canonical.items() if len(v) > 1}
    evidence = {
        "provider_id": provider_id,
        "correlation_evidence_row_count": len(rows),
        "cross_channel_canonical_signal_count": len(cross_channel_groups),
    }
    if cross_channel_groups:
        return AutomatedCheckResult(CheckStatus.PASS, evidence=evidence)
    return AutomatedCheckResult(
        CheckStatus.NOT_RUN,
        evidence=evidence,
        detail="no cross-channel (differing channel_id) correlation evidence recorded for this provider yet",
    )


def paper_execution_check(store: Any, *, provider_id: str, account_route: str) -> AutomatedCheckResult:
    """PAPER_EXECUTION: real `orders` rows exist for this
    (provider, account_route) whose `broker` is `main.py`'s own
    `"paper"` registration key and whose `status='filled'` -- a genuine
    completed paper fill, not merely a PENDING/REJECTED attempt. Joins
    through `signals.source` (== `provider_id`, this codebase's own
    existing provider-identity convention -- see
    `app.provider_catalog`'s module docstring) since `orders` itself
    carries no provider identity column of its own."""
    with store._connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM orders o JOIN signals s ON o.signal_id = s.id "
            "WHERE s.source = ? AND o.account_id = ? AND o.broker = ? AND o.status = 'filled'",
            (provider_id, account_route, PAPER_BROKER_KEY),
        ).fetchone()
    count = row[0] if row else 0
    evidence = {
        "provider_id": provider_id,
        "account_route": account_route,
        "paper_broker_key": PAPER_BROKER_KEY,
        "filled_paper_order_count": count,
    }
    if count > 0:
        return AutomatedCheckResult(CheckStatus.PASS, evidence=evidence)
    return AutomatedCheckResult(
        CheckStatus.NOT_RUN, evidence=evidence, detail="no filled paper-broker order recorded for this provider/route yet"
    )
