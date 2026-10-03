# Phase 2: Infrastructure & External Accounts Checklist

**Purpose**: Venue-specific integration testing and production calibration  
**Estimated Hours**: 50-60 hours of hands-on testing  
**Status**: Ready to begin once accounts are provisioned

---

## Broker Accounts Required (9 Total)

### 🔴 CRITICAL (3 accounts - must have for production)

#### 1. **Interactive Brokers (IBKR)**
- **What**: Live or paper trading account
- **Required for**: 
  - Account code wiring (C-18): `IB_ACCOUNT_CODE` environment variable
  - Account status endpoint (C-14): Real-time account status API calls
  - Bracket order validation: Native stop/target order handling
- **Cost**: Free paper trading account available (no real money)
- **Setup Time**: 1-2 hours
- **Test Coverage**: 15-20 hours
- **Files to Update**:
  - `app/brokers/ibkr.py`: Account code configuration
  - Tests: `tests/test_wp49_ibkr_status_and_account_code.py`
- **Validation**: Account code must appear in order metadata; status endpoint must return real balance

#### 2. **Alpaca**
- **What**: Paper trading account (free tier sufficient)
- **Required for**:
  - Buying power admission check (B-07): Real `get_buying_power()` calls
  - Route qualification (B-13): Account qualification gate verification
  - Partial fill handling: Real order execution with fractional fills
- **Cost**: FREE (paper trading, no real money)
- **Account Type**: Paper trading tier
- **Setup Time**: 30 minutes
- **Test Coverage**: 12-15 hours
- **Files to Update**:
  - `app/brokers/alpaca.py`: Buying power gate
  - Tests: `tests/test_alloc09_loss_limit_fails_closed.py` (uses Alpaca)
- **Validation**: Paper account orders execute with realistic fills; balance updates reflect trades

#### 3. **CCXT Exchange** (Choose 1-2)
- **Options**: Binance, Kraken, Coinbase, Bybit (all have free tier sandboxes)
- **What**: Testnet/sandbox account (no real money needed)
- **Required for**:
  - Multi-exchange status readback (C-15): Real exchange status API
  - Rebalance tolerance detection (C-16): Exchange-specific position reconciliation
  - Crypto trading workflow: Full entry/exit cycle on real exchange infrastructure
- **Recommended**: **Binance Testnet** (most complete sandbox)
- **Cost**: FREE (testnet only, no deposits)
- **Setup Time**: 45 minutes per exchange
- **Test Coverage**: 20 hours
- **Files to Update**:
  - `app/brokers/ccxt.py`: Exchange-specific status handling
  - Tests: `tests/test_alloc02_cross_process_claim.py` (multi-exchange)
- **Validation**: Testnet orders execute; position reconciliation works across exchanges

---

### 🟡 HIGHLY RECOMMENDED (3 accounts - venue specific)

#### 4. **MT5 (MetaTrader 5)**
- **What**: Demo account (free, no real money)
- **Required for**:
  - Bracket order handling: Native MT5 stop/target orders
  - Partial fill scenarios: MT5-specific fill behavior
- **Cost**: FREE (demo account)
- **Broker**: Use MetaQuotes demo or any MetaTrader 5 broker (Pepperstone, etc.)
- **Setup Time**: 1 hour
- **Test Coverage**: 10-12 hours
- **Files to Update**:
  - `app/brokers/mt5.py`: Bracket handling
  - Tests: `tests/test_alloc08_managed_and_properties.py` (MT5 specific)
- **Validation**: Orders placed with bracket legs; fills propagate correctly

#### 5. **Tradovate** (Futures)
- **What**: Demo account (free, no real money)
- **Required for**:
  - Futures trading workflow: Contract multiplier handling
  - Partial fill execution: Futures-specific fill quantities
- **Cost**: FREE (demo account)
- **Setup Time**: 1 hour
- **Test Coverage**: 8-10 hours
- **Files to Update**:
  - `app/brokers/tradovate.py`: Futures-specific logic
  - Tests: `tests/test_wp15_contract_multipliers.py` (Tradovate)
- **Validation**: ES/NQ futures orders execute with correct multipliers

#### 6. **TradeStation** (Equities/Options)
- **What**: Demo account (free, no real money)
- **Required for**:
  - Equities trading workflow: Extended hours support
  - Options support (future): Contract specifications
- **Cost**: FREE (demo account)
- **Setup Time**: 1 hour
- **Test Coverage**: 8-10 hours
- **Files to Update**:
  - `app/brokers/tradestation.py`: Equities/options
  - Tests: `tests/test_wp15_contract_multipliers.py` (TradeStation variant)
- **Validation**: Orders execute during regular hours; extended hours available

---

### 🟢 OPTIONAL (3 accounts - nice to have)

