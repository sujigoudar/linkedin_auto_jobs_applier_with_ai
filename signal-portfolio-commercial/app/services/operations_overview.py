"""AD-01 "Commercial operations overview" -- the real cross-subsystem
summary backing `/ops`, the operator landing page. See
dashboard_spec/screens/AD-01.md for the full screen contract this
implements a bounded slice of.

Bounded scope: two of the spec's four business indicators are honestly
computable right now against existing models (Products/blockers from
`product_admin.py`, Subscriptions from `app/models/billing.py`) and are
built here for real. "Unknown publications" needs a tenant-scoped join
through `PortfolioVersion` since `PublicationIntent` itself carries no
`tenant_id` (see app/models/publication.py) -- also built here. "Open
incidents" has NO backing model at all yet (an incident-tracking screen
is separate, unbuilt, later work) -- this module deliberately returns no
count for it rather than fabricate a zero. Per AD-01's own state table:
"Zero only if query complete and count truly zero" -- a metric with no
real query behind it is `unsupported`, never a silent 0.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.billing import Subscription, SubscriptionState
from app.models.portfolio_version import PortfolioVersion
from app.models.product import Product
from app.models.publication import PublicationIntent, PublicationState
from app.services.product_admin import compute_publication_blockers, list_products


@dataclass(frozen=True)
class OperationsOverview:
    eligible_products: list[Product]
    #: (product, blockers) for every product that isn't yet eligible --
    #: never collapsed into a count alone, matching AD-01-P01's own
    #: "enumerates independently evaluated conditions... reason code."
    blocked_products: list[tuple[Product, list[str]]] = field(default_factory=list)
    active_subscriptions_count: int = 0
    unknown_publications_count: int = 0


def get_operations_overview(session: Session, *, tenant_id: str) -> OperationsOverview:
    products = list_products(session, tenant_id=tenant_id)
    eligible: list[Product] = []
    blocked: list[tuple[Product, list[str]]] = []
    for product in products:
        blockers = compute_publication_blockers(session, product)
        if blockers:
            blocked.append((product, blockers))
        else:
            eligible.append(product)

    active_subscriptions_count = (
        session.scalar(
            select(func.count())
            .select_from(Subscription)
            .where(Subscription.tenant_id == tenant_id, Subscription.state == SubscriptionState.ACTIVE_PAID)
        )
        or 0
    )

    #: PublicationIntent carries no tenant_id of its own -- join through
    #: PortfolioVersion (which does) to scope this count to the caller's
    #: own tenant, the same "no RLS on this table, so filter explicitly"
    #: pattern rights_registry.py and product_admin.py already use.
    #: Verified load-bearing: removing the explicit `tenant_id` clause
    #: here does NOT leak another tenant's rows, because the INNER JOIN
    #: to `portfolio_versions` (which IS RLS-protected) already excludes
    #: them -- so this WHERE clause is defense in depth on top of RLS,
    #: not the only thing standing between tenants. The `state ==
    #: UNKNOWN` clause, by contrast, is load-bearing on its own: removing
    #: it counts every state, not just UNKNOWN (confirmed by temporarily
    #: removing it and watching the corresponding test fail).
    unknown_publications_count = (
        session.scalar(
            select(func.count())
            .select_from(PublicationIntent)
            .join(PortfolioVersion, PortfolioVersion.portfolio_version_id == PublicationIntent.portfolio_version_id)
            .where(PortfolioVersion.tenant_id == tenant_id, PublicationIntent.state == PublicationState.UNKNOWN)
        )
        or 0
    )

    return OperationsOverview(
        eligible_products=eligible,
        blocked_products=blocked,
        active_subscriptions_count=active_subscriptions_count,
        unknown_publications_count=unknown_publications_count,
    )
