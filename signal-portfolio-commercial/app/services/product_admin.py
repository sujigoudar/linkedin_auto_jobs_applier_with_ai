"""AD-07 "Products and portfolio versions" -- the real query/command
service backing the admin Products screen. See
dashboard_spec/screens/AD-07.md for the full screen contract this
implements a bounded slice of.

Reads are always tenant-scoped through the caller's own already-set RLS
session (app/db.py's set_tenant_scope) -- this module never accepts or
trusts a caller-supplied tenant_id for authorization, only for
recording which tenant a NEW draft belongs to (and even then, the
caller is responsible for having already scoped the session to that
same tenant_id, so a cross-tenant write is stopped by RLS regardless of
what this function is told).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.portfolio_version import PortfolioVersion, PortfolioVersionSleeve
from app.models.product import Product, ProductLifecycleState, ServiceMode
from app.models.rights import RightsUse
from app.models.sleeve import Sleeve
from app.services.portfolio_rights import check_portfolio_rights

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class InvalidProductDraftError(Exception):
    pass


class SlugAlreadyExistsError(Exception):
    pass


class StaleRevisionError(Exception):
    pass


def list_products(session: Session, *, tenant_id: str) -> list[Product]:
    """This tenant's own products, in any lifecycle state -- correctly
    returns an empty list for a tenant with none, per docs/12: "Open
    Products; the repository returns zero rows... This is a complete,
    useful implementation, not scaffolding around nothing."

    RLS already restricts the underlying rows to "this session's tenant,
    or published" (app/db.py's `product_visibility` policy), but that
    policy alone would let this admin listing leak OTHER tenants'
    published products in among "my products". Filtering by `tenant_id`
    here explicitly is defense in depth, not a substitute for RLS: it's
    what keeps this specific query honestly scoped to "mine", while
    `list_published_products` below is the one that intentionally spans
    every tenant's published rows."""
    return list(
        session.scalars(
            select(Product).where(Product.tenant_id == tenant_id).order_by(Product.created_at.desc())
        ).all()
    )


def get_product(session: Session, product_id: str, *, tenant_id: str) -> Product | None:
    """Returns None both when the ID never existed AND when it belongs
    to another tenant -- callers must turn a None here into a 404, never
    a 403, so a cross-tenant URL guess reveals nothing (docs/12's "scoped
    not-found"). Filters by `tenant_id` explicitly rather than relying
    solely on RLS, since the `product_visibility` policy alone would let
    a published product belonging to another tenant come back non-None
    here -- correct for the public catalog, wrong for "fetch MY draft by
    ID"."""
    product = session.get(Product, product_id)
    if product is None or product.tenant_id != tenant_id:
        return None
    return product


def create_draft_product(session: Session, *, tenant_id: str, product_name: str, slug: str) -> Product:
    """Create a new DRAFT product. Fails fast on an invalid/duplicate
    slug rather than silently normalizing it -- "unique lower-case URL
    slug" (AD-07's own validation text)."""
    if not product_name or not product_name.strip():
        raise InvalidProductDraftError("product_name is required")
    if not _SLUG_RE.match(slug):
        raise InvalidProductDraftError(f"{slug!r} is not a valid lower-case URL slug")

    existing = session.scalars(select(Product).where(Product.slug == slug)).first()
    if existing is not None:
        raise SlugAlreadyExistsError(f"slug {slug!r} is already in use")

    product = Product(tenant_id=tenant_id, product_name=product_name, slug=slug)
    session.add(product)
    session.flush()
    return product


def update_product_draft(
    session: Session,
    product: Product,
    *,
    expected_revision: int,
    product_name: str | None = None,
    portfolio_version_id: str | None = None,
    cash_bps: int | None = None,
    service_modes: list[str] | None = None,
    audience_policy_id: str | None = None,
    research_report_id: str | None = None,
    methodology_document_id: str | None = None,
) -> Product:
    """Updates only the fields explicitly provided -- "Partial drafts can
    save typed provided fields" (docs/12). Requires `expected_revision`
    to match the product's CURRENT revision, and bumps it on success --
    a stale client (one that read an older revision) is rejected with
    StaleRevisionError rather than silently overwriting a newer edit
    (AD-07's own "conflict" state obligation)."""
    if product.revision != expected_revision:
        raise StaleRevisionError(
            f"expected revision {expected_revision}, product is now at revision {product.revision}"
        )

    if product_name is not None:
        if not product_name.strip():
            raise InvalidProductDraftError("product_name cannot be blank")
        product.product_name = product_name
    if portfolio_version_id is not None:
        product.portfolio_version_id = portfolio_version_id
    if cash_bps is not None:
        if not (0 <= cash_bps <= 10_000):
            raise InvalidProductDraftError("cash_bps must be between 0 and 10000")
        product.cash_bps = cash_bps
    if service_modes is not None:
        valid = {mode.value for mode in ServiceMode}
        unknown = set(service_modes) - valid
        if unknown:
            raise InvalidProductDraftError(f"unknown service_modes: {sorted(unknown)}")
        product.service_modes = service_modes
    if audience_policy_id is not None:
        product.audience_policy_id = audience_policy_id
    if research_report_id is not None:
        product.research_report_id = research_report_id
    if methodology_document_id is not None:
        product.methodology_document_id = methodology_document_id

    product.revision += 1
    product.updated_at = datetime.now(timezone.utc)
    session.flush()
    return product


