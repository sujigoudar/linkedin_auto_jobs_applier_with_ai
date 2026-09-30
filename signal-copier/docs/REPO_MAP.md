# Repository Map

signal-copier lives in the `signal-copier/` directory of a monorepo
(alongside `signal-portfolio-commercial/` and the shared
`signal_platform_contracts/` package — see
docs/architecture/SYSTEM_CONTEXT.md). Everything below is relative to
`signal-copier/`.

```
signal-copier/
├── app/                     # the application
│   ├── main.py              # FastAPI app, route registration, lifespan, dashboard
│   ├── engine.py            # SignalCopierEngine — the core pipeline
│   ├── models.py            # Signal, OrderResult, DestinationAccount, and every shared enum
│   ├── db.py                # SignalStore (SQLite), schema, Alembic stamping
│   ├── routing.py           # which sources feed which accounts
│   ├── risk.py               # position sizing / symbol mapping
│   ├── providers.py          # account -> provider -> analyst overrides
│   ├── capital_allocator.py  # notional / risk-basis admission gates
│   ├── command_ledger.py     # pre-effect durable command intent
│   ├── writer_lease.py       # cross-process/cross-host fencing token
│   ├── qualification.py      # route qualification ladder
│   ├── reconciliation.py     # OrderReconciler background loop
│   ├── pricing.py            # PriceMonitor background loop
│   ├── provider_scout.py     # ProviderScout background loop
│   ├── provider_value.py     # provider P&L attribution (episode-based; FIFO-lot kept deprecated)
│   ├── trade_episode.py      # TR-EPISODE-01: one authoritative episode per position lifecycle
│   ├── economics.py          # confirmed-fill-replay account economics
│   ├── account_economics_v2.py # extended P&L view (NAV/unrealized/TWR/slippage), built alongside economics.py
│   ├── execution_quality.py  # per-stage execution latency
│   ├── equity_history.py     # periodic equity snapshots
│   ├── statistics.py         # rolling stats / drawdown / correlation
│   ├── export_events.py      # signal_platform_contracts export envelopes
│   ├── relay_worker.py       # signs and delivers the export outbox
│   ├── relay_scheduler.py    # schedules relay_worker runs
│   ├── promote_cli.py        # `python -m app.promote_cli` — writer-lease promotion
│   ├── config.py             # typed env-var settings
│   ├── config_admin.py       # one-time YAML -> database config seeding
│   ├── auth.py                # owner password/session/CSRF
│   ├── rate_limit.py          # per-IP ingress rate limiting
│   ├── logging_config.py      # structlog setup + secret redaction
│   ├── metrics.py             # Prometheus metrics rendering
│   ├── errors.py              # SignalValidationError
│   ├── lifecycle/             # managed-lifecycle (protect-first) subsystem
│   │   ├── models.py          # PositionPlan, Target, TrailingPolicy, PendingEntry/Exit
│   │   ├── close_arbiter.py   # CloseArbiter — serialized exit ownership
│   │   └── manager.py         # PositionLifecycleManager
│   ├── sources/                # one adapter per signal source
│   │   ├── base.py             # SourceAdapter interface
│   │   ├── webhook.py, telegram.py, discord.py, slack.py, twitter.py,
│   │   │   sms_twilio.py, whatsapp.py, mt4_mt5.py, ninjatrader.py, rithmic.py
│   │   └── text_parser.py      # shared free-text signal parser
│   ├── brokers/                 # one adapter per execution destination
│   │   ├── base.py              # BrokerAdapter interface, capability introspection
│   │   └── paper.py, alpaca.py, ccxt_broker.py, ibkr.py, mt4_mt5.py,
│   │       signalstack.py, ninjatrader.py, rithmic.py, oanda.py,
│   │       tradovate.py, tradestation.py, tastytrade.py, schwab.py,
│   │       robinhood.py
│   ├── backtest/                # POST /backtest and the fit simulator
│   │   ├── models.py, simulator.py, replay.py, cost_stress.py, fit_simulator.py
│   ├── context/                  # read-only market/economic data (not in the trading path)
│   │   ├── sec_edgar.py, fred.py, fx.py
│   ├── services/
│   │   └── catalog_fit_sim_auth.py  # signed-token auth for the cross-service fit-sim route
│   └── static/
│       └── dashboard.html       # single-page dashboard, vanilla JS, no build step
│
├── tests/                    # pytest suite — one file per feature/bugfix area,
│                              # named after the adoption-plan/audit item it covers
│                              # (e.g. test_p0_5_close_reconciliation.py, test_e03_capital_exposure_gate.py)
├── alembic/
│   ├── env.py, script.py.mako
│   └── versions/              # one file per schema migration — the ONLY way to
│                               # change the schema going forward (app/db.py's
│                               # _COLUMN_MIGRATIONS list is frozen)
├── config/                    # one-time YAML import path; database is authoritative once seeded
│   ├── accounts.example.yaml, providers.example.yaml, routing.example.yaml
├── deploy/                    # multi-site deployment design + IaC (draft, not yet applied)
│   ├── README.md, RUNBOOK.md
│   ├── cloud-init/, cloudflare-heartbeat/, litestream/, systemd/, terraform/
├── docs/                      # this documentation set
│   ├── manifest.yaml           # index of every doc category and when to read it
│   ├── FAILOVER.md             # writer-lease fencing design detail
│   ├── architecture/           # this foundation layer
│   ├── database/, design/, security/, standards/, testing/, operations/,
│   │   agents/, state/, adr/, requirements/, integrations/, observability/,
│   │   process/                # built by parallel documentation efforts
│   ├── REPO_MAP.md, GLOSSARY.md
├── ninjascript/
│   └── SignalCopierAutoJournal.cs   # NinjaTrader-side signal-source indicator
├── .agent/
│   └── context-map.yaml        # task-category -> docs-to-read index for agents
├── CLAUDE.md                   # slim operating contract
├── PROJECT_STATUS.yaml         # machine-readable build/lint/typecheck/test snapshot
├── README.md                   # the primary, most detailed, most current narrative doc
├── requirements.txt
├── pyproject.toml              # ruff + mypy scope/config
├── pytest.ini
├── alembic.ini
├── Dockerfile, docker-compose.yml
└── .env.example
```

Everything not listed above (e.g. `app/__init__.py`) is a standard
package marker or is covered by docs/architecture/COMPONENTS.md.
