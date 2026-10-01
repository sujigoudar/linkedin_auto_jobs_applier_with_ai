"""Track 41 (Gap 2): the one sanctioned way to record a
`SourceStopTargetRevision` row -- append-only, same discipline as
`app/services/ledger.py::append_entry` (a new row every time, never an
UPDATE of an earlier revision for the same position). See
`app/models/source_stop_target_revision.py`'s own module docstring and
ADR-0011 for the full design.
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.source_stop_target_revision import SourceStopTargetRevision, StopTargetRevisionKind


def append_stop_target_revision(
    session: Session,
    *,
    tenant_id: str,
    kind: StopTargetRevisionKind,
    source_event_native_key: str,
    provider_timestamp: datetime,
    source_authority: str,
    instrument: str | None = None,
    stop_loss: Decimal | None = None,
    take_profit: Decimal | None = None,
    targets: list[dict] | None = None,
    resolved_source_entry_id: str | None = None,
) -> SourceStopTargetRevision:
    """Always inserts a brand-new row -- there is no "correction" concept
    here (unlike `ledger.append_correction`): every real revision this
    build receives is its own distinct historical fact, so there is
    nothing to supersede, only to append. `targets` is the SAME
    `SourceReceiptPayload.targets` shape (a list of plain, JSON-
    serializable dicts, e.g. from `ProfitTargetPayload.model_dump(mode=
    "json")`) -- serialized verbatim, `[]` when `None`/empty."""
    revision = SourceStopTargetRevision(
        tenant_id=tenant_id,
        kind=kind,
        source_event_native_key=source_event_native_key,
        instrument=instrument,
        stop_loss=stop_loss,
        take_profit=take_profit,
        targets_json=json.dumps(targets or []),
        resolved_source_entry_id=resolved_source_entry_id,
        provider_timestamp=provider_timestamp,
        source_authority=source_authority,
    )
    session.add(revision)
    session.flush()
    return revision


def list_stop_target_revisions(
    session: Session, *, tenant_id: str, resolved_source_entry_id: str | None = None,
) -> list[SourceStopTargetRevision]:
    """Every revision this tenant has ever received, oldest first --
    optionally narrowed to the revisions that resolved against ONE
    specific `Book.SOURCE` `LedgerEntry` (`resolved_source_entry_id`),
    when a caller wants exactly one position's own stop/target history
    rather than every revision this tenant has ever received. Each
    revision has its OWN distinct `source_event_native_key` (it is its
    own real provider message), so that column is never the right way
    to group "every revision for this position" -- `resolved_source_
    entry_id` is."""
    filters = [SourceStopTargetRevision.tenant_id == tenant_id]
    if resolved_source_entry_id is not None:
        filters.append(SourceStopTargetRevision.resolved_source_entry_id == resolved_source_entry_id)
    return list(
        session.scalars(
            select(SourceStopTargetRevision).where(*filters).order_by(SourceStopTargetRevision.provider_timestamp)
        ).all()
    )
