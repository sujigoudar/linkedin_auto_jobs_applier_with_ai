"""AD-03 "Research universe and sleeves" -- the real query/command
service backing the admin sleeve-catalog screen. See
dashboard_spec/screens/AD-03.md for the full screen contract this
implements a bounded slice of.

Bounded scope: `Sleeve` (app/models/sleeve.py, Phase 04) already models
exactly the lineage fields F-SLEEVE's own form asks for (provider,
analyst, strategy horizon, parser version, execution/cost/capacity/risk
policy references, history origin) -- reused as-is rather than adding a
second, differently-named field set. Not built here: a distinct
DRAFT/QUALIFIED lifecycle state, or the "Confirm: Save qualified status
only from evidence-backed evaluation" step -- that needs real coverage/
overlap statistics against actual historical data this environment
doesn't have, so a sleeve created here is a real, persisted lineage
record, not yet a "qualified" one; inventing a qualification step ahead
of real evidence would be exactly the kind of fabricated-evaluation
result this build has consistently refused to produce.

Reads are always tenant-scoped through the caller's own already-set RLS
session (app/db.py's set_tenant_scope) -- this module never accepts or
trusts a caller-supplied tenant_id for authorization, only for
recording which tenant a new sleeve belongs to (and even then, the
caller is responsible for having already scoped the session to that
same tenant_id, so a cross-tenant write is stopped by RLS regardless of
what this function is told).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.sleeve import Sleeve


class InvalidSleeveDraftError(Exception):
    pass


_REQUIRED_FIELDS = (
    "provider",
    "analyst",
    "strategy_horizon",
    "asset_class",
    "parser_version",
    "execution_policy_id",
    "cost_model_id",
    "capacity_policy_id",
    "risk_unit_id",
    "history_origin",
)


def list_sleeves(session: Session, *, tenant_id: str) -> list[Sleeve]:
    """This tenant's own sleeve lineage catalog -- correctly returns an
    empty list before any sleeve has ever been created, per AD-03's own
    empty state: "No qualified strategy sleeves are available."

    RLS already restricts the underlying rows to this session's tenant
    (`sleeves` is in app/db.py's `_TENANT_SCOPED_TABLES`); filtering by
    `tenant_id` here explicitly is defense in depth, matching
    `product_admin.py`'s own established pattern."""
    return list(
        session.scalars(
            select(Sleeve).where(Sleeve.tenant_id == tenant_id).order_by(Sleeve.created_at.desc())
        ).all()
    )


def get_sleeve(session: Session, sleeve_id: str, *, tenant_id: str) -> Sleeve | None:
    """Returns None both when the ID never existed AND when it belongs to
    another tenant -- callers must turn a None here into a 404, never a
    403, so a cross-tenant URL guess reveals nothing (the same "scoped
    not-found" pattern as `product_admin.get_product`)."""
    sleeve = session.get(Sleeve, sleeve_id)
    if sleeve is None or sleeve.tenant_id != tenant_id:
        return None
    return sleeve


def create_sleeve(session: Session, *, tenant_id: str, **fields: str) -> Sleeve:
    """Create a new sleeve lineage record. Every F-SLEEVE-required field
    (provider/analyst/strategy/parser/policy/product/risk/history) must
    be a non-blank string -- "Not arbitrary marketing label", "No latest
    alias", "No mixed financial math" (AD-03's own field help text) all
    read as: these are exact, reviewed identifiers, never left empty or
    defaulted."""
    missing = [key for key in _REQUIRED_FIELDS if not fields.get(key, "").strip()]
    if missing:
        raise InvalidSleeveDraftError(f"missing required field(s): {', '.join(missing)}")

    sleeve = Sleeve(tenant_id=tenant_id, **{key: fields[key].strip() for key in _REQUIRED_FIELDS})
    session.add(sleeve)
    session.flush()
    return sleeve