#### 7. **OANDA** (Forex)
- **What**: Paper trading account
- **Required for**: Forex trading workflow, multi-currency P&L
- **Cost**: FREE (paper/demo)
- **Setup Time**: 30 minutes
- **Test Coverage**: 5-8 hours
- **Validation**: Forex pairs execute with pip-based sizing

#### 8. **Tastytrade** (Options)
- **What**: Paper trading account
- **Required for**: Options trading, Greeks calculation (future)
- **Cost**: FREE (paper trading)
- **Setup Time**: 45 minutes
- **Test Coverage**: 5-8 hours
- **Validation**: Options orders execute with correct multiplier (100 shares)

#### 9. **Charles Schwab** (Equities)
- **What**: Paper trading account
- **Required for**: Schwab error classification (C-11), equity workflow
- **Cost**: FREE (paper trading)
- **Setup Time**: 30 minutes
- **Test Coverage**: 5-8 hours
- **Files to Update**:
  - `tests/test_wp36_adapter_declarations.py` (Schwab error handling)
- **Validation**: Schwab-specific errors properly classified

---

## Signal Source Integrations (6 Sources)

### Already Working (No New Setup)
- ✅ **Webhook** (TradingView, custom JSON)
- ✅ **RSS** (Feed URLs)

### Require One-Time OAuth Setup (5 minutes each)

