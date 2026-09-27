"""PU-01 "Public home" -- the real service-status checklist and
published-product summary backing the anonymous landing page. See
dashboard_spec/screens/PU-01.md for the full screen contract this
implements a bounded slice of.

"Service status" (AD-01-style checklist, per PU-01-P05's own contract:
"enumerates independently evaluated conditions, actual outcome, reason
code") is computed from real configuration state, never a decorative
"all systems operational" banner: the environment tag and whether
billing is genuinely connected are both read from app.config, exactly
as configured -- a placeholder Stripe secret is honestly reported as
NOT_CONFIGURED, never silently upgraded to look connected.
"""
from __future__ import annotations

from dataclasses import dataclass

from app import config

_PLACEHOLDER_STRIPE_SECRET = "whsec_LOCAL_SIM_not_a_real_stripe_secret"


@dataclass(frozen=True)
class ServiceStatusItem:
    name: str
    status: str
    reason: str


def get_service_status() -> list[ServiceStatusItem]:
    billing_configured = config.STRIPE_WEBHOOK_SECRET != _PLACEHOLDER_STRIPE_SECRET
    return [
        ServiceStatusItem(
            name="Environment",
            status=config.ENVIRONMENT,
            reason="Every trade and subscription on this deployment is paper-traded/simulated unless this reads COMMERCIAL_LIVE.",
        ),
        ServiceStatusItem(
            name="Billing (Stripe)",
            status="CONFIGURED" if billing_configured else "NOT_CONFIGURED",
            reason=(
                "A real Stripe webhook secret has been set."
                if billing_configured
                else "No real Stripe account has been connected yet -- billing runs against a local placeholder secret only."
            ),
        ),
    ]
