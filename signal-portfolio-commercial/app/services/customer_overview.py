"""CU-01 "Customer overview" -- the real summary service backing
`/app`, the customer's own landing page. See
dashboard_spec/screens/CU-01.md for the full screen contract this
implements a bounded slice of.

Reuses CU-02's/CU-07's/CU-09's own already-real queries
(list_own_portfolio_selections/list_own_platform_connections/
list_own_copy_mandates) rather than a duplicate technique -- this
module only joins their outputs together into one row per selection.

"Subscription, connection and live copying badges are independent. No
connection means actual performance unavailable, not model return"
(CU-01's own acceptance text) holds by construction: `required_action`
is derived from the real state of each row's own mandate/connection,
never a fabricated aggregate. Actual performance card and Observed open
episodes have no backing execution/NAV model anywhere in this build
(the same gap PU-03/AD-05's own slices already documented) -- the
template renders those as explicit UNSUPPORTED, never a guessed or
zero-filled figure.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.copy_mandate import CopyMandate, CopyMandateState
from app.models.platform_connection import PlatformConnection, PlatformConnectionState
from app.models.portfolio_selection import PortfolioSelection, PortfolioSelectionState
from app.services.copy_mandate import list_own_copy_mandates
from app.services.platform_connection import list_own_platform_connections
from app.services.portfolio_selection import list_own_portfolio_selections
from app.services.product_admin import get_product


@dataclass(frozen=True)
class CustomerOverviewRow:
    selection: PortfolioSelection
    product_name: str | None
    product_slug: str | None
    mandate: CopyMandate | None
    connection: PlatformConnection | None

    @property
    def required_action(self) -> str:
        if self.mandate is None:
            return "Create a copy mandate"
        if self.connection is None:
            return "Reconnect platform -- the connected account for this mandate is missing"
        return "No action required"


@dataclass(frozen=True)
class CustomerOverview:
    rows: list[CustomerOverviewRow]

    @property
    def selected_portfolios_count(self) -> int:
        return len(self.rows)


def get_customer_overview(session: Session, *, tenant_id: str, user_id: str) -> CustomerOverview:
    selections = [
        s
        for s in list_own_portfolio_selections(session, tenant_id=tenant_id, user_id=user_id)
        if s.state == PortfolioSelectionState.ACTIVE
    ]
    mandates = list_own_copy_mandates_by_selection(session, tenant_id=tenant_id, user_id=user_id)
    connections_by_id = {
        c.connection_id: c
        for c in list_own_platform_connections(session, tenant_id=tenant_id, user_id=user_id)
        if c.state == PlatformConnectionState.DECLARED
    }

    rows: list[CustomerOverviewRow] = []
    for selection in selections:
        product = get_product(session, selection.product_id, tenant_id=tenant_id)
        mandate = mandates.get(selection.selection_id)
        connection = connections_by_id.get(mandate.connection_id) if mandate else None
        rows.append(
            CustomerOverviewRow(
                selection=selection,
                product_name=product.product_name if product else None,
                product_slug=product.slug if product else None,
                mandate=mandate,
                connection=connection,
            )
        )
    return CustomerOverview(rows=rows)


def list_own_copy_mandates_by_selection(
    session: Session, *, tenant_id: str, user_id: str
) -> dict[str, CopyMandate]:
    """The most recent non-CANCELLED mandate for each selection_id --
    reuses copy_mandate.py's own list_own_copy_mandates (already
    newest-first) rather than a duplicate query."""
    by_selection: dict[str, CopyMandate] = {}
    for mandate in list_own_copy_mandates(session, tenant_id=tenant_id, user_id=user_id):
        if mandate.state == CopyMandateState.CANCELLED:
            continue
        by_selection.setdefault(mandate.selection_id, mandate)
    return by_selection
