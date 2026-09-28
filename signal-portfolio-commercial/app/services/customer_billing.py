"""CU-11 "Billing, invoices and plan changes" -- the real, tenant-scoped
read backing `/app/billing`. See dashboard_spec/screens/CU-11.md for
the full screen contract this implements a bounded slice of.

`Subscription` (app/models/billing.py) has no `user_id` at all -- it is
the TENANT's own billing account (the same "no per-price Subscription
linkage" gap app/services/publication_admin.py's own AD-10 docstring
already documented), never one row per individual retail customer.
Every CUSTOMER-role member of a tenant therefore sees the same, single
shared subscription state here -- exactly what "canonical entitlement
state" (CU-11's own purpose) means for this build's own billing model,
not a per-user fabrication this schema cannot actually support.

## What is deliberately NOT computed here

There is no `Invoice`/hosted-invoice model anywhere in this codebase,
and `app/services/stripe_webhook.py`'s own docstring is explicit: "No
Stripe SDK call and no real Stripe account exist in this environment...
Wiring a real STRIPE_WEBHOOK_SIGNING_SECRET and receiving real events
is future work gated on CARD-4 (payment processor approval)." Invoices,
hosted checkout/portal sessions, and invoice downloads are therefore
all rendered as explicit UNSUPPORTED by the template -- never a
fabricated invoice row, checkout URL, or payment amount. Pending
changes (a proration/scheduled-change preview) need the same missing
processor connection and are UNSUPPORTED for the identical reason.
Platform fee distinction has no separate fee-schedule model to read
from either; the template states this honestly rather than inventing a
number.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.billing import Subscription
from app.services.entitlement import authorizes_new_entry, authorizes_risk_reducing_management


@dataclass(frozen=True)
class CustomerBillingState:
    subscriptions: list[Subscription]

    @property
    def current(self) -> Subscription | None:
        """The most recently created subscription row, if any -- this
        build has no explicit "current" flag, so recency is the only
        real signal available (never guessed from state alone, since
        more than one terminal-state row can coexist)."""
        return self.subscriptions[0] if self.subscriptions else None

    @property
    def authorizes_new_entry(self) -> bool | None:
        return authorizes_new_entry(self.current) if self.current is not None else None

    @property
    def authorizes_risk_reducing_management(self) -> bool | None:
        return authorizes_risk_reducing_management(self.current) if self.current is not None else None


def get_own_billing_state(session: Session, *, tenant_id: str) -> CustomerBillingState:
    subscriptions = list(
        session.scalars(
            select(Subscription).where(Subscription.tenant_id == tenant_id).order_by(Subscription.created_at.desc())
        ).all()
    )
    return CustomerBillingState(subscriptions=subscriptions)
