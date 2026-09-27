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
"""
from __future__ import annotations

import json

from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy.orm import Session

from app import config
from app.api.dependencies import get_current_scope, get_db_session
from app.db import make_engine, make_session_factory, set_tenant_scope
from app.services.auth import TenantScope
from app.services.stripe_webhook import (
    InvalidSignatureHeaderError,
    SignatureMismatchError,
    StaleTimestampError,
    record_event_if_new,
    verify_signature,
)


def create_app(database_url: str | None = None) -> FastAPI:
    app = FastAPI(title="signal-portfolio-commercial")
    engine = make_engine(database_url)
    app.state.session_factory = make_session_factory(engine)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

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

    return app
