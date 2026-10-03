"""CU-02 "My portfolios" -- the real create/list/cancel service backing
this screen. See dashboard_spec/screens/CU-02.md for the full screen
contract this implements a bounded slice of.

"Manage selections and versions without creating unintended orders" is
implemented literally: `create_portfolio_selection` refuses any
product_id that is not both real AND genuinely PUBLISHED
(`PRODUCT_NOT_PUBLISHED_OR_NOT_FOUND` -- a customer can never select a
DRAFT/VALIDATED/APPROVED product by guessing its id). G-C-10 fix: allows
selecting PUBLISHED products from any tenant (e.g., operator's products
selected by self-signup customers). A selection carries no quantity,
broker, or execution field at all -- there is nothing here that could
route to a broker or place an order.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.product import Product, ProductLifecycleState
from app.models.portfolio_selection import PortfolioSelection, PortfolioSelectionState


class InvalidPortfolioSelectionError(Exception):
    pass


def list_own_portfolio_selections(session: Session, *, tenant_id: str, user_id: str) -> list[PortfolioSelection]:
    return list(
        session.scalars(
            select(PortfolioSelection)
            .where(PortfolioSelection.tenant_id == tenant_id, PortfolioSelection.user_id == user_id)
            .order_by(PortfolioSelection.created_at.desc())
        ).all()
    )


def get_own_portfolio_selection(
    session: Session, selection_id: str, *, tenant_id: str, user_id: str
) -> PortfolioSelection | None:
    """Returns None both when the id never existed AND when it belongs
    to another tenant or another user under the same tenant -- a scoped
    not-found, never a 403 that would leak existence."""
    selection = session.get(PortfolioSelection, selection_id)
    if selection is None or selection.tenant_id != tenant_id or selection.user_id != user_id:
        return None
    return selection


def create_portfolio_selection(
    session: Session, *, tenant_id: str, user_id: str, product_id: str
) -> PortfolioSelection:
    # G-C-10: allow selecting any PUBLISHED product, even if owned by another
    # tenant (e.g. operator's products selected by self-signup customers).
    # get_product() scopes to the caller's tenant, so fetch it directly with
    # a PUBLISHED state check instead.
    product = session.get(Product, product_id)
    if product is None or product.lifecycle_state != ProductLifecycleState.PUBLISHED:
        raise InvalidPortfolioSelectionError(
            f"PRODUCT_NOT_PUBLISHED_OR_NOT_FOUND: {product_id!r} is not a published product"
        )

    existing = session.scalars(
        select(PortfolioSelection).where(
            PortfolioSelection.tenant_id == tenant_id,
            PortfolioSelection.user_id == user_id,
            PortfolioSelection.product_id == product_id,
            PortfolioSelection.state == PortfolioSelectionState.ACTIVE,
        )
    ).first()
    if existing is not None:
        raise InvalidPortfolioSelectionError(f"ALREADY_SELECTED: {product_id!r} is already actively selected")

    selection = PortfolioSelection(tenant_id=tenant_id, user_id=user_id, product_id=product_id)
    session.add(selection)
    session.flush()
    return selection


def cancel_portfolio_selection(session: Session, selection: PortfolioSelection) -> PortfolioSelection:
    selection.state = PortfolioSelectionState.CANCELLED
    selection.updated_at = datetime.now(timezone.utc)
    session.flush()
    return selection
