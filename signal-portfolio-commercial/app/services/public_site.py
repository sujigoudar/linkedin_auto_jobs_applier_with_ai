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


@dataclass(frozen=True)
class ChannelCompatibilityItem:
    channel: str
    supported_service: str
    limitation: str


def get_channel_compatibility() -> list[ChannelCompatibilityItem]:
    """PU-08 "Help and compatibility guide" -- Compatibility directory.
    Grounded in the actual state of each adapter module
    (app/services/collective2_publisher.py, etoro_adapter.py,
    copyfactory_close_only.py), not a marketing claim: request-building
    is real and tested; live transmission is blocked in every case by
    the same fact -- no real platform credentials exist in this
    environment. "Only verified capabilities appear as available; no
    promises of universal broker coverage" (PU-08's own acceptance
    text)."""
    return [
        ChannelCompatibilityItem(
            channel="Collective2",
            supported_service="Request building/validation (API4 order envelope)",
            limitation="Not transmitted -- no Collective2 sandbox or real credentials exist in this environment.",
        ),
        ChannelCompatibilityItem(
            channel="eToro",
            supported_service="Request building (Builders API trade request shape), demo transport only",
            limitation="No application is registered with eToro; the code path refuses any non-demo account mode outright.",
        ),
        ChannelCompatibilityItem(
            channel="MetaApi CopyFactory (close-only)",
            supported_service="Close-only mode classification (by-position/by-symbol/immediately)",
            limitation="Classification only -- this module never calls CopyFactory itself.",
        ),
    ]


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
