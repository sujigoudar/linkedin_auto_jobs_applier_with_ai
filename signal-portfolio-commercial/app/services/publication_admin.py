"""AD-10 "Publication intent and cohort detail" -- the real,
tenant-scoped read backing this screen. See
dashboard_spec/screens/AD-10.md for the full screen contract this
implements a bounded slice of.

`PublicationIntent` carries no `tenant_id` of its own (see
app/models/publication.py) -- scoped the same way
operations_overview.py's own `unknown_publications_count` already does:
an inner join through `PortfolioVersion` (which IS tenant-scoped and
RLS-protected), reused here rather than a second, parallel technique.
An intent whose `portfolio_version_id` doesn't join to a
`PortfolioVersion` this tenant owns is treated exactly like an unknown
id -- a scoped not-found, never a leak of another tenant's intent.

Recipient cohort, delivery attempts and external order-family state are
all deliberately NOT computed here -- `Subscription` (app/models/
billing.py) has no field linking a row to any specific product or
portfolio version at all (the same gap AD-13's own slice already
documented as "no per-price Subscription linkage"), and no delivery-
attempt/external-acknowledgment tracking model exists anywhere in this
build. Each is rendered as an explicit UNSUPPORTED note by the
template, never a fabricated zero or count.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.portfolio_version import PortfolioVersion
from app.models.publication import PublicationIntent


@dataclass(frozen=True)
class PublicationIntentDetail:
    intent: PublicationIntent
    portfolio_version: PortfolioVersion


def get_publication_intent_detail(
    session: Session, intent_id: str, *, tenant_id: str
) -> PublicationIntentDetail | None:
    row = session.execute(
        select(PublicationIntent, PortfolioVersion)
        .join(PortfolioVersion, PortfolioVersion.portfolio_version_id == PublicationIntent.portfolio_version_id)
        .where(PublicationIntent.intent_id == intent_id, PortfolioVersion.tenant_id == tenant_id)
    ).first()
    if row is None:
        return None
    intent, portfolio_version = row
    return PublicationIntentDetail(intent=intent, portfolio_version=portfolio_version)
