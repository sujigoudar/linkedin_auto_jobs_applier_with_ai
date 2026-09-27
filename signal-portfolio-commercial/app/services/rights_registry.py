"""Rights-registry enforcement: the one place allowed to answer "is this
source_id's data permitted for THIS specific use, channel, jurisdiction
and asset, right now."

See spec/docs/01_rights_legal_and_launch_gates.md: "Check rights at
portfolio candidate admission, research export, product publication,
subscriber delivery and outbound financial publication." Every one of
those call sites (Phases 04/05/06/07, not yet built) must call
`check_rights` again immediately before its own action, not cache an
earlier result -- "a grant changed during a job must be rechecked before
external delivery... data downloaded earlier does not gain permanent
future commercial rights."

Fail-closed by construction: no grant at all, a grant whose `status`
isn't GRANTED, an expired/not-yet-effective grant, or a requested channel/
jurisdiction/asset not explicitly listed on a GRANTED grant are all
`RIGHTS_USE_NOT_GRANTED` -- an empty `channels`/`jurisdictions`/`assets`
list means NO channel/jurisdiction/asset is permitted yet, never "no
restriction." A paid subscription status is never itself a rights basis
(see this module's docstring reference above: "paid status does not
override").
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.rights import RightsGrant, RightsStatus, RightsUse

#: The named external providers the request itself named as having
#: UNKNOWN commercial rights (spec/docs/01, SRC06-SRC08) -- seeded here
#: so the registry starts honest (nothing is silently permitted) rather
#: than simply absent. An absent source_id and an UNKNOWN-status grant
#: both fail closed identically in `check_rights`; this seed exists so
#: these three are visible in the registry for owner review (CARD-2),
#: not because UNKNOWN behaves any differently from "not recorded."
NAMED_UNKNOWN_SOURCES: tuple[str, ...] = ("buyalerts", "tradealgo", "kamdenai")


@dataclass(frozen=True)
class RightsCheckResult:
    allowed: bool
    reason: str
    grant_id: str | None = None


_DENIED_NO_GRANT = RightsCheckResult(allowed=False, reason="RIGHTS_USE_NOT_GRANTED")


def check_rights(
    session: Session,
    *,
    source_id: str,
    use: RightsUse,
    channel: str,
    jurisdiction: str,
    asset: str,
    at: datetime | None = None,
) -> RightsCheckResult:
    now = at or datetime.now(timezone.utc)
    grants = session.scalars(
        select(RightsGrant).where(RightsGrant.source_id == source_id, RightsGrant.status == RightsStatus.GRANTED)
    ).all()

    for grant in grants:
        if use.value not in grant.uses:
            continue
        if grant.effective_at > now or grant.expires_at < now:
            continue
        if channel not in grant.channels:
            continue
        if jurisdiction not in grant.jurisdictions:
            continue
        if asset not in grant.assets:
            continue
        return RightsCheckResult(allowed=True, reason="GRANTED", grant_id=grant.grant_id)

    return _DENIED_NO_GRANT


def list_rights_grants(session: Session) -> list[RightsGrant]:
    """AD-02 "Rights and service approvals" -- the grant register panel's
    own query. `RightsGrant` carries no `tenant_id` (rights are recorded
    against a source/provider, not a tenant -- see this module's own
    docstring), so this is a plain platform-wide list, gated by role
    (owner/reviewer) rather than by row-level security. Correctly returns
    an empty list before any grant has ever been recorded -- "No
    commercial rights grants have been approved" (AD-02's own empty
    state) is a real, valid result, not a loading failure."""
    return list(session.scalars(select(RightsGrant).order_by(RightsGrant.effective_at.desc())).all())


def seed_unknown_source(session: Session, source_id: str, grantee_entity: str) -> RightsGrant:
    """Record a source as explicitly reviewed-and-UNKNOWN (not silently
    absent) -- idempotent by `source_id`+UNKNOWN (a second call for the
    same source_id is a no-op, it doesn't create a duplicate placeholder
    row every time this seed runs). Never creates a GRANTED row -- only an
    explicit, reviewed grant (added separately, by an owner action, once a
    real contract exists) can do that."""
    existing = session.scalars(
        select(RightsGrant).where(RightsGrant.source_id == source_id, RightsGrant.status == RightsStatus.UNKNOWN)
    ).first()
    if existing is not None:
        return existing

    now = datetime.now(timezone.utc)
    grant = RightsGrant(
        grant_id=f"unknown-{source_id}",
        source_id=source_id,
        grantee_entity=grantee_entity,
        contract_hash="",
        status=RightsStatus.UNKNOWN,
        uses=[],
        channels=[],
        jurisdictions=[],
        assets=[],
        effective_at=now,
        expires_at=now,
        attribution_policy_id="unset",
        wind_down_policy_id="unset",
        review_id="unset",
    )
    session.add(grant)
    session.flush()
    return grant
