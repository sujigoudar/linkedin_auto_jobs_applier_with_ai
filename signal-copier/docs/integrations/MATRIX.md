# Feature × broker-adapter capability matrix

Pulled directly from each adapter's actual override of
`app/brokers/base.py`'s `BrokerAdapter` — capability here means "this
subclass overrides the base no-op with a real implementation," computed
via `BrokerAdapter`'s own `has_*_capability` properties (`type(self).method
is not BrokerAdapter.method`), never a separately maintained flag nobody
updates when a method changes. A blank cell means the base class's no-op
(returns `None`/`False`) is in effect — "not supported," the honest
default, never assumed to be "supports everything."

| Broker | Native bracket order | Protective stop (standalone) | Cancel order | Replace stop qty | Position readback | Order-status readback | Balance readback | Asset classes |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|---|
| **Alpaca** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | Equity |
| **ccxt** | ✅ | ✅ | ✅ | | ✅ | | | Crypto |
| **IBKR** | ✅ | | | | | ✅ | | Equity |
| **MT4/MT5** (local + MetaApi) | ✅ (sl/tp on the same order call) | | | | | | | Undeclared* |
| **OANDA** | | | | | | ✅ | | Forex |
| **Paper** (in-memory) | | ✅ | ✅ | ✅ | ✅ | | ✅ | Undeclared* |
| **Robinhood** | | | | | | ✅ | | Equity |
| **Schwab** | | | | | | ✅ | | Equity |
| **Tastytrade** | | | | | | ✅ | | Equity, Option |
| **TradeStation** | | | | | | ✅ | | Equity, Option, Future |
| **Tradovate** | | | | | | ✅ | | Future |
| **SignalStack** | | | | | | | | Undeclared* |
| **NinjaTrader** | | | | | | | | Undeclared* |
| **Rithmic** | | | | | | | | Undeclared* |

\* `supported_asset_classes = None` (the base class default) means "not
declared," **not** "supports everything" — `app/brokers/base.py`'s
`can_trade_asset_class` treats `None` as unrestricted rather than a false
restriction, since declaring a restriction that isn't actually enforced by
the adapter's own code would silently break a real, working route. Treat
an undeclared adapter's asset-class support as unverified, not as "all."

## What each column actually means

- **Native bracket order**: this broker can submit entry + stop +
  take-profit as one atomic bracket/OCO order (`supports_native_bracket`).
  Verified per each broker module's own docstring — not assumed from the
  broker's general reputation.
- **Protective stop (standalone)**: `place_protective_stop` is
  implemented — a standalone stop order sized to a quantity, submitted
  *after* a fill is known, independent of any bracket. This is what
  actually backs `can_protect_a_managed_position()`
  (`app/brokers/base.py`) — **a broker whose only claimed capability is
  native bracket cannot protect a managed-lifecycle position through this
  path at all**, regardless of `supports_native_bracket`, because managed
  entries deliberately strip `stop_loss`/`take_profit` before calling
  `place_order` (protection is meant to be owned entirely by
  `PositionLifecycleManager`'s own logic, never embedded in the broker
  order). Only Alpaca, ccxt, and the paper broker can protect a
  managed-lifecycle position at all, today.
- **Cancel order**: `cancel_order` implemented — can cancel a previously
  placed order (e.g. before replacing a stop).
- **Replace stop qty**: `replace_stop_quantity` implemented — can resize
  an existing stop order in place, without a separate cancel-then-resubmit
  round trip. Only Alpaca and the paper broker support this; every other
  adapter that needs to resize a stop falls back to cancel-then-resubmit.
- **Position readback**: `get_broker_position` implemented — can query the
  broker's own record of current position size for a symbol.
- **Order-status readback**: `get_order_status` implemented — can re-check
  a previously PENDING order's real status asynchronously. Used by
  `app/reconciliation.py`'s `OrderReconciler`.
- **Balance readback**: `get_account_balance` implemented — can query
  real, live cash/equity/buying-power/margin directly from the broker.
  **This is the specific capability `app/promote_cli.py`'s
  `--confirm-identity` step depends on**: promotion refuses if any
  configured account's broker adapter has real balance-readback capability
  but the check itself fails or returns nothing meaningful.

## Feedback-capability floor (`has_account_order_position_feedback`)

A broker with **none** of order-status readback, position readback, or
balance readback has no real channel to verify *anything* back about an
account, order, or position after submission — this is the genuine,
structural floor `app/qualification.py`'s `account_entitled` rung and
everything above it depend on. SignalStack is the concrete case this
property was written for: it only confirms SignalStack itself received the
webhook, never a fill, position, or balance from whatever it routed to
downstream. Any adapter with a blank row across all three of those columns
above is capped the same way, structurally, not by operator discipline.
