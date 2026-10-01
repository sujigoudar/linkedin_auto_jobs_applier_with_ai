"""The commercial_api FastAPI app skeleton -- real HTTP routes wiring
together the services built in Phases 01-12, deliberately kept to the
few endpoints that need no external account/credential to be genuine:
a truthful health check, an authenticated "who am I" that exercises the
real JWT + tenant-scope stack end to end, and the Stripe webhook
receiver (signature verification only needs the shared secret both
sides already have -- it needs no real Stripe account to be correct,
only to be USED for real).

Deliberately NOT here: any route that would admit new financial
exposure, publish to a real channel, or process a real payment --
those all sit behind CARD-3/CARD-4 (real platform/processor
credentials) that do not exist in this environment. Adding routes for
capabilities that can't be exercised for real would be scaffolding
around nothing, which this build has consistently avoided.

`app/api/dashboard_routes.py` (included below) is the first dashboard
vertical slice on top of this skeleton -- draft/save/reload a Product
and see its exact publication blockers (AD-07), plus the truthfully
empty public catalog (PU-02). Same rule applies there: no publish/
release-review route exists yet, only what's genuinely buildable
without a real release decision.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import config
from app.api.dashboard_routes import router as dashboard_router
from app.api.dependencies import get_current_scope, get_db_session
from app.api.relay_routes import router as relay_router
from app.db import make_engine, make_session_factory, set_tenant_scope
from app.metrics import render_metrics
from app.rate_limit import limiter
from app.services.auth import TenantScope
from app.services.permissions import PermissionDenied, require_permission
from app.services.service_health import record_health_sample
from app.services.stripe_webhook import (
    InvalidSignatureHeaderError,
    SignatureMismatchError,
    StaleTimestampError,
    record_event_if_new,
    verify_signature,
)


STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(database_url: str | None = None, relay_database_url: str | None = None) -> FastAPI:
    app = FastAPI(title="signal-portfolio-commercial")
    engine = make_engine(database_url)
    app.state.session_factory = make_session_factory(engine)
    #: A SEPARATE engine, bound to the restricted `relay_role` connection
    #: (app/db.py's `_apply_relay_role_access`) -- app/api/relay_routes.py
    #: is the only route that uses it, via `get_relay_db_session`. Never
    #: reuses `app.state.session_factory` (the admin/app connection).
    relay_engine = make_engine(relay_database_url or config.RELAY_DATABASE_URL)
    app.state.relay_session_factory = make_session_factory(relay_engine)
    app.include_router(dashboard_router)
    app.include_router(relay_router)

    #: C06-equivalent (bounded): per-IP rate limiting for the one public
    #: route that fans out into a cross-service call
    #: (`POST /portfolios/{slug}/fit-simulation` -- see app/rate_limit.py).
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]  # slowapi's handler is typed narrower (RateLimitExceeded, not the generic Exception Starlette expects) than the real, correct runtime behavior needs
    app.add_middleware(SlowAPIMiddleware)

    #: Locally-pinned Chart.js vendor file (no CDN, no build step), same
    #: convention as signal-copier's own `app/main.py` static mount --
    #: used only by PU-03's "Try our fit simulator" equity-curve chart
    #: (app/templates/pu03_portfolio_detail.html). Public, unauthenticated:
    #: this is a static library file, not application data.
    app.mount("/static/vendor", StaticFiles(directory=STATIC_DIR / "vendor"), name="vendor")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/health")
    def health(session: Session = Depends(get_db_session)) -> dict:
        """Track 11 -- public, minimal, truthful liveness/readiness,
        matching signal-copier's own GET /health pattern (app/main.py
        there): `status` is never hardcoded "ok" independent of the
        flags next to it, and a check that could not run reports False
        rather than being silently omitted. This process has no
        background trading workers to report on (that shape is
        signal-copier's own, not this one's) -- the one real readiness
        signal this process has is whether its own database connection
        actually works, so that is exactly, and only, what `database_ok`
        reports; nothing here is invented to look like a busier health
        response than this process's real components justify. Uses the
        request-scoped `get_db_session` dependency (not
        `app.state.session_factory` directly) so this route is
        exercised the same way every other route in this app is --
        including under tests/conftest.py's `get_db_session` override."""
        try:
            session.execute(text("SELECT 1"))
            database_ok = True
        except Exception:  # noqa: BLE001 - health check must never raise
            database_ok = False

        return {
            "status": "ok" if database_ok else "degraded",
            "database_ok": database_ok,
        }

    @app.get("/metrics")
    def metrics(
        scope: TenantScope = Depends(get_current_scope),
        session: Session = Depends(get_db_session),
    ) -> Response:
        """Track 11 -- private aggregate operational metrics (Prometheus
        text format), owner/publisher_operator-session protected, unlike
        `/health` -- same reasoning signal-copier's own GET /metrics
        docstring gives: these numbers are operational detail an
        anonymous caller has no business reading. See app/metrics.py."""
        try:
            require_permission(scope.role, "view_deployment_status")
        except PermissionDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        set_tenant_scope(session, scope.tenant_id)
        body = render_metrics(session, tenant_id=scope.tenant_id)
        return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.get("/api/v1/me")
    def me(
        scope: TenantScope = Depends(get_current_scope),
        session: Session = Depends(get_db_session),
    ) -> dict:
        set_tenant_scope(session, scope.tenant_id)
        return {"tenant_id": scope.tenant_id, "user_id": scope.user_id, "role": scope.role.value}

    @app.post("/api/v1/billing/webhook/stripe")
    async def stripe_webhook(request: Request, session: Session = Depends(get_db_session)) -> dict:
        payload = await request.body()
        sig_header = request.headers.get("stripe-signature", "")
        try:
            verify_signature(payload, sig_header, config.STRIPE_WEBHOOK_SECRET)
        except (InvalidSignatureHeaderError, SignatureMismatchError, StaleTimestampError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            event = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="invalid JSON body") from exc

        event_id = event.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise HTTPException(status_code=400, detail="event is missing a string 'id'")

        is_new = record_event_if_new(session, event_id)
        session.commit()
        return {"received": True, "processed": is_new}

    @app.on_event("startup")
    async def _refuse_to_boot_commercial_live_with_placeholder_secrets() -> None:
        """A real deployment with `ENVIRONMENT=COMMERCIAL_LIVE` must never
        boot with any of this build's repo-committed, publicly-known
        placeholder secrets (app/config.py's own `LOCAL_JWT_SECRET`,
        `RELAY_SIGNING_SECRET`, `CATALOG_FIT_SIM_SIGNING_SECRET`,
        `STRIPE_WEBHOOK_SECRET` defaults) still in effect -- booting
        anyway would mean every JWT/relay/catalog-fit-sim signature and
        every Stripe webhook signature check is trivially forgeable by
        anyone who has read this repo. Every other environment value
        (LOCAL_SIM, INTEGRATION_ISOLATED, PLATFORM_DEMO, PRIVATE_SHADOW)
        is unaffected -- this check is a hard gate on COMMERCIAL_LIVE
        only, never a general secret-strength policy."""
        if config.ENVIRONMENT != config.COMMERCIAL_LIVE_ENVIRONMENT:
            return
        still_default = config.placeholder_secrets_in_use()
        if still_default:
            raise RuntimeError(
                "Refusing to start with ENVIRONMENT=COMMERCIAL_LIVE while still carrying the "
                "repo-committed placeholder default for: "
                f"{', '.join(still_default)}. Set a real, rotated value for each of these via a "
                "secrets manager before deploying to COMMERCIAL_LIVE."
            )

    if config.HEALTH_SAMPLER_ENABLED:

        @app.on_event("startup")
        async def _start_health_sampler() -> None:
            app.state.health_sampler_task = asyncio.create_task(_health_sampler_loop(app))

        @app.on_event("shutdown")
        async def _stop_health_sampler() -> None:
            task = getattr(app.state, "health_sampler_task", None)
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    return app


