"""INT-033 "Shared account cannot gain a second writer" enforcement -- see
app/models/real_account_route.py's own docstring for the exact spec
language this implements.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.real_account_route import ExclusiveOwnershipPlan, RealAccountRoute


class SecondWriterRejectedError(Exception):
    pass


def register_real_account_route(
    session: Session,
    *,
    tenant_id: str,
    broker: str,
    account_reference: str,
    channel: str,
    external_strategy_id: str,
    writer_identity: str,
) -> RealAccountRoute:
    """Claim `(broker, account_reference)` -- the real account itself,
    never just one of its aliases -- for the effect route named by
    `(channel, external_strategy_id)`. Idempotent when the SAME route
    re-registers what it already holds (a restart/retry is not a
    conflict, and re-registration always refreshes `writer_identity`/
    `tenant_id` in place, matching a legitimate redeploy of the same
    route under new process/tenant metadata).

    A DIFFERENT route attempting to claim an already-claimed real
    account is rejected -- INT-033's own "Alias treated as independent
    capital/authority" is the exact outcome this refusal prevents --
    UNLESS a qualified `ExclusiveOwnershipPlan` for this exact
    `(broker, account_reference)` names exactly this `(channel,
    external_strategy_id)` as its own approved successor, in which case
    ownership transfers to it. No other path ever lets a second route
    take over."""
    existing = session.get(RealAccountRoute, (broker, account_reference))
    if existing is None:
        route = RealAccountRoute(
            broker=broker, account_reference=account_reference, channel=channel,
            external_strategy_id=external_strategy_id, writer_identity=writer_identity, tenant_id=tenant_id,
        )
        session.add(route)
        session.flush()
        return route

    if (existing.channel, existing.external_strategy_id) == (channel, external_strategy_id):
        existing.writer_identity = writer_identity
        existing.tenant_id = tenant_id
        session.flush()
        return existing

    plan = session.get(ExclusiveOwnershipPlan, (broker, account_reference))
    plan_approves_this_route = plan is not None and (
        plan.approved_channel, plan.approved_external_strategy_id
    ) == (channel, external_strategy_id)
    if not plan_approves_this_route:
        raise SecondWriterRejectedError(
            f"real account {broker!r}/{account_reference!r} is already claimed by route "
            f"{existing.channel!r}/{existing.external_strategy_id!r} -- a second, different effect route "
            f"for the SAME real account is rejected until an explicit exclusive ownership plan qualifies "
            f"{channel!r}/{external_strategy_id!r} to take it over"
        )

    existing.channel = channel
    existing.external_strategy_id = external_strategy_id
    existing.writer_identity = writer_identity
    existing.tenant_id = tenant_id
    session.flush()
    return existing


def qualify_exclusive_ownership_plan(
    session: Session,
    *,
    broker: str,
    account_reference: str,
    approved_channel: str,
    approved_external_strategy_id: str,
    qualified_by: str,
) -> ExclusiveOwnershipPlan:
    """An explicit, owner-only decision that a NAMED successor route may
    take over an already-claimed real account -- the only path INT-033
    permits for a second, different effect route to ever gain write
    authority over the same real account. Never created implicitly by
    `register_real_account_route` itself -- a plan is a deliberate act,
    not a side effect of a route merely attempting to register."""
    plan = session.get(ExclusiveOwnershipPlan, (broker, account_reference))
    if plan is None:
        plan = ExclusiveOwnershipPlan(
            broker=broker, account_reference=account_reference, approved_channel=approved_channel,
            approved_external_strategy_id=approved_external_strategy_id, qualified_by=qualified_by,
        )
        session.add(plan)
    else:
        plan.approved_channel = approved_channel
        plan.approved_external_strategy_id = approved_external_strategy_id
        plan.qualified_by = qualified_by
    session.flush()
    return plan
