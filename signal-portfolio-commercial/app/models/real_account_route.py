"""INTEGRATION_ACCEPTANCE_CASES.json INT-033 "Shared account cannot gain
a second writer": a direct adapter (e.g. `ManagedProgram`'s own
`broker_program_id`) and an external platform alias (e.g.
`PublisherDestination`'s own `external_strategy_id`) can resolve to the
SAME real brokerage account -- `app/models/publisher_writer_claim.py`'s
own durable-claim pattern already prevents two writers on the same
`(channel, external_strategy_id)`, but that key is scoped to ONE effect
route's own alias, never to the real account underneath it. A PAMM route
and a Collective2 publisher route naming two DIFFERENT
`(channel, external_strategy_id)` pairs are, today, free to both write to
the identical real account -- "Alias treated as independent
capital/authority", the exact prohibited outcome here.

`RealAccountRoute` extends the same durable-claim pattern one level up:
keyed by `(broker, account_reference)` -- the canonical identity of the
real account itself, declared explicitly by an owner (never derived from
`PlatformConnection.masked_account_label`, which is documented there as a
display label only, never an authoritative readback identity) -- naming
which single `(channel, external_strategy_id)` effect route currently owns
write authority over it.

Deliberately NOT tenant-scoped (matching `PublisherWriterClaim`'s own
precedent, and `app/db.py`'s own `_TENANT_SCOPED_TABLES` list, which does
not include `publisher_writer_claims` either): a real brokerage account is
one real-world entity, and this table's own job is to prevent two DIFFERENT
routes -- however they identify themselves, even across tenants -- from
both claiming write authority over it. `tenant_id` is still recorded on the
current holder, for audit/display, but is never part of the uniqueness key.

`ExclusiveOwnershipPlan` is the ONLY path by which a second, different
route may ever take over an already-claimed real account (INT-033's own
"Conflict is rejected until an explicit exclusive ownership plan is
qualified") -- a real, explicit row an owner creates naming exactly which
successor route is approved, never inferred or auto-granted by
`register_real_account_route` itself.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RealAccountRoute(Base):
    __tablename__ = "real_account_routes"

    broker: Mapped[str] = mapped_column(String, primary_key=True)
    account_reference: Mapped[str] = mapped_column(String, primary_key=True)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    external_strategy_id: Mapped[str] = mapped_column(String, nullable=False)
    writer_identity: Mapped[str] = mapped_column(String, nullable=False)
    #: The tenant that currently owns this route -- audit/display only,
    #: never part of this table's own uniqueness key (see this module's
    #: own docstring for why).
    tenant_id: Mapped[str] = mapped_column(String, nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class ExclusiveOwnershipPlan(Base):
    __tablename__ = "exclusive_ownership_plans"

    broker: Mapped[str] = mapped_column(String, primary_key=True)
    account_reference: Mapped[str] = mapped_column(String, primary_key=True)
    #: The ONE successor route this plan approves to take over the real
    #: account named by (broker, account_reference) -- any OTHER route is
    #: still rejected, even while this plan exists.
    approved_channel: Mapped[str] = mapped_column(String, nullable=False)
    approved_external_strategy_id: Mapped[str] = mapped_column(String, nullable=False)
    qualified_by: Mapped[str] = mapped_column(String, nullable=False)
    qualified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
