# Data Flows

## End-to-end: a plain-account entry signal

```mermaid
sequenceDiagram
    participant Src as Source adapter
    participant Eng as SignalCopierEngine
    participant Route as RoutingConfig
    participant Prov as ProviderRegistry
    participant Risk as risk.py
    participant Cap as CapitalAllocator
    participant Ledger as command_ledger
    participant Broker as BrokerAdapter
    participant Store as SignalStore (SQLite)

    Src->>Eng: handle_signal(Signal)
    Eng->>Store: save_signal(signal)
    Eng->>Route: destinations_for(source, symbol)
    Route-->>Eng: [DestinationAccount, ...]
    loop per destination account
        Eng->>Prov: resolve overrides (account, provider, analyst)
        Prov-->>Eng: effective multiplier/fixed_quantity/managed_lifecycle/enabled
        alt account or provider or analyst disabled
            Eng-->>Eng: skip this destination
        end
        Eng->>Eng: broker.can_trade_asset_class(signal.asset_class)?
        Eng->>Risk: size_for_account / symbol_for_account
        Risk-->>Eng: quantity, mapped symbol
        Eng->>Cap: admission check (notional / risk-basis / owner-wide)
        Cap-->>Eng: admit or reject
        alt has stop_loss/take_profit and not broker.supports_native_bracket
            Eng-->>Eng: REJECTED - refuse to submit unprotected entry
        end
        Eng->>Ledger: open_command_ledger_entry(...) [pre-effect, committed]
        Eng->>Broker: place_order(signal, account, quantity, symbol)
        Broker-->>Eng: OrderResult (FILLED / PENDING / REJECTED / ERROR)
        Eng->>Ledger: classify_order_result -> mark_command_ledger_outcome
        Eng->>Store: save_order_result(applied_quantity=...)
        Eng->>Store: update tracked position (only from confirmed fill data)
    end
```

## End-to-end: a managed-lifecycle entry

```mermaid
sequenceDiagram
    participant Eng as SignalCopierEngine
    participant Lcm as PositionLifecycleManager
    participant Arb as CloseArbiter
    participant Broker as BrokerAdapter
    participant Store as SignalStore

    Eng->>Eng: build PositionPlan (stop_loss -> initial_stop, take_profit -> Target)
    Eng->>Eng: validate_plan (needs place_protective_stop or native bracket)
    Eng->>Lcm: submit entry
    Lcm->>Ledger: open_command_ledger_entry (ENTRY)
    Lcm->>Broker: place_order (entry, protection stripped)
    Broker-->>Lcm: OrderResult
    alt synchronous FILLED
        Lcm->>Broker: place_protective_stop(filled_quantity)
        Lcm->>Store: record_fill + lifecycle_state checkpoint (one transaction)
    else PENDING (async confirmation, e.g. Alpaca/IBKR)
        Lcm->>Store: PendingEntry recorded
        Note over Lcm: reconciliation polls get_order_status later
    end
```

## Continuous monitoring (background)

```mermaid
sequenceDiagram
    participant PM as PriceMonitor (15s poll)
    participant Broker as BrokerAdapter (ccxt/Alpaca/IBKR)
    participant Lcm as PositionLifecycleManager
    participant Arb as CloseArbiter

    loop every position with managed_lifecycle
        PM->>Broker: get_last_price(symbol)
        Broker-->>PM: price or None
        PM->>Lcm: on_price_update(account, symbol, price)
        Lcm->>Lcm: evaluate targets / trailing policy
        alt target/trailing fires
            Lcm->>Arb: reserve_quantity (available_to_sell check)
            Lcm->>Broker: submit exit order
            Broker-->>Lcm: OrderResult
            Lcm->>Lcm: resize/cancel-replace protective stop to true remainder
        end
    end
```

```mermaid
sequenceDiagram
    participant OR as OrderReconciler (30s poll)
    participant Broker as BrokerAdapter (Alpaca/IBKR - has get_order_status)
    participant Lcm as PositionLifecycleManager
    participant Store as SignalStore

    loop every PENDING order
        OR->>Broker: get_order_status(broker_order_id)
        Broker-->>OR: OrderResult or None
        alt order belongs to an active lifecycle's tracked pending entry/exit
            OR->>Lcm: resolve_pending_entry / resolve_pending_exit
            Lcm->>Store: apply real delta exactly once (single execution-application owner)
        else generic plain-account order
            OR->>Store: correct tracked position (reverse rejection, true up partial fill)
        end
    end
```

## Close signal resolution (plain account)

1. Look up this service's own tracked net position for
   `(account_id, mapped_symbol)` — `SignalStore.get_position`, its own
   record, not a live broker read.
2. Flat? `REJECTED` — "no open position to close" — no broker call.
3. **P0-5 reconciliation gate** — one of:
   - a fresh `BrokerAdapter.get_broker_position` readback matching the
     tracked quantity within tolerance, or
   - `DestinationAccount.exclusive_writer_qualified = True` (an explicit,
     off-by-default operator assertion).
   Neither holding: `REJECTED`, never proceeds on an unproven projection.
4. Resolve to the opposing BUY/SELL at the full open quantity, call
   `place_order`. Serialized per `(account_id, symbol)` so two concurrent
   close attempts can't both oversell.

On a `managed_lifecycle` account, the equivalent serialization and
reconciliation is `CloseArbiter`'s job — see
docs/architecture/ARCHITECTURE.md's "Managed lifecycle" section.

## Export to the commercial platform

```mermaid
sequenceDiagram
    participant Eng as SignalCopierEngine / Lcm
    participant Exp as export_events.py
    participant Store as SignalStore (export_events table)
    participant Sched as relay_scheduler.py
    participant Worker as relay_worker.py
    participant SPC as signal-portfolio-commercial

    Eng->>Exp: build_execution_applied_envelope(...)
    Exp->>Store: append to export_events outbox
    loop scheduled
        Sched->>Worker: run_once(pending export_events)
        Worker->>Worker: sign_relay_payload (HMAC)
        Worker->>SPC: POST signed batch
        SPC-->>Worker: ack
        Worker->>Store: mark delivered
```
    end
