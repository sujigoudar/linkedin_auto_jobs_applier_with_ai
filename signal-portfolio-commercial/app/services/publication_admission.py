"""The real effect boundary for admitting a PublicationIntent -- this is
what closes CP-003 "Grant expiry at effect boundary" and CP-051
"Entitlement expiration": every earlier phase built the individual
checks (rights_registry.check_rights / portfolio_rights.check_portfolio_rights
in Phase 01/this session, entitlement.authorizes_new_entry in Phase 08,
publisher_writer_claim's single-authority fencing this session) but
nothing called them together, immediately before the one real
publish-admitting action (`enqueue_intent`), until now.

"Check rights at ... product publication, subscriber delivery and
outbound financial publication ... again immediately before its own
action, not cache an earlier result" (app/services/rights_registry.py's
own docstring, quoting docs/01) -- `admit_publication_intent` is that
recheck, not a duplicate of it.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.billing import Subscription
from app.models.publication import PublicationAction, PublicationIntent
from app.models.rights import RightsUse
from app.services.entitlement import authorizes_new_entry
from app.services.portfolio_rights import check_portfolio_rights
from app.services.publication import enqueue_intent
from app.services.publisher_writer_claim import claim_writer

#: Actions that admit NEW exposure -- these are the ones entitlement
#: must gate (docs/08: "new premium entries require a valid paid-through
#: entitlement"). STOP_UPDATE/TARGET_UPSERT/etc. manage EXISTING exposure
#: and are deliberately not included here -- see
#: app/services/entitlement.py's authorizes_risk_reducing_management,
#: which this module does not call, since safety management must never
#: be blocked by a payment problem.
_NEW_EXPOSURE_ACTIONS = frozenset({PublicationAction.OPEN, PublicationAction.ADD})


class RightsDeniedAtAdmissionError(Exception):
    pass


class EntitlementDeniedAtAdmissionError(Exception):
    pass


def admit_publication_intent(
    session: Session,
    intent: PublicationIntent,
    *,
    portfolio_version_id: str,
    writer_identity: str,
    subscription: Subscription | None = None,
    rights_use: RightsUse = RightsUse.AUTOMATED_PUBLICATION,
    jurisdiction: str,
    asset: str,
    at: datetime | None = None,
) -> PublicationIntent:
    """Re-checks portfolio rights and (for new-exposure actions) customer
    entitlement against `portfolio_version_id`/`subscription` AS THEY ARE
    RIGHT NOW, claims sole writer authority for the target
    (channel, external_strategy_id), and only then enqueues `intent`.
    Raises rather than enqueuing on any denial -- there is no fallback
    that queues anyway and hopes a later step catches it."""
    rights_result = check_portfolio_rights(
        session,
        portfolio_version_id=portfolio_version_id,
        use=rights_use,
        channel=intent.channel,
        jurisdiction=jurisdiction,
        asset=asset,
        at=at,
    )
    if not rights_result.allowed:
        raise RightsDeniedAtAdmissionError(
            f"portfolio version {portfolio_version_id!r} is not rights-eligible for channel "
            f"{intent.channel!r} right now: {rights_result.reason} (failing_sleeve_id="
            f"{rights_result.failing_sleeve_id!r})"
        )

    if intent.action in _NEW_EXPOSURE_ACTIONS:
        if subscription is None or not authorizes_new_entry(subscription):
            raise EntitlementDeniedAtAdmissionError(
                f"{intent.action.value} requires an entitled subscription in a new-entry-authorized state; "
                f"got {subscription.state.value if subscription else None}"
            )

    claim_writer(session, channel=intent.channel, external_strategy_id=intent.external_strategy_id, writer_identity=writer_identity)

    return enqueue_intent(session, intent)
