"""S12 step 6 "Real FOLLOWER observation connector" -- the boundary
that actually populates `Book.FOLLOWER` from a customer's own
DECLARED `PlatformConnection` (Collective2, eToro, MetaApi CopyFactory).

## Scope this module actually covers, and why it stops where it does

This module is the SAME "build the real, honest half; refuse to
fabricate the rest" split `app/services/collective2_publisher.py`
already establishes for the outbound (publish) direction -- applied
here to the inbound (observe) direction:

- **`ObservedFill`** is a normalized, vendor-agnostic shape this app
  defines for itself (instrument/side/quantity/price/fee/executed_at/
  `external_observation_id`) -- it has no dependency on any vendor's
  own wire format, so nothing about it needs vendor documentation to
  build or test honestly.
- **`ingest_observed_fill`** writes a REAL `Book.FOLLOWER` ledger entry
  from one `ObservedFill`, idempotent by `external_observation_id` (a
  re-poll or redelivery of the SAME real fill is a harmless no-op,
  never a duplicate ledger row) -- fully real, fully tested, no vendor
  dependency either.
- **What this module deliberately does NOT include**: a function that
  actually calls Collective2's or eToro's own reporting API and parses
  ITS raw response into an `ObservedFill`. This session attempted to
  fetch Collective2's own current API4 documentation (the same
  `https://collective2.com/apidoc/v4` `app/services/
  collective2_publisher.py`'s own docstring already names) and eToro's
  public API documentation, to confirm each platform's real trade-
  history/position-report response shape before writing a parser
  against it -- both requests were refused by this environment's own
  egress policy (403, not a transient failure), the identical blocked
  condition that module's own docstring already documents for
  collective2.com. Per this build's own standing rule that missing
  vendor-schema evidence keeps a gate BLOCKED rather than guessed, no
  Collective2/eToro-specific response parser exists here -- writing one
  from memory or invention would be exactly the "fabricated financial
  data" this build's own standing rules forbid. A future slice with
  real API access (or captured vendor documentation) adds a
  `parse_collective2_trade_history(raw_json) -> list[ObservedFill]`
  (and its eToro equivalent) that produces `ObservedFill` instances for
  `ingest_observed_fill` to consume -- this module's own real half
  needs no change to receive them.

`PlatformConnection` itself is, by its own model docstring, "honestly
DECLARED (never VERIFIED)... no live connectivity is ever attempted" --
this module does not change that; it only defines what a REAL
observation would look like once one legitimately arrives, from
whatever channel eventually supplies it (a future vendor-specific
poller, a manually-reconciled import, or a webhook), and prepares real
processing for it, exactly as `PlatformConnection`'s own docstring
already anticipates.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import Book, EvidenceClass, LedgerEntry, Side
from app.models.platform_connection import PlatformConnection
from app.services.ledger import append_entry


@dataclass(frozen=True)
class ObservedFill:
    """A normalized, vendor-agnostic real fill, however it was
    obtained. `external_observation_id` MUST be the observation
    source's own stable identifier for this exact fill (never
    synthesized here) -- it is the idempotency key `ingest_observed_fill`
    dedups on."""

    instrument: str
    side: Side
    quantity: Decimal
    price: Decimal
    currency: str
    executed_at: datetime
    external_observation_id: str
    fee: Decimal | None = None
    multiplier: Decimal = Decimal(1)


def _already_ingested(session: Session, *, tenant_id: str, connection_id: str, external_observation_id: str) -> LedgerEntry | None:
    return session.scalars(
        select(LedgerEntry).where(
            LedgerEntry.tenant_id == tenant_id,
            LedgerEntry.book == Book.FOLLOWER,
            LedgerEntry.follower_connection_id == connection_id,
            LedgerEntry.external_observation_id == external_observation_id,
        )
    ).first()


def ingest_observed_fill(
    session: Session,
    *,
    tenant_id: str,
    connection: PlatformConnection,
    fill: ObservedFill,
    evidence_class: EvidenceClass,
    source_authority: str,
) -> LedgerEntry:
    """Writes a real `Book.FOLLOWER` entry for `fill`, scoped to
    `connection.connection_id` (the ONLY identity
    `app/services/customer_performance_state.py`'s own query checks --
    see its own docstring). Idempotent by `(tenant_id, connection_id,
    external_observation_id)`: re-ingesting the identical observation
    (a re-poll, a redelivered webhook) returns the EXISTING row rather
    than appending a duplicate, the same "same identity, harmless
    re-detect" contract `app/services/integration_inbox.py`'s own
    `event_id` dedup gives the private-relay boundary. Never checks
    `connection.state` -- a DISCONNECTED connection's own already-
    recorded history remains real history; only a caller deciding
    whether to poll a connection at all should look at its state, not
    this function."""
    existing = _already_ingested(
        session, tenant_id=tenant_id, connection_id=connection.connection_id,
        external_observation_id=fill.external_observation_id,
    )
    if existing is not None:
        return existing

    return append_entry(
        session,
        tenant_id=tenant_id,
        book=Book.FOLLOWER,
        instrument=fill.instrument,
        side=fill.side,
        quantity=fill.quantity,
        price=fill.price,
        currency=fill.currency,
        multiplier=fill.multiplier,
        fee=fill.fee,
        event_time=fill.executed_at,
        source_authority=source_authority,
        evidence_class=evidence_class,
        follower_connection_id=connection.connection_id,
        external_observation_id=fill.external_observation_id,
    )
