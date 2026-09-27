"""Portfolio-level rights intersection -- CP-002 "Every component grant
must qualify": spec/catalog/requirements.json's own boundary contract is
"No commercial portfolio publication; A-only is a separately reviewed
version, not a silent change." A portfolio version is only rights-
eligible for a given use/channel/jurisdiction/asset/time when EVERY one
of its member sleeves independently qualifies -- one ungranted sleeve
blocks the whole version; it is never silently dropped to "publish the
rest" without that being a distinct, separately reviewed version (a
different `PortfolioVersion` row, which this module has no authority to
create).

`Sleeve.provider` is the identifier `RightsGrant.source_id` is checked
against -- the same string names "who this data ultimately comes from"
in both models; there is no separate mapping table because introducing
one here, before any real sleeve/grant data exists, would be inventing
structure this build has no evidence it needs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.portfolio_version import PortfolioVersionSleeve
from app.models.rights import RightsUse
from app.models.sleeve import Sleeve
from app.services.rights_registry import check_rights


@dataclass(frozen=True)
class PortfolioRightsResult:
    allowed: bool
    reason: str
    #: The specific sleeve_id that failed, when `allowed` is False and a
    #: single sleeve was the cause -- None when there are no member
    #: sleeves at all, or when allowed is True.
    failing_sleeve_id: str | None = None


_EMPTY_PORTFOLIO = PortfolioRightsResult(allowed=False, reason="PORTFOLIO_HAS_NO_SLEEVES")


def check_portfolio_rights(
    session: Session,
    *,
    portfolio_version_id: str,
    use: RightsUse,
    channel: str,
    jurisdiction: str,
    asset: str,
    at: datetime | None = None,
) -> PortfolioRightsResult:
    """Fail closed: a portfolio version with no member sleeves is never
    "vacuously eligible," and the first sleeve whose own `check_rights`
    call is denied blocks the entire result -- there is no partial-
    portfolio fallback here."""
    memberships = session.scalars(
        select(PortfolioVersionSleeve).where(
            PortfolioVersionSleeve.portfolio_version_id == portfolio_version_id
        )
    ).all()
    if not memberships:
        return _EMPTY_PORTFOLIO

    for membership in memberships:
        sleeve = session.get(Sleeve, membership.sleeve_id)
        if sleeve is None:
            return PortfolioRightsResult(
                allowed=False, reason="UNKNOWN_SLEEVE", failing_sleeve_id=membership.sleeve_id
            )

        sleeve_result = check_rights(
            session,
            source_id=sleeve.provider,
            use=use,
            channel=channel,
            jurisdiction=jurisdiction,
            asset=asset,
            at=at,
        )
        if not sleeve_result.allowed:
            return PortfolioRightsResult(
                allowed=False, reason=sleeve_result.reason, failing_sleeve_id=sleeve.sleeve_id
            )

    return PortfolioRightsResult(allowed=True, reason="ALL_SLEEVES_GRANTED")
