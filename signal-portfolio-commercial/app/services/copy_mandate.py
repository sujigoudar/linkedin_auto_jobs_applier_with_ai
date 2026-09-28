"""CU-09 "Copy setup and mandate wizard" -- the real draft/list service
backing F-MANDATE. See app/models/copy_mandate.py for the full screen
contract this implements a bounded slice of.

`create_copy_mandate_draft` implements F-MANDATE's own "Save: Persist
mandate draft without effect" literally: it never activates anything,
never touches a broker, and refuses a `selection_id`/`connection_id`
that isn't both real AND currently eligible (an ACTIVE portfolio
selection, a DECLARED platform connection) -- reusing CU-02's/CU-07's
own ownership-and-state checks rather than a duplicate technique.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.copy_mandate import CopyMandate, CopyMandateStartMode
from app.models.platform_connection import PlatformConnectionState
from app.models.portfolio_selection import PortfolioSelectionState
from app.services.platform_connection import get_own_platform_connection
from app.services.portfolio_selection import get_own_portfolio_selection


class InvalidCopyMandateError(Exception):
    pass


def list_own_copy_mandates(session: Session, *, tenant_id: str, user_id: str) -> list[CopyMandate]:
    return list(
        session.scalars(
            select(CopyMandate)
            .where(CopyMandate.tenant_id == tenant_id, CopyMandate.user_id == user_id)
            .order_by(CopyMandate.created_at.desc())
        ).all()
    )


def create_copy_mandate_draft(
    session: Session,
    *,
    tenant_id: str,
    user_id: str,
    selection_id: str,
    connection_id: str,
    allocation_amount: str,
    allocation_currency: str,
    max_trade_risk: str | None,
    max_loss: str | None,
    start_mode: str,
    policy_version_id: str,
    consent_version: str,
) -> CopyMandate:
    selection = get_own_portfolio_selection(session, selection_id, tenant_id=tenant_id, user_id=user_id)
    if selection is None or selection.state != PortfolioSelectionState.ACTIVE:
        raise InvalidCopyMandateError(
            f"SELECTION_NOT_OWNED_OR_NOT_ACTIVE: {selection_id!r} is not an active selection of this customer"
        )

    connection = get_own_platform_connection(session, connection_id, tenant_id=tenant_id, user_id=user_id)
    if connection is None or connection.state != PlatformConnectionState.DECLARED:
        raise InvalidCopyMandateError(
            f"CONNECTION_NOT_OWNED_OR_NOT_DECLARED: {connection_id!r} is not a declared connection of this customer"
        )

    try:
        allocation_amount_decimal = Decimal(allocation_amount)
    except InvalidOperation as exc:
        raise InvalidCopyMandateError(f"allocation_amount {allocation_amount!r} is not a valid decimal") from exc
    if allocation_amount_decimal <= 0:
        raise InvalidCopyMandateError("allocation_amount must be positive")

    max_trade_risk_decimal: Decimal | None = None
    if max_trade_risk:
        try:
            max_trade_risk_decimal = Decimal(max_trade_risk)
        except InvalidOperation as exc:
            raise InvalidCopyMandateError(f"max_trade_risk {max_trade_risk!r} is not a valid decimal") from exc

    max_loss_decimal: Decimal | None = None
    if max_loss:
        try:
            max_loss_decimal = Decimal(max_loss)
        except InvalidOperation as exc:
            raise InvalidCopyMandateError(f"max_loss {max_loss!r} is not a valid decimal") from exc

    try:
        start_mode_enum = CopyMandateStartMode(start_mode)
    except ValueError as exc:
        raise InvalidCopyMandateError(f"{start_mode!r} is not a known start_mode") from exc

    if not allocation_currency or not allocation_currency.strip():
        raise InvalidCopyMandateError("allocation_currency is required")
    if not policy_version_id or not policy_version_id.strip():
        raise InvalidCopyMandateError("policy_version_id is required")
    if not consent_version or not consent_version.strip():
        raise InvalidCopyMandateError("consent_version is required")

    mandate = CopyMandate(
        tenant_id=tenant_id,
        user_id=user_id,
        selection_id=selection_id,
        connection_id=connection_id,
        allocation_amount=allocation_amount_decimal,
        allocation_currency=allocation_currency,
        max_trade_risk=max_trade_risk_decimal,
        max_loss=max_loss_decimal,
        start_mode=start_mode_enum,
        policy_version_id=policy_version_id,
        consent_version=consent_version,
    )
    session.add(mandate)
    session.flush()
    return mandate
