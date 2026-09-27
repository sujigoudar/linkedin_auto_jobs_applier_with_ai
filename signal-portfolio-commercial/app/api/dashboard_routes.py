"""Real HTTP routes + Jinja2 templates for the first dashboard vertical
slice: AD-07 "Products and portfolio versions" (admin) and PU-02
"Portfolio catalog" (public), per dashboard_spec/screens/AD-07.md and
dashboard_spec/screens/PU-02.md.

Deliberately bounded to what that slice actually needs: create/save/
reload a draft, see its exact publication blockers, and a truthfully
empty public catalog until something is really published. No publish/
release-review action is implemented here at all -- that is a distinct,
separately reviewed admission decision (app/services/publication_admission.py)
this screen has no authority over, matching AD-07's own "Confirm: Submit
version for review; not directly publish."
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

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_ALL_SERVICE_MODES = [mode.value for mode in ServiceMode]


def _require_product_admin(scope: TenantScope) -> None:
    try:
        require_permission(scope.role, "manage_product_draft")
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


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


@router.get("/portfolios")
def public_portfolio_catalog(request: Request, session: Session = Depends(get_db_session)):
    """Anonymous, no tenant scope set -- exactly what a real unauthenticated
    visitor looks like. `list_published_products` (and the underlying
    `product_visibility` RLS policy) is what keeps this truthfully empty
    until a product is genuinely PUBLISHED, never an application-level
    "if logged in" check standing in for it."""
    products = list_published_products(session)
    return templates.TemplateResponse(request, "pu02_catalog.html", {"products": products})
