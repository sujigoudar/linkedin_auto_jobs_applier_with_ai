"""Audit finding (Track 28): `set_tenant_scope()` was called
imperatively at the top of ~95 separate route handlers in
`app/api/dashboard_routes.py` -- it worked (a forgotten call fails
closed under RLS, never leaks a row) but nothing caught a new route
that forgot the call except that same fail-closed behavior in
production. `app/api/dependencies.py::require_tenant_scope` centralizes
it as a single FastAPI dependency.

This file proves two things, both over real HTTP against the real,
non-superuser `app_role` Postgres login -- the same role real RLS
policies actually apply to (`tests/test_row_level_security.py`'s own
convention; `tests/test_dashboard_routes.py`'s own `db_session`-backed
`_client` runs as the Postgres *superuser*, which bypasses RLS
entirely, so it cannot be used to prove RLS itself holds):

(a) A real, existing dashboard route that now uses
    `Depends(require_tenant_scope)` still enforces tenant isolation
    correctly (`/ops/products`).
(b) A brand-new route that declares ONLY
    `scope: TenantScope = Depends(require_tenant_scope)` -- no
    `set_tenant_scope` call anywhere in its own body -- gets correct
    tenant isolation automatically, with no per-handler scoping code at
    all. This is the exact gap the dependency closes: before it
    existed, a route like this would have needed to remember the
    imperative call.
"""
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_session, require_tenant_scope
from app.main import create_app
from app.models.product import Product
from app.models.tenancy import MembershipRole
from app.services.auth import TenantScope, issue_token


def _seed_product(db_session, *, tenant_id: str, product_id: str, slug: str) -> None:
    db_session.add(
        Product(product_id=product_id, tenant_id=tenant_id, product_name=f"Product for {tenant_id}", slug=slug)
    )
    db_session.commit()


def _client_on_app_role(tenant_session_factory):
    """Same shape as tests/test_dashboard_routes.py's own `_client`, but
    overriding `get_db_session` with a fresh `app_role` (RLS-subject)
    session per request instead of the superuser `db_session` fixture --
    see this module's own docstring on why that distinction matters
    here."""
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


def test_existing_route_on_require_tenant_scope_still_enforces_rls(db_session, tenant_session_factory):
    """(a) `/ops/products` (app/api/dashboard_routes.py) now declares
    `Depends(require_tenant_scope)` in place of the old
    `Depends(get_current_scope)` + imperative `set_tenant_scope` call --
    confirm tenant-a still cannot see tenant-b's product, over real RLS."""
    _seed_product(db_session, tenant_id="tenant-a", product_id="prod-a-1", slug="product-a-1")
    _seed_product(db_session, tenant_id="tenant-b", product_id="prod-b-1", slug="product-b-1")

    _app, client = _client_on_app_role(tenant_session_factory)

    response_a = client.get("/ops/products", headers=_auth_headers("tenant-a", "user-a"))
    assert response_a.status_code == 200
    assert "product-a-1" in response_a.text or "Product for tenant-a" in response_a.text
    assert "product-b-1" not in response_a.text
    assert "Product for tenant-b" not in response_a.text

    response_b = client.get("/ops/products", headers=_auth_headers("tenant-b", "user-b"))
    assert response_b.status_code == 200
    assert "Product for tenant-b" in response_b.text
    assert "Product for tenant-a" not in response_b.text


def test_new_route_declaring_only_the_dependency_gets_tenant_isolation_automatically(
    db_session, tenant_session_factory
):
    """(b) A brand-new route is mounted here, for this test only, whose
    entire body is the dependency declaration plus an UNFILTERED
    `select(Product)` -- it never calls `set_tenant_scope` and never
    filters by `tenant_id` itself. If `require_tenant_scope` did nothing,
    or a future refactor broke it, this route would leak every tenant's
    products to every caller; real Postgres RLS (via the dependency) is
    the only thing standing between it and that leak."""
    _seed_product(db_session, tenant_id="tenant-a", product_id="prod-a-2", slug="product-a-2")
    _seed_product(db_session, tenant_id="tenant-b", product_id="prod-b-2", slug="product-b-2")

    app, client = _client_on_app_role(tenant_session_factory)

    @app.get("/__test_only/products_no_imperative_scope")
    def new_route_with_no_per_handler_scoping_code(
        scope: TenantScope = Depends(require_tenant_scope),
        session: Session = Depends(get_db_session),
    ) -> dict:
        rows = session.execute(select(Product)).scalars().all()
        return {"caller_tenant_id": scope.tenant_id, "product_ids": [p.product_id for p in rows]}

    response_a = client.get(
        "/__test_only/products_no_imperative_scope", headers=_auth_headers("tenant-a", "user-a")
    )
    assert response_a.status_code == 200
    body_a = response_a.json()
    assert body_a["product_ids"] == ["prod-a-2"]

    response_b = client.get(
        "/__test_only/products_no_imperative_scope", headers=_auth_headers("tenant-b", "user-b")
    )
    assert response_b.status_code == 200
    body_b = response_b.json()
    assert body_b["product_ids"] == ["prod-b-2"]


def test_no_tenant_scope_at_all_yields_zero_rows_not_all_rows(db_session, tenant_session_factory):
    """Fail-closed proof for the same new-route shape, directly against
    `app_role` with no `set_tenant_scope` call at all (no dependency, no
    manual call) -- mirrors
    tests/test_row_level_security.py::test_no_tenant_scope_set_means_no_rows_visible_not_all_rows
    for `Product`."""
    _seed_product(db_session, tenant_id="tenant-a", product_id="prod-a-3", slug="product-a-3")
    _seed_product(db_session, tenant_id="tenant-b", product_id="prod-b-3", slug="product-b-3")

    session = tenant_session_factory()
    try:
        rows = session.execute(select(Product)).scalars().all()
        assert rows == []
    finally:
        session.close()
