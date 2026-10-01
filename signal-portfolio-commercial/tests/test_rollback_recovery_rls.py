"""Track 33: the ~8 "rollback-recovery" routes in
`app/api/dashboard_routes.py` (e.g. `create_product_draft`) do
`session.rollback()` on a validation error and then manually re-call
`set_tenant_scope()` -- see `require_tenant_scope`'s own docstring
(app/api/dependencies.py) for why that re-call is needed (`rollback()`
clears a `set_config(..., is_local=true)` the same way `COMMIT`/ROLLBACK
always clears any other `SET LOCAL`). Those routes ARE exercised at the
HTTP level (tests/test_dashboard_routes.py's duplicate-slug test), but
only through that file's `_client`, which binds the Postgres SUPERUSER
session directly as the dependency override -- a superuser bypasses RLS
entirely, so passing that test proves the *application-level* `tenant_id`
filter in `list_products` works, never that the manual post-rollback
`set_tenant_scope` call actually restores real RLS for whatever query
runs next in that request.

This file closes that gap: it drives the exact same duplicate-slug
rollback-recovery path over a REAL, non-superuser `app_role` session
(this module's own `_client_on_app_role`, the same shape as
tests/test_require_tenant_scope_dependency.py's), with two tenants'
draft products present, and proves an UNFILTERED query issued right
after the rollback + re-scope -- not `list_products`'s own tenant_id-
filtered one, which would look correct even if RLS itself were silently
broken -- still only sees the calling tenant's own rows.
"""
from fastapi import Depends, Form
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session, require_tenant_scope
from app.main import create_app
from app.models.product import Product
from app.models.tenancy import MembershipRole
from app.services.auth import TenantScope, issue_token
from app.services.product_admin import SlugAlreadyExistsError, create_draft_product


def _seed_product(db_session, *, tenant_id: str, product_id: str, slug: str) -> None:
    db_session.add(
        Product(product_id=product_id, tenant_id=tenant_id, product_name=f"Product for {tenant_id}", slug=slug)
    )
    db_session.commit()


def _client_on_app_role(tenant_session_factory):
    """Same shape as tests/test_require_tenant_scope_dependency.py's own
    `_client_on_app_role` -- a fresh `app_role` (RLS-subject) session per
    request, never the superuser `db_session` fixture."""
    app = create_app()

    def override_get_db_session():
        session = tenant_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_get_db_session
    return app, TestClient(app, follow_redirects=False)


def _auth_headers(tenant_id: str, user_id: str, role: MembershipRole = MembershipRole.OWNER) -> dict:
    token = issue_token(tenant_id, user_id, role)
    return {"Authorization": f"Bearer {token}"}


def test_real_production_route_rollback_recovery_does_not_leak_cross_tenant_via_app_role(
    db_session, tenant_session_factory
):
    """The real `/ops/products` POST route, over real `app_role` RLS, with
    both tenants' drafts present: tenant-a's error-page re-render (after
    the rollback + manual re-scope) must show only tenant-a's own
    product, never tenant-b's -- same assertion
    tests/test_dashboard_routes.py::test_create_draft_rejects_a_duplicate_slug_with_an_error_banner
    makes, but now genuinely RLS-enforced rather than superuser-bypassed."""
    _seed_product(db_session, tenant_id="tenant-a", product_id="prod-a-dup", slug="dup-rls")
    _seed_product(db_session, tenant_id="tenant-b", product_id="prod-b-other", slug="tenant-b-own-slug")

    _app, client = _client_on_app_role(tenant_session_factory)
    headers = _auth_headers("tenant-a", "user-a")

    response = client.post("/ops/products", data={"product_name": "Dup", "slug": "dup-rls"}, headers=headers)

    assert response.status_code == 400
    assert "already in use" in response.text
    # The error-page re-render runs `list_products` right after the
    # rollback + re-scope -- it must show tenant-a's own product and must
    # NOT show tenant-b's, even though tenant-b's row is a real row in the
    # same table.
    assert "Product for tenant-a" in response.text
    assert "Product for tenant-b" not in response.text


def test_rollback_then_reset_scope_then_unfiltered_query_is_still_rls_scoped(
    db_session, tenant_session_factory
):
    """The load-bearing proof: a route whose entire body is the EXACT
    rollback-recovery shape production code uses (`session.rollback()`
    then `set_tenant_scope(session, scope.tenant_id)`) followed by a
    deliberately UNFILTERED `select(Product)` -- no `tenant_id` where
    clause at all, so there is nothing but real Postgres RLS standing
    between this query and every tenant's rows. If the manual re-scope
    after `rollback()` did not actually restore the session variable RLS
    policies key off of, this would return both tenants' products (or,
    fail-closed, zero); it must return only the calling tenant's own."""
    _seed_product(db_session, tenant_id="tenant-a", product_id="prod-a-unfiltered", slug="unfiltered-rls-a")
    _seed_product(db_session, tenant_id="tenant-b", product_id="prod-b-unfiltered", slug="unfiltered-rls-b")

    app, client = _client_on_app_role(tenant_session_factory)

    @app.post("/__test_only/rollback_then_unfiltered_query")
    def rollback_recovery_probe(
        slug: str = Form(...),
        scope: TenantScope = Depends(require_tenant_scope),
        session: Session = Depends(get_db_session),
    ) -> dict:
        try:
            # A duplicate slug (seeded above, within THIS tenant's own
            # visible rows) deterministically raises, forcing the exact
            # same exception-driven rollback production code hits.
            create_draft_product(session, tenant_id=scope.tenant_id, product_name="x", slug=slug)
            session.commit()
        except SlugAlreadyExistsError:
            session.rollback()
            from app.db import set_tenant_scope

            set_tenant_scope(session, scope.tenant_id)

        rows = session.execute(select(Product)).scalars().all()  # deliberately unfiltered
        return {"tenant_ids_seen": sorted({p.tenant_id for p in rows}), "product_ids": sorted(p.product_id for p in rows)}

    response = client.post(
        "/__test_only/rollback_then_unfiltered_query",
        data={"slug": "unfiltered-rls-a"},
        headers=_auth_headers("tenant-a", "user-a"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tenant_ids_seen"] == ["tenant-a"]
    assert body["product_ids"] == ["prod-a-unfiltered"]

    response_b = client.post(
        "/__test_only/rollback_then_unfiltered_query",
        data={"slug": "unfiltered-rls-b"},
        headers=_auth_headers("tenant-b", "user-b"),
    )
    assert response_b.status_code == 200
    body_b = response_b.json()
    assert body_b["tenant_ids_seen"] == ["tenant-b"]
    assert body_b["product_ids"] == ["prod-b-unfiltered"]
