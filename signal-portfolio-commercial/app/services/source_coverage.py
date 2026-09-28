"""INTEGRATION_ACCEPTANCE_CASES.json INT-027 "All permitted source
outcomes reach research": the coverage report that case itself asks for
and S12 step 5's own "Portfolio Lab source feed" slice never built -- a
real cross-reference of every ingested `SOURCE_RECEIPT` event's own
CURRENT disposition and lineage, computed straight from `InboxEvent`/
`LedgerEntry`, never fabricated or estimated.

Honest scope boundary, stated here because it is the single thing this
report cannot claim: this build's own data model tracks exactly three
dispositions --

- `LEDGER_RECORDED`: a real `Book.SOURCE` ledger entry exists for this
  receipt (`InboxEvent.ledger_entry_id` is set).
- `PARKED`: received but not yet applied at all (`InboxEvent.
  applied_at` is still NULL) -- either a NAMED `parked_reason` (an
  unsupported schema version, a generation mismatch) or an out-of-order
  gap wait, which carries no `parked_reason` of its own but is exactly
  as un-applied; see app/services/integration_inbox.py's own
  `_next_expected_sequence` docstring.
- `RECEIVED_NO_LEDGER_ENTRY`: applied, but no ledger row exists. This
  bucket is NOT further split into "the receipt genuinely had no known
  quantity/price" versus "it had both but was swept as covered-by-an-
  activated-bootstrap-snapshot" (app/services/integration_inbox.py's own
  `_apply_projection` docstring) -- `InboxEvent` does not persist enough
  of the original payload's own quantity/price to distinguish those two
  cases after the fact, only the original `envelope_json` (which this
  report does not re-parse, to keep this a light aggregate query rather
  than a payload deserialization pass over every row).

INT-027's own explicitly requested taxonomy (admitted/rejected/unfilled/
canceled/loss/commentary, reflecting signal-copier's own ROUTING
decision) does NOT exist anywhere in this system's data model yet --
signal-copier's own export path (`signal-copier/app/export_events.py`)
exports a `SOURCE_RECEIPT` unconditionally, for every non-CLOSE signal
regardless of its own routing outcome, but never encodes WHICH outcome
that was onto the receipt itself. Widening the payload/receipt shape to
carry that is real future work this report does not attempt; this
report is honest about covering only the disposition axis this build
already has real data for.

"Lineage" here means each row's own `source_stream`/`export_sequence`
identity (enough to trace it back to its own exact envelope in
`InboxEvent.envelope_json`) -- never a synthesized/guessed identity.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session
from signal_platform_contracts import EventType

from app.models.integration_inbox import InboxEvent

DISPOSITION_LEDGER_RECORDED = "ledger_recorded"
DISPOSITION_PARKED = "parked"
DISPOSITION_RECEIVED_NO_LEDGER_ENTRY = "received_no_ledger_entry"


@dataclass(frozen=True)
class SourceCoverageRow:
    event_id: str
    source_stream: str
    export_sequence: int
    disposition: str
    parked_reason: str | None
    ledger_entry_id: str | None


@dataclass
class SourceCoverageReport:
    rows: list[SourceCoverageRow] = field(default_factory=list)
    ledger_recorded_count: int = 0
    parked_count: int = 0
    received_no_ledger_entry_count: int = 0

    @property
    def total_count(self) -> int:
        return len(self.rows)


def compute_source_coverage(session: Session, *, tenant_id: str) -> SourceCoverageReport:
    """Every `SOURCE_RECEIPT` `InboxEvent` this tenant has ever received,
    each labeled with its own CURRENT disposition (see this module's own
    docstring for exactly what that does and doesn't distinguish) --
    ordered by (source_stream, export_sequence) so a reader can walk one
    stream's own history in the order it actually happened."""
    events = session.scalars(
        select(InboxEvent)
        .where(InboxEvent.tenant_id == tenant_id, InboxEvent.event_type == EventType.SOURCE_RECEIPT.value)
        .order_by(InboxEvent.source_stream, InboxEvent.export_sequence)
    ).all()

    report = SourceCoverageReport()
    for event in events:
        if event.applied_at is None:
            # Not yet applied at all -- either a NAMED parked_reason
            # (an unsupported schema version, a generation mismatch) or,
            # just as real, a gap wait: an out-of-order receipt sitting
            # behind a still-missing lower export_sequence never gets a
            # parked_reason of its own (app/services/integration_inbox.py's
            # own `_next_expected_sequence` docstring), but is exactly as
            # un-applied. Both are this report's own PARKED bucket.
            disposition = DISPOSITION_PARKED
            report.parked_count += 1
        elif event.ledger_entry_id is not None:
            disposition = DISPOSITION_LEDGER_RECORDED
            report.ledger_recorded_count += 1
        else:
            disposition = DISPOSITION_RECEIVED_NO_LEDGER_ENTRY
            report.received_no_ledger_entry_count += 1
        report.rows.append(
            SourceCoverageRow(
                event_id=event.event_id,
                source_stream=event.source_stream,
                export_sequence=event.export_sequence,
                disposition=disposition,
                parked_reason=event.parked_reason,
                ledger_entry_id=event.ledger_entry_id,
            )
        )
    return report