#### 1. **Telegram Bot**
- **Setup**: Create Telegram bot via @BotFather
- **Get**: Bot token (e.g., `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)
- **Environment**: `TELEGRAM_BOT_TOKEN`
- **Testing**: Send test message to bot; verify it arrives in signal-copier
- **Files**: `app/sources/telegram_collector.py`

#### 2. **Discord User Token**
- **Setup**: Extract user token from Discord client
  - Open Discord DevTools (F12) → Application → Local Storage → token
  - Or use `discord.py` OAuth flow
- **Get**: User token (starts with `mfa.` for 2FA-enabled accounts)
- **Environment**: `DISCORD_USER_TOKEN`
- **Testing**: Join test channel; post test message; verify ingestion
- **Files**: `app/sources/discord_collector.py`

#### 3. **Slack User Token**
- **Setup**: Create Slack app via api.slack.com
  - OAuth scopes: `channels:read`, `groups:read`, `users:read`, `chat:read`
- **Get**: User OAuth token (starts with `xoxp-`)
- **Environment**: `SLACK_USER_TOKEN`
- **Testing**: Subscribe to channel; post test message; verify ingestion
- **Files**: `app/sources/slack_collector.py`

#### 4. **Email (Gmail/Outlook)**
- **Setup**: Gmail: Enable 2FA, generate app-specific password
  - Or Outlook: OAuth2 flow
- **Get**: Email address + app password or OAuth token
- **Environment**: `EMAIL_ADDRESS`, `EMAIL_PASSWORD`, `IMAP_SERVER`
- **Testing**: Send test email to configured address; verify parsing
- **Files**: `app/sources/email_collector.py`

#### 5. **TradingView Webhook** (Already Integrated)
- **Setup**: Use existing webhook at `/webhook/tradingview`
- **Testing**: Send test webhook payload; verify signal parsing
- **Files**: `app/sources/webhook.py`

---

## Time Estimate by Component

| Component | Estimated Hours | Complexity | Critical? |
|-----------|-----------------|-----------|-----------|
| IBKR account code (C-18) | 6 hours | High | YES |
| IBKR status endpoint (C-14) | 4 hours | Medium | YES |
| Alpaca buying power (B-07) | 5 hours | Medium | YES |
| Alpaca qualification (B-13) | 4 hours | Low | YES |
| CCXT multi-exchange (C-15/16) | 8 hours | High | YES |
| MT5 bracket handling | 6 hours | Medium | Optional |
| Tradovate futures | 5 hours | Medium | Optional |
| TradeStation equities | 4 hours | Low | Optional |
| OANDA forex | 3 hours | Low | Optional |
| Schwab error classification (C-11) | 3 hours | Low | Optional |
| Telegram integration | 2 hours | Low | Enhancement |
| Discord integration | 2 hours | Low | Enhancement |
| Slack integration | 2 hours | Low | Enhancement |
| Email parsing | 3 hours | Low | Enhancement |
| **TOTAL** | **57 hours** | - | - |

---

## Setup Priority (Recommended Order)

### Phase 2A: Critical Blocking Items (16 hours)
1. **IBKR Paper Account** (Account Code + Status) - 10 hours
2. **Alpaca Paper Account** (Buying Power + Qualification) - 9 hours
3. **CCXT Testnet** (Binance or Kraken) - 8 hours
   - **Subtotal: 27 hours** (Start here for production readiness)

### Phase 2B: Venue-Specific Testing (20 hours)
4. MT5 Demo Account - 6 hours
5. Tradovate Demo Account - 5 hours
6. TradeStation Demo Account - 4 hours
7. Schwab Paper Account - 3 hours
8. OANDA Paper Account - 2 hours
   - **Subtotal: 20 hours**

### Phase 2C: Signal Source Integrations (10 hours)
9. Telegram + Discord + Slack OAuth setup - 6 hours
10. Email IMAP/OAuth setup - 4 hours
    - **Subtotal: 10 hours**

---

## Environment Variables to Configure

```bash
# IBKR
export IB_ACCOUNT_CODE="DU123456"
export IBKR_API_PORT="7497"  # Paper trading port

# Alpaca
export ALPACA_API_KEY="PKxxxxxx"
export ALPACA_SECRET_KEY="xxxxxx"
export ALPACA_BASE_URL="https://paper-api.alpaca.markets"

# CCXT (Binance example)
export CCXT_EXCHANGE="binance"
export CCXT_API_KEY="your-testnet-key"
export CCXT_SECRET="your-testnet-secret"
export CCXT_TESTNET="true"

# MT5
export MT5_LOGIN="12345"
export MT5_PASSWORD="password"
export MT5_SERVER="MetaQuotes-Demo"

# Signal Sources
export TELEGRAM_BOT_TOKEN="123456:ABC-DEF1234..."
export DISCORD_USER_TOKEN="mfa.xxxxxxx"
export SLACK_USER_TOKEN="xoxp-xxxxx"
export EMAIL_ADDRESS="you@gmail.com"
export EMAIL_PASSWORD="app-specific-password"
```

---

## Validation Checklist

### IBKR
- [ ] Account code appears in test order metadata
- [ ] Status endpoint returns real balance
- [ ] Bracket orders execute with native stops/targets
- [ ] Partial fills reconcile correctly

### Alpaca
- [ ] Paper account orders execute with realistic fills
- [ ] Buying power gate rejects oversized orders
- [ ] Qualification check validates account type
- [ ] Partial fills update position correctly

### CCXT (Binance Testnet)
- [ ] Testnet orders execute on exchange
- [ ] Status API returns real exchange state
- [ ] Position reconciliation works cross-exchange
- [ ] Fills propagate with realistic latency

### MT5
- [ ] Demo account connects and authorizes
- [ ] Bracket orders place with native SL/TP
- [ ] Partial fills reconcile correctly
- [ ] Market orders execute

### Signal Sources
- [ ] Telegram: Bot receives message → signal parsed
- [ ] Discord: User can post → signal parsed  
- [ ] Slack: Post to channel → signal parsed
- [ ] Email: Send email → signal parsed
- [ ] RSS: Feed updates → signals parsed

---

## Cost Summary

| Account | Type | Cost |
|---------|------|------|
| IBKR | Paper | FREE |
| Alpaca | Paper | FREE |
| CCXT (Binance) | Testnet | FREE |
| MT5 | Demo | FREE |
| Tradovate | Demo | FREE |
| TradeStation | Demo | FREE |
| OANDA | Paper | FREE |
| Tastytrade | Paper | FREE |
| Schwab | Paper | FREE |
| Telegram | Bot | FREE |
| Discord | App | FREE |
| Slack | App | FREE |
| Gmail | App Password | FREE |
| **TOTAL** | | **$0 (FREE)** |

---

## Next Steps

1. **Start Phase 2A** (Critical, 16 hours)
   - Provision IBKR paper account
   - Provision Alpaca paper account
   - Provision CCXT testnet account (Binance recommended)
   - Run existing test suites against live accounts
   - Update `.env` with credentials

2. **Validate Core Venues** (20 hours)
   - Confirm broker adapters work with live data
   - Test partial fill reconciliation
   - Validate crash recovery with real orders
   - Document any venue-specific quirks

3. **Integrate Signal Sources** (10 hours)
   - Set up OAuth for Discord/Slack/Email
   - Validate signal parsing from each source
   - Test end-to-end signal flow

4. **Production Readiness Review**
   - All 9 broker adapters tested with live data
   - All signal sources validated with production routing
   - Load testing with concurrent orders
   - Deployment to production infrastructure

---

## Support Resources

- **IBKR**: https://ibkr.info/article/2482 (paper account setup)
- **Alpaca**: https://alpaca.markets/docs/ (paper trading docs)
- **CCXT**: https://docs.ccxt.com/ (testnet configuration)
- **Discord**: https://discord.com/developers/docs (OAuth guide)
- **Slack**: https://api.slack.com/authentication/basics (token setup)

---

**Generated**: 2026-10-03  
**Status**: Phase 2 Prerequisites Documented  
**All Accounts**: Free/Paper Trading (No Real Money Required)
