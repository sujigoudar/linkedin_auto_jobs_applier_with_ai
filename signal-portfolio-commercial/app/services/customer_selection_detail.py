"""CU-03 "Selected portfolio detail" -- the real detail service backing
`/app/portfolios/{selection_id}`. See
dashboard_spec/screens/CU-03.md for the full screen contract this
implements a bounded slice of.

Reuses CU-02's own get_own_portfolio_selection (scoped not-found),
PU-03's own get_published_portfolio_detail (identity/version facts),
and CU-01's own list_own_copy_mandates_by_selection (the selection's
most recent non-cancelled mandate) rather than a duplicate technique.

`performance_state` (same states/reasoning as
app/services/customer_overview.py's own CustomerOverviewRow) replaces
Observed net P&L's old blanket UNSUPPORTED. Open episodes, Delivery
lag, and the Actual versus model tabs / Alerts/trades panels still
have no backing model at all in this build (the same gap CU-01/PU-03/
AD-05's own slices already documented) -- the template still renders
those as explicit UNSUPPORTED, never a guessed or zero-filled figure.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.copy_mandate import CopyMandate
from app.models.platform_connection import PlatformConnection, PlatformConnectionState
from app.models.portfolio_selection import PortfolioSelection
from app.services.customer_overview import list_own_copy_mandates_by_selection
from app.services.customer_performance_state import PerformanceState, get_customer_performance_state
from app.services.platform_connection import list_own_platform_connections
from app.services.portfolio_selection import get_own_portfolio_selection
from app.services.product_admin import get_product
from app.services.public_site import PortfolioDetail, get_published_portfolio_detail


@dataclass(frozen=True)
class CustomerSelectionDetail:
    selection: PortfolioSelection
    portfolio: PortfolioDetail | None
    mandate: CopyMandate | None
    connection: PlatformConnection | None
    performance_state: PerformanceState


def get_own_selection_detail(
    session: Session, selection_id: str, *, tenant_id: str, user_id: str
) -> CustomerSelectionDetail | None:
    """Returns None for an unknown, cross-tenant, or different-customer
    selection_id -- callers must turn a None here into a 404, never a
    403 (docs/12's "scoped not-found")."""
    selection = get_own_portfolio_selection(session, selection_id, tenant_id=tenant_id, user_id=user_id)
    if selection is None:
        return None

    product = get_product(session, selection.product_id, tenant_id=tenant_id)
    portfolio = get_published_portfolio_detail(session, product.slug) if product is not None else None

    mandate = list_own_copy_mandates_by_selection(session, tenant_id=tenant_id, user_id=user_id).get(
        selection.selection_id
    )
    connection: PlatformConnection | None = None
    if mandate is not None:
        connections_by_id = {
            c.connection_id: c
            for c in list_own_platform_connections(session, tenant_id=tenant_id, user_id=user_id)
            if c.state == PlatformConnectionState.DECLARED
        }
        connection = connections_by_id.get(mandate.connection_id)

    performance_state = get_customer_performance_state(session, tenant_id=tenant_id, connection=connection)
    return CustomerSelectionDetail(
        selection=selection, portfolio=portfolio, mandate=mandate, connection=connection,
        performance_state=performance_state,
    )
