"""eToro Builders API adapter: maps a QUEUED PublicationIntent onto an
eToro trade request shape, per spec/docs/06_platform_adapters.md's
"eToro separately" section. Builds (never sends) a request -- no
application is registered with eToro in this environment, and "No live
financial publisher/broker/payment authority during build" holds
regardless.

Two rules from that section are structural here, not just documented:

- "Implement DEMO transport... then real read-only verification. No
  code may select a real endpoint merely because demo is unavailable."
  `build_trade_request` refuses any `account_mode` other than `"demo"`
  outright -- there is no fallback branch that would ever choose a real
  endpoint, so a caller cannot accidentally "fail over" into one.
- "Close-by-position-ID is not the same as selling a generic ticker
  amount; preserve position identity and platform units." REDUCE and
  CLOSE both require an explicit `position_id`; there is no code path
  that synthesizes a close from just an instrument symbol and a
  quantity.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.models.publication import PublicationAction, PublicationIntent, PublicationSide, QuantityBasis


class UnsupportedAccountModeError(Exception):
    pass


class MissingPositionIdError(Exception):
    pass


class UnsupportedActionError(Exception):
    pass


class UnsupportedQuantityError(Exception):
    pass


_OPEN_ACTIONS = frozenset({PublicationAction.OPEN, PublicationAction.ADD})
_POSITION_SCOPED_ACTIONS = frozenset({PublicationAction.REDUCE, PublicationAction.CLOSE})


def build_trade_request(
    intent: PublicationIntent,
    *,
    instrument_symbol: str,
    account_mode: str = "demo",
    position_id: str | None = None,
) -> dict:
    if account_mode != "demo":
        raise UnsupportedAccountModeError(
            f"account_mode {account_mode!r} is not supported -- this adapter only ever targets eToro's "
            "DEMO transport in this build; demo being unavailable is never a reason to select real"
        )

    if intent.action in _POSITION_SCOPED_ACTIONS:
        if not position_id:
            raise MissingPositionIdError(
                f"{intent.action.value} requires an explicit position_id -- selling a generic amount of "
                f"{instrument_symbol!r} is not the same operation as closing/reducing a specific position"
            )
        return _build_position_scoped_request(intent, position_id=position_id)

    if intent.action in _OPEN_ACTIONS:
        return _build_open_request(intent, instrument_symbol=instrument_symbol)

    raise UnsupportedActionError(f"action {intent.action.value!r} has no eToro request mapping in this adapter yet")


def _resolved_units(intent: PublicationIntent) -> Decimal:
    if intent.quantity_basis != QuantityBasis.UNITS:
        raise UnsupportedQuantityError(
            f"quantity_basis {intent.quantity_basis.value!r} has no eToro amount mapping in this adapter"
        )
    if intent.quantity is None:
        raise UnsupportedQuantityError(f"{intent.action.value} requires a quantity, got None")
    try:
        return Decimal(intent.quantity)
    except InvalidOperation as exc:
        raise UnsupportedQuantityError(f"quantity {intent.quantity!r} is not a valid decimal string") from exc


def _build_open_request(intent: PublicationIntent, *, instrument_symbol: str) -> dict:
    amount = _resolved_units(intent)
    if amount <= 0:
        raise UnsupportedQuantityError(f"an OPEN/ADD amount must be positive, got {amount}")
    direction = "SELL" if intent.side is PublicationSide.SELL else "BUY"
    return {
        "instrument": instrument_symbol,
        "direction": direction,
        "amount": str(amount),
    }


def _build_position_scoped_request(intent: PublicationIntent, *, position_id: str) -> dict:
    request: dict[str, str | bool] = {"positionId": position_id}
    if intent.action is PublicationAction.CLOSE:
        request["fullClose"] = True
        return request

    # REDUCE: a partial close needs the amount being closed, distinct from
    # the position's own total size (which this adapter never guesses).
    amount = _resolved_units(intent)
    if amount <= 0:
        raise UnsupportedQuantityError(f"a REDUCE amount must be positive, got {amount}")
    request["fullClose"] = False
    request["amount"] = str(amount)
    return request
