"""AD-13 "Pricing, entitlements and billing operations" -- the real
save/list service backing F-PRICE. See dashboard_spec/screens/AD-13.md
for the full screen contract this implements a bounded slice of.

Only "test" mode is ever accepted -- "live requires merchant/owner
price approval; No auto paid launch" (AD-13's own field help text), and
this build has no merchant-approval workflow or real Stripe live-mode
connection at all (see app/services/public_site.py's own billing-status
check). `mode="live"` is therefore always a real, named
LIVE_MODE_NOT_AUTHORIZED refusal, never silently downgraded to test or
silently accepted.

`features` is restricted to `_REVIEWED_FEATURE_REGISTRY` -- exactly the
real, already-implemented API scopes from app/services/api_key.py
(alerts_read, reports_read, delivery_receive) plus the two catalog-
scope facts this build can genuinely support (portfolios_up_to_three,
research_api). "No implicit trading right" (AD-13's own field help
text): nothing resembling a trading/admin capability is ever a valid
feature id here, matching CU-16's own scope allowlist discipline.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.price_version import BillingInterval, PriceMode, PriceVersion

#: The exact real API scopes from app/services/api_key.py, plus two
#: catalog-scope facts this build can genuinely support. Deliberately
#: excludes anything resembling a trading/admin capability.
_REVIEWED_FEATURE_REGISTRY: frozenset[str] = frozenset(
    {"alerts_read", "reports_read", "delivery_receive", "portfolios_up_to_three", "research_api"}
)
_ONLY_AUTHORIZED_MODE = PriceMode.TEST


class InvalidPriceVersionError(Exception):
    pass


class SkuAlreadyExistsError(InvalidPriceVersionError):
    pass


def list_price_versions(session: Session, *, tenant_id: str) -> list[PriceVersion]:
    return list(
        session.scalars(
            select(PriceVersion).where(PriceVersion.tenant_id == tenant_id).order_by(PriceVersion.created_at.desc())
        ).all()
    )


def create_price_version(
    session: Session,
    *,
    tenant_id: str,
    sku: str,
    currency: str,
    amount_minor: int,
    interval: str,
    is_unlimited_portfolios: bool,
    portfolio_limit: int | None,
    features: list[str],
    mode: str,
) -> PriceVersion:
    if not sku or not sku.strip():
        raise InvalidPriceVersionError("sku is required")
    try:
        interval_enum = BillingInterval(interval)
    except ValueError as exc:
        raise InvalidPriceVersionError(f"{interval!r} is not a known interval") from exc
    try:
        mode_enum = PriceMode(mode)
    except ValueError as exc:
        raise InvalidPriceVersionError(f"{mode!r} is not a known mode") from exc
    if amount_minor < 0:
        raise InvalidPriceVersionError("amount_minor must be >= 0")
    if not features:
        raise InvalidPriceVersionError("at least one feature is required")
    unknown = sorted(set(features) - _REVIEWED_FEATURE_REGISTRY)
    if unknown:
        raise InvalidPriceVersionError(f"unknown or unauthorized feature(s): {unknown} -- no implicit trading right")
    if not is_unlimited_portfolios and (portfolio_limit is None or portfolio_limit < 0):
        raise InvalidPriceVersionError("portfolio_limit must be a nonnegative integer unless explicitly unlimited")

    if mode_enum != _ONLY_AUTHORIZED_MODE:
        raise InvalidPriceVersionError(
            "LIVE_MODE_NOT_AUTHORIZED: no merchant/owner live-price approval workflow exists in this build -- "
            f"only {_ONLY_AUTHORIZED_MODE.value!r} can be saved"
        )

    existing = session.scalars(select(PriceVersion).where(PriceVersion.sku == sku)).first()
    if existing is not None:
        raise SkuAlreadyExistsError(f"sku {sku!r} is already in use")

    price_version = PriceVersion(
        tenant_id=tenant_id,
        sku=sku,
        currency=currency,
        amount_minor=amount_minor,
        interval=interval_enum,
        is_unlimited_portfolios=is_unlimited_portfolios,
        portfolio_limit=None if is_unlimited_portfolios else portfolio_limit,
        features=list(features),
        mode=mode_enum,
    )
    session.add(price_version)
    session.flush()
    return price_version
