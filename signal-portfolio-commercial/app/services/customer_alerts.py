"""CU-04 "Alerts and delivery history" / CU-05 "Alert, trade and
order-family detail" -- the real, tenant-and-customer-scoped read
backing `/app/alerts` and `/app/activity/{episode_id}`. See
dashboard_spec/screens/CU-04.md and CU-05.md for the full screen
contracts this implements a bounded slice of.

## What "entitled" means here, and why

`PublicationIntent` carries no `tenant_id` or customer identity of its
own (see app/models/publication.py) -- the same gap
app/services/publication_admin.py's own AD-10 slice already
documented and scoped through `PortfolioVersion` (which IS
tenant-scoped and RLS-protected). This module extends that same join
one hop further, through `Product.portfolio_version_id`, to the
customer's own ACTIVE `PortfolioSelection` rows -- "Read entitled
alerts in correct lifecycle sequence" (CU-04's own purpose) means
exactly the publication intents for the portfolio version(s) backing a
product this specific customer has actively selected, never every
intent the tenant has ever published. An intent whose portfolio
version does not trace back to one of this customer's own ACTIVE
selections is treated exactly like an unknown id for CU-05 -- a scoped
not-found, never a leak of another customer's (or another tenant's)
alert.

## What is deliberately NOT computed here

CU-04-P04 "Delivery receipts" and CU-05's "Execution and fees" /
"Protection coverage" panels all need a delivery-attempt/external-
acknowledgment tracking model and a ledger-to-episode link that do not
exist anywhere in this build (the same gap AD-10's own docstring
already documented for recipient cohort/delivery attempts, and
cu03_selection_detail.html's own "no execution ledger or
alert-delivery-to-customer link exists" note for the Alerts/trades
panel). `LedgerEntry` (app/models/ledger.py) has no `episode_id`
column at all. Each is rendered as an explicit UNSUPPORTED note by the
template, never a fabricated timestamp, quantity or fee.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.portfolio_selection import PortfolioSelection, PortfolioSelectionState
from app.models.portfolio_version import PortfolioVersion
from app.models.product import Product
from app.models.publication import PublicationIntent


def _own_entitled_intents_query(*, tenant_id: str, user_id: str):
    return (
        select(PublicationIntent)
        .join(PortfolioVersion, PortfolioVersion.portfolio_version_id == PublicationIntent.portfolio_version_id)
        .join(Product, Product.portfolio_version_id == PortfolioVersion.portfolio_version_id)
        .join(PortfolioSelection, PortfolioSelection.product_id == Product.product_id)
        .where(
            PortfolioVersion.tenant_id == tenant_id,
            Product.tenant_id == tenant_id,
            PortfolioSelection.tenant_id == tenant_id,
            PortfolioSelection.user_id == user_id,
            PortfolioSelection.state == PortfolioSelectionState.ACTIVE,
        )
        .distinct()
    )


def list_own_alerts(session: Session, *, tenant_id: str, user_id: str) -> list[PublicationIntent]:
    """CU-04's own timeline -- every publication intent entitled to this
    customer's own ACTIVE portfolio selections. Newest event descending
    then immutable ID descending (CU-04's own table-behavior contract)."""
    query = _own_entitled_intents_query(tenant_id=tenant_id, user_id=user_id).order_by(
        PublicationIntent.created_at.desc(), PublicationIntent.intent_id.desc()
    )
    return list(session.scalars(query).all())


@dataclass(frozen=True)
class AlertEpisodeDetail:
    episode_id: str
    revisions: list[PublicationIntent]


def get_own_alert_episode(session: Session, episode_id: str, *, tenant_id: str, user_id: str) -> AlertEpisodeDetail | None:
    """CU-05's own detail -- every revision of one episode this customer
    is entitled to see, ordered newest-first (`revision` descending,
    ties broken by immutable id descending). Returns None for an
    unknown, cross-tenant, or not-entitled episode_id -- callers must
    turn that into a 404, never a 403 (docs/12's own "scoped
    not-found")."""
    query = _own_entitled_intents_query(tenant_id=tenant_id, user_id=user_id).where(
        PublicationIntent.episode_id == episode_id
    ).order_by(PublicationIntent.revision.desc(), PublicationIntent.intent_id.desc())
    revisions = list(session.scalars(query).all())
    if not revisions:
        return None
    return AlertEpisodeDetail(episode_id=episode_id, revisions=revisions)