def compute_publication_blockers(session: Session, product: Product) -> list[str]:
    """The exact, named prerequisites still missing before `product`
    could pass release review -- AD-07's own "Preview": "Render private
    no-store audience-safe preview with missing-data messages." Never
    collapses these into one boolean; every screen consuming this shows
    the full list, per the same screen's panel contract: "Do not
    collapse payment, connection, rights and trading authority into one
    active badge." An empty list means nothing is currently blocking --
    it does NOT mean the product is published (that's a separate,
    explicit review/confirm step this function has no authority over).
    """
    blockers: list[str] = []

    if product.portfolio_version_id is None:
        blockers.append("NO_PORTFOLIO_VERSION_SELECTED")
    else:
        weights = session.scalars(
            select(PortfolioVersionSleeve).where(
                PortfolioVersionSleeve.portfolio_version_id == product.portfolio_version_id
            )
        ).all()
        if not weights:
            blockers.append("PORTFOLIO_VERSION_HAS_NO_SLEEVES")
        else:
            total_bps = product.cash_bps + sum(int(w.weight * 10_000) for w in weights)
            if total_bps != 10_000:
                blockers.append("WEIGHTS_DO_NOT_CONSERVE_TO_TEN_THOUSAND_BPS")

            portfolio_version = session.get(PortfolioVersion, product.portfolio_version_id)
            if portfolio_version is not None:
                sleeves_by_id = {w.sleeve_id: session.get(Sleeve, w.sleeve_id) for w in weights}
                unknown_sleeve_ids = [sid for sid, sleeve in sleeves_by_id.items() if sleeve is None]
                for sleeve_id in unknown_sleeve_ids:
                    blockers.append(f"UNKNOWN_SLEEVE:{sleeve_id}")

                known_sleeves = [sleeve for sleeve in sleeves_by_id.values() if sleeve is not None]
                if not unknown_sleeve_ids:
                    #: `check_portfolio_rights` already checks every member
                    #: sleeve for a given `asset`, so one call per DISTINCT
                    #: asset class among this portfolio's sleeves covers
                    #: the whole set -- never one call per sleeve, and
                    #: never a single call using only one sleeve's asset
                    #: class to stand in for sleeves of a different class.
                    asset_classes = {sleeve.asset_class for sleeve in known_sleeves}
                    for asset_class in sorted(asset_classes):
                        rights_result = check_portfolio_rights(
                            session,
                            portfolio_version_id=product.portfolio_version_id,
                            use=RightsUse.COMMERCIAL_ALERTS,
                            channel="web",
                            jurisdiction="US",
                            asset=asset_class,
                        )
                        if not rights_result.allowed:
                            blockers.append("RIGHTS_NOT_GRANTED_FOR_A_MEMBER_SLEEVE")
                            break

    if not product.service_modes:
        blockers.append("NO_SERVICE_MODE_SELECTED")
    if product.audience_policy_id is None:
        blockers.append("NO_AUDIENCE_POLICY")
    if product.research_report_id is None:
        blockers.append("NO_RESEARCH_EVIDENCE")
    if product.methodology_document_id is None:
        blockers.append("NO_METHODOLOGY_DOCUMENT")

    return blockers


def list_published_products(session: Session) -> list[Product]:
    """The public catalog's own query -- "The public catalog stays
    truthfully empty" until a real release review approves a product.
    Deliberately identical in shape to `list_products`, filtered to
    PUBLISHED only, so there is exactly one code path that decides what
    counts as published."""
    return list(
        session.scalars(
            select(Product)
            .where(Product.lifecycle_state == ProductLifecycleState.PUBLISHED)
            .order_by(Product.product_name)
        ).all()
    )
