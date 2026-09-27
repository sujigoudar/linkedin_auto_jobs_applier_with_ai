"""Real HTTP routes + Jinja2 templates for the dashboard build, per
dashboard_spec/screens/*.md. Each screen here is a bounded, honestly
scoped slice of its full spec -- see each route's own docstring for
what is and is not implemented yet.

- AD-07 "Products and portfolio versions" (admin) + PU-02 "Portfolio
  catalog" (public): create/save/reload a Product draft, see its exact
  publication blockers, and a truthfully empty public catalog until
  something is really published. Submitting a zero-blocker draft for
  review (AD-07's own "Confirm: Submit version for review; not directly
  publish") is now real too -- see AD-08 below. Actual publication
  remains a distinct, separately reviewed admission decision
  (app/services/publication_admission.py) neither screen has authority
  over.
- AD-02 "Rights and service approvals": a read-only grant register.
  Create/attach-evidence/approve are NOT implemented -- they need
  private document upload/malware-scan infrastructure and an audit-
  logged approval workflow that don't exist yet; building an empty
  create form around them would be exactly the kind of scaffolding
  around nothing this build avoids.
- AD-03 "Research universe and sleeves": create/list real sleeve
  lineage records (reusing the Phase 04 `Sleeve` model as-is). No
  distinct DRAFT/QUALIFIED lifecycle or "Confirm: qualified status"
  step -- that needs real coverage/overlap statistics against actual
  historical data this environment doesn't have.
- AD-01 "Commercial operations overview": a real cross-subsystem
  summary (release blockers per product, active subscriptions,
  unknown-state publications). "Open incidents" has no backing model
  at all yet, so it's shown as unsupported, never a fabricated zero.
- AD-08 "Release and change approvals": a real release-review queue.
  Requesting review (from AD-07) is refused unless the product's
  publication blockers are genuinely empty; deciding enforces a real
  independent-reviewer gate (the reviewer cannot be the proposer) and
  refuses a stale review (the product changed since it was requested).
  Approving moves a Product to APPROVED, never to PUBLISHED -- release
  is not publication.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_scope, get_db_session
from app.db import set_tenant_scope
from app.models.product import ServiceMode
from app.services.auth import TenantScope
from app.services.permissions import PermissionDenied, require_permission
from app.services.product_admin import (
    InvalidProductDraftError,
    SlugAlreadyExistsError,
    StaleRevisionError,
    compute_publication_blockers,
    create_draft_product,
    get_product,
    list_products,
    list_published_products,
    update_product_draft,
)
from app.services.operations_overview import get_operations_overview
from app.services.release_review import (
    InvalidReviewDecisionError,
    ReviewNotEligibleError,
    SelfReviewNotAllowedError,
    StaleReviewTargetError,
    decide_release_review,
    get_release_review,
    list_release_reviews,
    request_release_review,
)
from app.services.rights_registry import list_rights_grants
from app.services.sleeve_admin import InvalidSleeveDraftError, create_sleeve, list_sleeves

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_ALL_SERVICE_MODES = [mode.value for mode in ServiceMode]


def _require_product_admin(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "manage_product_draft")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/ops")
def operations_overview_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """AD-01 "Commercial operations overview" -- see this route module's
    own docstring above for what's deliberately not built (Open
    incidents has no backing model, so it's rendered as unsupported)."""
    try:
        require_permission(scope.role, "view_operations_overview")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    set_tenant_scope(session, scope.tenant_id)
    overview = get_operations_overview(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(request, "ad01_overview.html", {"overview": overview})


@router.get("/ops/products")
def list_products_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_product_admin(scope)
    set_tenant_scope(session, scope.tenant_id)
    products = list_products(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(
        request,
        "ad07_products.html",
        {"products": products, "tenant_id": scope.tenant_id, "role": scope.role.value},
    )


@router.post("/ops/products")
def create_product_draft(
    request: Request,
    product_name: str = Form(...),
    slug: str = Form(...),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_product_admin(scope)
    set_tenant_scope(session, scope.tenant_id)
    try:
        product = create_draft_product(session, tenant_id=scope.tenant_id, product_name=product_name, slug=slug)
        session.commit()
    except (InvalidProductDraftError, SlugAlreadyExistsError) as exc:
        session.rollback()
        set_tenant_scope(session, scope.tenant_id)
        products = list_products(session, tenant_id=scope.tenant_id)
        return templates.TemplateResponse(
            request,
            "ad07_products.html",
            {
                "products": products,
                "tenant_id": scope.tenant_id,
                "role": scope.role.value,
                "error": str(exc),
            },
            status_code=400,
        )
    return RedirectResponse(url=f"/ops/products/{product.product_id}", status_code=303)


@router.get("/ops/products/{product_id}")
def product_detail_page(
    product_id: str,
    request: Request,
    conflict: int = 0,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_product_admin(scope)
    set_tenant_scope(session, scope.tenant_id)
    product = get_product(session, product_id, tenant_id=scope.tenant_id)
    if product is None:
        raise HTTPException(status_code=404, detail="not found")

    blockers = compute_publication_blockers(session, product)
    return templates.TemplateResponse(
        request,
        "ad07_product_detail.html",
        {
            "product": product,
            "blockers": blockers,
            "all_service_modes": _ALL_SERVICE_MODES,
            "conflict": bool(conflict),
        },
    )


@router.post("/ops/products/{product_id}")
def update_product_draft_route(
    product_id: str,
    request: Request,
    expected_revision: int = Form(...),
    product_name: str = Form(...),
    portfolio_version_id: str = Form(""),
    cash_bps: int = Form(...),
    service_modes: list[str] = Form([]),
    audience_policy_id: str = Form(""),
    research_report_id: str = Form(""),
    methodology_document_id: str = Form(""),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_product_admin(scope)
    set_tenant_scope(session, scope.tenant_id)
    product = get_product(session, product_id, tenant_id=scope.tenant_id)
    if product is None:
        raise HTTPException(status_code=404, detail="not found")

    try:
        update_product_draft(
            session,
            product,
            expected_revision=expected_revision,
            product_name=product_name,
            portfolio_version_id=portfolio_version_id or None,
            cash_bps=cash_bps,
            service_modes=service_modes,
            audience_policy_id=audience_policy_id or None,
            research_report_id=research_report_id or None,
            methodology_document_id=methodology_document_id or None,
        )
        session.commit()
    except StaleRevisionError:
        session.rollback()
        return RedirectResponse(url=f"/ops/products/{product_id}?conflict=1", status_code=303)
    except InvalidProductDraftError as exc:
        session.rollback()
        set_tenant_scope(session, scope.tenant_id)
        product = get_product(session, product_id, tenant_id=scope.tenant_id)
        blockers = compute_publication_blockers(session, product) if product is not None else []
        return templates.TemplateResponse(
            request,
            "ad07_product_detail.html",
            {
                "product": product,
                "blockers": blockers,
                "all_service_modes": _ALL_SERVICE_MODES,
                "conflict": False,
                "error": str(exc),
            },
            status_code=400,
        )

    return RedirectResponse(url=f"/ops/products/{product_id}", status_code=303)


@router.post("/ops/products/{product_id}/request-review")
def request_release_review_route(
    product_id: str,
    request: Request,
    evidence_manifest_id: str = Form(...),
    audience_policy_id: str = Form(...),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """AD-07's own "Confirm: Submit version for review" -- creates a
    real AD-08 release review. Reuses `manage_product_draft`: proposing
    a review is a research/release decision, same trio of roles as
    drafting the product itself."""
    _require_product_admin(scope)
    set_tenant_scope(session, scope.tenant_id)
    product = get_product(session, product_id, tenant_id=scope.tenant_id)
    if product is None:
        raise HTTPException(status_code=404, detail="not found")

    try:
        review = request_release_review(
            session,
            tenant_id=scope.tenant_id,
            product=product,
            proposer_user_id=scope.user_id,
            evidence_manifest_id=evidence_manifest_id,
            audience_policy_id=audience_policy_id,
        )
        session.commit()
    except ReviewNotEligibleError as exc:
        session.rollback()
        set_tenant_scope(session, scope.tenant_id)
        product = get_product(session, product_id, tenant_id=scope.tenant_id)
        blockers = compute_publication_blockers(session, product) if product is not None else []
        return templates.TemplateResponse(
            request,
            "ad07_product_detail.html",
            {
                "product": product,
                "blockers": blockers,
                "all_service_modes": _ALL_SERVICE_MODES,
                "conflict": False,
                "error": str(exc),
            },
            status_code=400,
        )
    return RedirectResponse(url=f"/ops/reviews/{review.release_review_id}", status_code=303)


def _require_release_reviewer(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "release_strategy")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/ops/reviews")
def release_review_queue_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_release_reviewer(scope)
    set_tenant_scope(session, scope.tenant_id)
    reviews = list_release_reviews(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(request, "ad08_reviews.html", {"reviews": reviews})


@router.get("/ops/reviews/{release_review_id}")
def release_review_detail_page(
    release_review_id: str,
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_release_reviewer(scope)
    set_tenant_scope(session, scope.tenant_id)
    review = get_release_review(session, release_review_id, tenant_id=scope.tenant_id)
    if review is None:
        raise HTTPException(status_code=404, detail="not found")
    product = get_product(session, review.product_id, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(
        request, "ad08_review_detail.html", {"review": review, "product": product}
    )


@router.post("/ops/reviews/{release_review_id}")
def decide_release_review_route(
    release_review_id: str,
    request: Request,
    decision: str = Form(...),
    reason: str = Form(...),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_release_reviewer(scope)
    set_tenant_scope(session, scope.tenant_id)
    review = get_release_review(session, release_review_id, tenant_id=scope.tenant_id)
    if review is None:
        raise HTTPException(status_code=404, detail="not found")
    product = get_product(session, review.product_id, tenant_id=scope.tenant_id)
    if product is None:
        raise HTTPException(status_code=404, detail="not found")

    try:
        decide_release_review(
            session, review, product, reviewer_user_id=scope.user_id, decision=decision, reason=reason
        )
        session.commit()
    except (SelfReviewNotAllowedError, StaleReviewTargetError, ReviewNotEligibleError, InvalidReviewDecisionError) as exc:
        session.rollback()
        set_tenant_scope(session, scope.tenant_id)
        review = get_release_review(session, release_review_id, tenant_id=scope.tenant_id)
        product = get_product(session, review.product_id, tenant_id=scope.tenant_id) if review is not None else None
        return templates.TemplateResponse(
            request,
            "ad08_review_detail.html",
            {"review": review, "product": product, "error": str(exc)},
            status_code=400,
        )

    return RedirectResponse(url=f"/ops/reviews/{release_review_id}", status_code=303)


@router.get("/ops/rights")
def rights_register_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    """AD-02 "Rights and service approvals" -- read-only grant register
    (this route's own docstring above explains what's deliberately not
    built yet: create/attach-evidence/approve). `RightsGrant` carries no
    tenant_id, so there is no `set_tenant_scope` call here -- visibility
    is gated purely by role (owner/reviewer), not by tenant."""
    try:
        require_permission(scope.role, "view_rights_register")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    grants = list_rights_grants(session)
    return templates.TemplateResponse(request, "ad02_rights.html", {"grants": grants})


def _require_sleeve_admin(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "manage_sleeve_draft")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/ops/research/universe")
def sleeve_catalog_page(
    request: Request,
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_sleeve_admin(scope)
    set_tenant_scope(session, scope.tenant_id)
    sleeves = list_sleeves(session, tenant_id=scope.tenant_id)
    return templates.TemplateResponse(
        request,
        "ad03_sleeves.html",
        {"sleeves": sleeves, "tenant_id": scope.tenant_id, "role": scope.role.value},
    )


@router.post("/ops/research/universe")
def create_sleeve_route(
    request: Request,
    provider: str = Form(...),
    analyst: str = Form(...),
    strategy_horizon: str = Form(...),
    asset_class: str = Form(...),
    parser_version: str = Form(...),
    execution_policy_id: str = Form(...),
    cost_model_id: str = Form(...),
    capacity_policy_id: str = Form(...),
    risk_unit_id: str = Form(...),
    history_origin: str = Form(...),
    scope: TenantScope = Depends(get_current_scope),
    session: Session = Depends(get_db_session),
):
    _require_sleeve_admin(scope)
    set_tenant_scope(session, scope.tenant_id)
    try:
        create_sleeve(
            session,
            tenant_id=scope.tenant_id,
            provider=provider,
            analyst=analyst,
            strategy_horizon=strategy_horizon,
            asset_class=asset_class,
            parser_version=parser_version,
            execution_policy_id=execution_policy_id,
            cost_model_id=cost_model_id,
            capacity_policy_id=capacity_policy_id,
            risk_unit_id=risk_unit_id,
            history_origin=history_origin,
        )
        session.commit()
    except InvalidSleeveDraftError as exc:
        session.rollback()
        set_tenant_scope(session, scope.tenant_id)
        sleeves = list_sleeves(session, tenant_id=scope.tenant_id)
        return templates.TemplateResponse(
            request,
            "ad03_sleeves.html",
            {
                "sleeves": sleeves,
                "tenant_id": scope.tenant_id,
                "role": scope.role.value,
                "error": str(exc),
            },
            status_code=400,
        )
    return RedirectResponse(url="/ops/research/universe", status_code=303)


@router.get("/portfolios")
def public_portfolio_catalog(request: Request, session: Session = Depends(get_db_session)):
    """Anonymous, no tenant scope set -- exactly what a real unauthenticated
    visitor looks like. `list_published_products` (and the underlying
    `product_visibility` RLS policy) is what keeps this truthfully empty
    until a product is genuinely PUBLISHED, never an application-level
    "if logged in" check standing in for it."""
    products = list_published_products(session)
    return templates.TemplateResponse(request, "pu02_catalog.html", {"products": products})
