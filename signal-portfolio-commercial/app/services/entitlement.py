"""Entitlement/safety separation, per spec/docs/08_products_billing_and_entitlements.md's
"Entitlement and safety separation" section -- the single most important
rule in this whole phase: "A payment outage does not stop existing
position management or revoke the broker's protective orders."

This module answers exactly two, deliberately DIFFERENT questions, and
never lets one substitute for the other:

- `authorizes_new_entry`: may a NEW premium action be admitted for this
  subscription state? Only a currently-paid-through state says yes.
- `authorizes_risk_reducing_management`: may EXISTING exposure still be
  protected/managed (stop updates, planned exits, safety continuity)?
  This is almost always yes, regardless of payment status -- billing
  trouble is never itself a reason to stop protecting a position that is
  already open, since it is "not merely current premium subscription"
  that risk-reducing management depends on (the original mandate does).

Structurally enforced, not just documented: this module has no import of
anything that could close, flatten or resize a position (no
app.services.publication, no app.services.collective2_publisher, no
app.services.etoro_adapter) -- it is physically incapable of issuing a
close command itself, so "payment failure triggers a flatten" cannot be
implemented as a bug in THIS module; that would require a caller to
wire a close action to `authorizes_new_entry` returning False, which is
exactly the wrong function for that caller to have used.
"""
from __future__ import annotations

from app.models.billing import Subscription, SubscriptionState

#: Only these states reflect a currently paid-through (or authorized
#: trial) window -- CANCEL_AT_PERIOD_END is included because the
#: customer explicitly remains paid through `current_period_end`; new
#: entries stop only once the period actually ends (ENDED), never the
#: moment cancellation is requested.
_NEW_ENTRY_AUTHORIZED_STATES: frozenset[SubscriptionState] = frozenset(
    {SubscriptionState.ACTIVE_PAID, SubscriptionState.CANCEL_AT_PERIOD_END, SubscriptionState.TRIAL_AUTHORIZED}
)

#: States that recognize this subscription at all for the purpose of
#: management continuity -- everything except a state that has never
#: represented a real customer relationship. DISPUTED/MANUAL_REVIEW and
#: PAST_DUE/SUSPENDED_NEW_ENTRIES all still authorize management: a
#: chargeback dispute or a failed card is a billing event, not a mandate
#: revocation.
_MANAGEMENT_AUTHORIZED_STATES: frozenset[SubscriptionState] = frozenset(SubscriptionState) - {
    SubscriptionState.PENDING_PAYMENT
}


def authorizes_new_entry(subscription: Subscription) -> bool:
    """Whether `subscription`'s current state permits admitting a NEW
    premium action (e.g. a new copy entry). Never call this to decide
    whether an EXISTING position may still be managed -- use
    `authorizes_risk_reducing_management` for that; the two are
    deliberately not the same question, per this module's docstring."""
    return subscription.state in _NEW_ENTRY_AUTHORIZED_STATES


def authorizes_risk_reducing_management(subscription: Subscription) -> bool:
    """Whether existing exposure under `subscription` may still be
    protected/managed (stop updates, planned exits). True for every
    state except PENDING_PAYMENT, which has never represented an
    admitted customer relationship in the first place -- there is no
    existing exposure to protect if a subscription never left
    PENDING_PAYMENT. A payment outage on an already-active subscription
    (PAST_DUE, SUSPENDED_NEW_ENTRIES, DISPUTED, MANUAL_REVIEW) never
    revokes this."""
    return subscription.state in _MANAGEMENT_AUTHORIZED_STATES