async def _sample_commercial_self(app: FastAPI) -> None:
    """Samples THIS process's own `/health` in-process (no self-HTTP
    loopback -- this process doesn't reliably know its own bound port),
    reusing `app.state.session_factory` directly rather than duplicating
    the `GET /health` route's own database probe."""
    start = time.monotonic()
    try:
        probe_session = app.state.session_factory()
        try:
            probe_session.execute(text("SELECT 1"))
            success = True
            failure_reason = None
        finally:
            probe_session.close()
    except Exception as exc:  # noqa: BLE001 - a sampler pass must never crash the loop
        success = False
        failure_reason = f"{type(exc).__name__}: {exc}"
    latency_ms = (time.monotonic() - start) * 1000.0

    record_session = app.state.session_factory()
    try:
        record_health_sample(
            record_session,
            service_name="commercial",
            success=success,
            latency_ms=latency_ms if success else None,
            failure_reason=failure_reason,
        )
        record_session.commit()
    finally:
        record_session.close()


async def _sample_signal_copier(app: FastAPI) -> None:
    """Samples signal-copier's own separate `/health` over real HTTP --
    the one cross-service check this deployment can genuinely make
    without new credentials, per this track's own scope. A no-op (not a
    fabricated failure sample) when `SIGNAL_COPIER_BASE_URL` is unset:
    an unconfigured deployment has no real target to report a failure
    against."""
    if not config.SIGNAL_COPIER_BASE_URL:
        return

    start = time.monotonic()
    success = False
    failure_reason: str | None = None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{config.SIGNAL_COPIER_BASE_URL.rstrip('/')}/health")
        if response.status_code == 200 and response.json().get("status") == "ok":
            success = True
        else:
            failure_reason = f"HTTP {response.status_code}, body status={response.json().get('status') if response.headers.get('content-type', '').startswith('application/json') else '<non-json>'}"
    except Exception as exc:  # noqa: BLE001 - a sampler pass must never crash the loop
        failure_reason = f"{type(exc).__name__}: {exc}"
    latency_ms = (time.monotonic() - start) * 1000.0

    record_session = app.state.session_factory()
    try:
        record_health_sample(
            record_session,
            service_name="signal_copier",
            success=success,
            latency_ms=latency_ms if success else None,
            failure_reason=failure_reason,
        )
        record_session.commit()
    finally:
        record_session.close()


async def _health_sampler_loop(app: FastAPI) -> None:
    """Track 11's periodic background task: samples this process's own
    `/health` and (when configured) signal-copier's, every
    `config.HEALTH_SAMPLER_INTERVAL_SECONDS`, recording a real
    `ServiceHealthSample` row each pass. One failed pass never stops the
    loop -- each sampler function already swallows its own exceptions
    into a failure sample, and this loop additionally guards the pass as
    a whole so a truly unexpected error still doesn't kill the
    background task outright."""
    while True:
        try:
            await _sample_commercial_self(app)
            await _sample_signal_copier(app)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the sampler loop must never die from one bad pass
            pass
        await asyncio.sleep(config.HEALTH_SAMPLER_INTERVAL_SECONDS)


#: A real, importable module-level instance for a production ASGI server
#: (`uvicorn app.main:app`, matching signal-copier's own `app/main.py`
#: precedent) -- every test builds its own isolated instance via
#: `create_app()` directly instead (a real Postgres/relay connection at
#: import time would break plain `pytest` collection), so this one is
#: never imported by the test suite, only by a real deployment's own
#: process manager.
app = create_app()
