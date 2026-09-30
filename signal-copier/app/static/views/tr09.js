/* TR-09: Signal providers and collectors (`#/trade/sources`).
 *
 * Real backing data: GET /providers (every configured ProviderConfig +
 * its AnalystConfigs + the already-computed effective settings per
 * destination account -- app/providers.py), GET /routing-rules (whether a
 * provider's signals are actually admitted to any destination -- a
 * provider with no routing rule receives/records signals but never routes
 * them, see app/routing.py), GET /signals (the immutable, already-received
 * signal history, used honestly as "History jobs" -- this build has no
 * separate async "import job" concept, a received signal IS the durable
 * record), and the public GET /health worker-liveness read (no per-source
 * telemetry exists, so this is the closest real evidence for "Transport
 * health").
 *
 * Honest gaps, disclosed rather than invented: this build has no
 * transport_instance_id/channel_product_id/parser_version_id/cursor
 * registry and no rights_grant model anywhere (see app/providers.py,
 * app/db.py) -- those columns/panels render "not tracked in this build" /
 * StateMatrix "unsupported" rather than fabricated values. "Transport"
 * and "Parser" per source are inferred from a hand-maintained mirror of
 * this repo's app/sources/*.py module list (SOURCE_MODULES below, kept in
 * comments in sync with that directory) since no API exposes it live --
 * disclosed in the panel note. "Lag" is approximated as time-since-last-
 * received-signal (from GET /signals), explicitly labelled as NOT a real
 * collector/transport lag metric (no such telemetry is tracked).
 *
 * TR-09-A03 "Pause new admissions" reuses the exact same, already-tested
 * POST /providers/{provider_id} upsert TR-12 and the legacy dashboard's
 * Providers form both use (app/main.py's create_or_update_provider) --
 * sets this exact provider's enabled=false (SettingsOverride.enabled),
 * muting it at the provider level without touching any other provider,
 * analyst, or account setting. It's the provider-level analogue of TR-07's
 * account-level "Pause new entries" (same POST /accounts pattern).
 *
 * --- Phase B4/B5 additions (this batch): provider/analyst scorecards +
 * correlation, built ONLY from real, joinable data ---
 *
 * ## The one thing this batch had to get right: no per-provider equity
 *
 * `account_equity_snapshots` and `stop_target_events` (Phase A3/A4/A5,
 * app/equity_history.py / app/statistics.py) are keyed by ACCOUNT, never
 * by provider/analyst -- this codebase has no per-provider P&L
 * attribution model anywhere (checked: app/provider_value.py,
 * app/provider_scout.py -- both score a provider's SIGNAL behavior
 * (frequency, win/loss counts inferred from later price action), never a
 * real account-equity slice belonging to that provider alone). An
 * account's own real equity/statistics can stand in for "this provider's
 * performance" ONLY when GET /routing-rules proves a clean 1:1 mapping:
 * this exact provider's signals are routed to exactly one destination
 * account, AND that account never receives any other provider's routed
 * signals either. `_routingMapping()` below computes that from the real,
 * already-fetched `routing_rules` list (union of `destinations` per
 * source, union of sources targeting each account) -- a provider that
 * fans out to 2+ accounts, or an account fed by 2+ providers, is real but
 * AMBIGUOUS, and renders `unsupported` with the specific reason rather
 * than arbitrarily picking one account to stand in for that provider.
 * See this module's own load-bearing test in
 * tests/test_tr09_provider_scorecards_correlation.py, which breaks this
 * exact invariant on purpose (removing the "no other provider also routes
 * to this account" half of the check) and confirms the test written to
 * catch that regression fails.
 *
 * ## What's charted (fully real, no attribution ambiguity)
 *
 * 1. Provider behavior (from the same paginated GET /signals this screen
 *    already fetches for "History jobs" -- capped at whatever page size
 *    that call uses, disclosed in the panel note, same honest scoping
 *    TR-04's own signal analytics charts use): signals-per-day by
 *    provider, long-vs-short mix by provider, asset-class mix by
 *    provider, and instrument concentration for one selected provider.
 * 2. Provider quality funnel: received (a signal row exists) -> order
 *    created (a real GET /orders row's signal_id matches) -> filled
 *    (that order's status === "filled") -> protected (Phase A4's
 *    GET /positions/{account}/{symbol}/stop-events shows a real
 *    STOP_PLACED event at or after that order's own executed_at, for that
 *    exact account/symbol) -> closed (that account/symbol pair shows up
 *    in Phase A1's GET /positions/excursions, i.e. a real closed managed-
 *    lifecycle position exists for it). "Protected"/"closed" are keyed by
 *    (account_id, symbol), not by this exact signal alone -- if two
 *    signals for the same provider both target the same account+symbol,
 *    a stop/close event on either could be attributed to both; this is
 *    disclosed in the panel note, never silently assumed to be
 *    signal-exact. Only the account/symbol pairs actually seen among this
 *    page's filled orders are queried (capped at 25, see
 *    `_STOP_EVENT_LOOKUP_CAP` below, to bound how many
 *    GET /positions/.../stop-events calls one page load makes).
 * 3. Account performance proxy: ONLY for a provider with a real, verified
 *    clean 1:1 routing mapping (see above) -- that account's own real
 *    equity curve (GET /accounts/{id}/equity-history, Phase A3) and real
 *    rolling stats (GET /accounts/{id}/statistics, Phase A5), labeled as
 *    that account's own performance (not renamed to the provider's).
 * 4. Correlation: real Pearson correlation between any two accounts'
 *    equity series (GET /accounts/correlation, Phase A5) -- that endpoint
 *    itself returns `correlation: null` (never a fabricated 0) below its
 *    own MIN_CORRELATION_SAMPLES threshold, rendered here as an honest
 *    "insufficient overlapping data" state. With likely only 1-2 real
 *    demo accounts in this environment, a real 2-node correlation NUMBER
 *    is meaningful; a network-graph/dendrogram visual is not (that needs
 *    3+ real correlated series to say anything a single number doesn't)
 *    -- deliberately not built here, per this batch's brief.
 *
 * Deliberately NOT built (per this batch's brief): provider-vs-copier
 * attribution against an external platform (no external-platform data
 * exists anywhere in this codebase); a trade-outcome distribution from
 * per-provider-aggregated MAE/MFE (Phase A1's MAE/MFE is per POSITION,
 * i.e. per account+symbol, with the same many-signals-per-position
 * ambiguity as the funnel's "protected"/"closed" stages above, but
 * spread across a whole distribution rather than one funnel count would
 * compound that ambiguity into a misleading-looking chart -- left out
 * rather than forced).
 *
 * --- Phase C (this batch): deeper provider scorecard + provider-vs-
 * copier execution gap + provider style distributions, all still built
 * ONLY from real, joinable data ---
 *
 * ## C1: "Provider signal vs. our copied execution" (#tr09-p10)
 *
 * The one genuinely new join this batch adds: for every real order whose
 * `signal_id` matches a real, already-fetched signal (same GET /signals
 * page this whole view already reads), compare what the PROVIDER'S
 * signal actually specified (`signal.price`/`signal.quantity`, both real
 * columns on the `signals` table, see app/models.py's `Signal` and
 * app/db.py's schema) against what this service actually did with it:
 * `order.requested_quantity` (this app's own real sizing decision, after
 * multiplier/fixed_quantity), `order.filled_quantity`/`order.filled_price`
 * (the real broker fill). Real, signed, side-aware slippage =
 * `filled_price - signal.price` for a BUY (positive = paid MORE than the
 * signal specified = adverse) and `signal.price - filled_price` for a
 * SELL (positive = received LESS = adverse) -- computed only when BOTH
 * real prices exist (a market signal with no `price` field contributes no
 * slippage sample, never a fabricated 0). The quantity waterfall is two
 * real, separately-attributable gaps: `signal.quantity - requested_quantity`
 * (this app's OWN sizing choice, e.g. a multiplier<1 or a fixed_quantity
 * override -- nothing to do with execution) and
 * `requested_quantity - filled_quantity` (what the broker/risk layer
 * actually did with that request -- a partial fill or a rejection).
 * Rejected/errored orders are grouped by their own real `message` column
 * (never invented) into a rejection-reason breakdown. See this file's own
 * load-bearing test in tests/test_tr09_provider_scorecards_correlation.py
 * (`test_tr09_signal_vs_execution_gap_...`), which breaks this exact sign
 * convention on purpose (flips the BUY/SELL slippage formula) and confirms
 * the test written to catch that regression fails.
 *
 * ## C2: Provider scorecard (#tr09-p11)
 *
 * Extends the quality funnel (received/order_created/filled/protected/
 * closed) with real per-(source) outcome figures replayed from confirmed
 * fills GLOBALLY across every account via the already-real, already-
 * tested GET /providers/value (app/provider_value.py's FIFO-lot replay,
 * unaffected by the funnel's account+symbol keying ambiguity since it
 * tags every lot with its OWN originating signal's provider) -- net
 * realized P&L, win rate, profit factor, average win/gross_profit and
 * average loss/gross_loss (this batch adds those last two to
 * `ProviderValue.to_dict()`, which already computed them internally but
 * never projected them out), expectancy (realized_pnl / closing_fills,
 * i.e. real average $ per closing fill, never a percentage return -- see
 * that module's own documented scope limits, most importantly that a
 * managed-lifecycle stop/target exit is NOT replayed here at all), and
 * cost burden (GET /providers/value's own `subscription`/
 * `provider_verdict_detail.cost_to_date`/`net_value`, real when a
 * `provider_subscriptions` row exists). Metrics with genuinely no real
 * data path anywhere in this codebase (valid-parse %% -- a failed parse
 * is never persisted as a row anywhere to count; capital utilization --
 * app/capital_allocator.py tracks only a global ceiling, never a
 * per-provider figure; per-provider drawdown/Sharpe/Sortino -- no
 * per-provider equity time series exists, only the account-performance-
 * proxy panel's per-ACCOUNT one for a clean 1:1 mapping) render
 * Components.renderCapabilityState({status:"not_tracked"}) with the
 * specific reason, never a fabricated number.
 *
 * ## C3: Provider style analysis (#tr09-p12)
 *
 * Adds three real distributions to #tr09-p06's existing asset-mix/long-
 * short-mix charts: (1) time-of-day, straight off each signal's own real
 * `received_at` hour -- always real, no ambiguity. (2) Stop-distance/
 * target-distance and typical risk/reward: `|stop_loss - price|`/
 * `|take_profit - price|` off the SAME signal row (real whenever a signal
 * carries all of `price`/`stop_loss`/`take_profit`; silently skipped, not
 * defaulted to 0, when any is missing), with risk/reward = target-distance
 * / stop-distance. (3) Holding-duration: uses `orders.family_id` (DB-0X,
 * real) to pair a managed-lifecycle position's own 'entry' order with its
 * OWN 'close' order (same family_id -- an exact, non-ambiguous link, not
 * the funnel's account+symbol heuristic) and takes the real wall-clock gap
 * between their two `executed_at` timestamps -- scoped, disclosed, to
 * managed_lifecycle accounts only (a plain account's close has no
 * family_id at all, per this table's own schema comment, so it is
 * honestly excluded rather than guessed).
 */
(function () {
  "use strict";

  // Hand-maintained mirror of app/sources/*.py -- see this file's module
  // docstring above for why (no live registry endpoint exists to query
  // this from). free_text: true means this module route its inbound
  // message through app/sources/text_parser.py's shared grammar (see that
  // module's own grep-verified import list); false means a structured
  // JSON/callback payload with no text grammar involved.
  const SOURCE_MODULES = {
    telegram: { transport: "Telegram bot (pull; needs TELEGRAM_BOT_TOKEN + restart)", free_text: true },
    discord: { transport: "Discord bot (pull; needs DISCORD_BOT_TOKEN + restart)", free_text: true },
    slack: { transport: "Slack app (pull; needs SLACK_BOT_TOKEN + restart)", free_text: true },
    whatsapp: { transport: "WhatsApp Cloud API webhook (push; POST /whatsapp/webhook)", free_text: true },
    sms_twilio: { transport: "Twilio SMS webhook (push; POST /sms/twilio)", free_text: true },
    twitter: { transport: "Twitter/X polling adapter (pull; needs credentials + restart)", free_text: true },
    ninjatrader: { transport: "NinjaTrader webhook (push; POST /ninjatrader/webhook)", free_text: false },
    mt4_mt5: { transport: "MT4/MT5 bridge (pull/polling)", free_text: false },
    rithmic: { transport: "Rithmic API adapter (pull)", free_text: false },
    webhook: { transport: "Generic webhook (push; POST /webhook/{source})", free_text: false },
  };

  function transportFor(providerId) {
    return SOURCE_MODULES[providerId] || null;
  }

  // Placeholder-slot helper: capability-state badges (Components.
  // renderCapabilityState) render into a real DOM element, but the panels
  // below build most of their HTML as one big string before it is
  // inserted -- same idiom tr04.js/tr05.js/tr07.js/tr08.js/tr10.js/tr11.js/
  // tr16.js already use. `capSlot` reserves an id inside that string;
  // `mountCapStates` is called once the string has been assigned to
  // `.innerHTML` so it can find those ids and fill each one in.
  function capSlot(id) {
    return `<span class="cap-state-slot" id="${id}"></span>`;
  }
  function mountCapStates(root, specs) {
    for (const [id, opts] of specs) {
      const el = root.querySelector(`#${id}`);
      if (el) Components.renderCapabilityState(el, opts);
    }
  }

  function isFiniteNum(v) {
    return typeof v === "number" && Number.isFinite(v);
  }

  // Bounds how many distinct (account_id, symbol) pairs the quality
  // funnel's "protected" stage will call GET /positions/.../stop-events
  // for on one page load -- a real, disclosed cap, not a silent drop.
  const _STOP_EVENT_LOOKUP_CAP = 25;

  const CHART_PALETTE = ["#3ddc84", "#4f8cff", "#ffb347", "#ff6b6b", "#b47dff", "#3ec6c6", "#e0e0e0", "#f45b8d"];

  // One persistent Chart.js instance per canvas -- destroyed and
  // recreated on every load()/selector-change, the same pattern
  // tr04.js/tr15.js/dashboard.html already established.
  const charts = {
    perDay: null,
    longShort: null,
    assetClass: null,
    instrument: null,
    funnel: null,
    correlation: null,
    timeOfDay: null,
    holding: null,
  };
  const equityCharts = new Map(); // account_id -> Chart.js instance (account-performance-proxy panel)

  function destroyChart(key) {
    if (charts[key]) {
      charts[key].destroy();
      charts[key] = null;
    }
  }
  function destroyEquityCharts() {
    for (const chart of equityCharts.values()) chart.destroy();
    equityCharts.clear();
  }

  function renderGroupedBarChart(canvas, labels, datasets, { stacked = false } = {}) {
    return new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: datasets.length > 1, labels: { color: "#e6e9f0" } } },
        scales: {
          x: { stacked },
          y: { stacked, beginAtZero: true, ticks: { precision: 0 } },
        },
      },
    });
  }

  function isoDay(ts) {
    const t = Date.parse(ts);
    return Number.isNaN(t) ? null : new Date(t).toISOString().slice(0, 10);
  }

  // --- Real routing-mapping honesty check (see this file's module
  // docstring's "one thing this batch had to get right"). ---
  function routingMapping(routingRules) {
    const providerToAccounts = new Map(); // source -> Set(account_id)
    const accountToProviders = new Map(); // account_id -> Set(source)
    for (const rule of routingRules) {
      const accounts = providerToAccounts.get(rule.source) || new Set();
      for (const accountId of rule.destinations || []) {
        accounts.add(accountId);
        const providers = accountToProviders.get(accountId) || new Set();
        providers.add(rule.source);
        accountToProviders.set(accountId, providers);
      }
      providerToAccounts.set(rule.source, accounts);
    }

    function forProvider(providerId) {
      const accounts = providerToAccounts.get(providerId);
      if (!accounts || accounts.size === 0) {
        return { clean: false, accountId: null, reason: "no routing rule routes this provider's signals to any destination account -- there is nothing real to attribute an account's performance to." };
      }
      if (accounts.size > 1) {
        return {
          clean: false,
          accountId: null,
          reason: `this provider's signals are routed to ${accounts.size} destination accounts (${[...accounts].join(", ")}) -- no single account's equity/statistics can honestly stand in for this provider's own performance.`,
        };
      }
      const [accountId] = accounts;
      const providersOnThatAccount = accountToProviders.get(accountId) || new Set();
      if (providersOnThatAccount.size > 1) {
        return {
          clean: false,
          accountId: null,
          reason: `destination account "${accountId}" also receives routed signals from ${providersOnThatAccount.size - 1} other provider(s) (${[...providersOnThatAccount].filter((p) => p !== providerId).join(", ")}) -- its equity/statistics reflect a mix of providers, not this one alone.`,
        };
      }
      return { clean: true, accountId, reason: null };
    }

    return { forProvider };
  }

  // --- Provider behavior (real, from the fetched signals page) ---
  function computeProviderBehavior(signals) {
    const byProvider = new Map(); // provider -> {byDay: Map, side: Map, assetClass: Map, symbol: Map}
    for (const s of signals) {
      const entry = byProvider.get(s.source) || { byDay: new Map(), side: new Map(), assetClass: new Map(), symbol: new Map() };
      const day = isoDay(s.received_at);
      if (day) entry.byDay.set(day, (entry.byDay.get(day) || 0) + 1);
      const side = s.side || "(none)";
      entry.side.set(side, (entry.side.get(side) || 0) + 1);
      const ac = s.asset_class || "(none)";
      entry.assetClass.set(ac, (entry.assetClass.get(ac) || 0) + 1);
      entry.symbol.set(s.symbol, (entry.symbol.get(s.symbol) || 0) + 1);
      byProvider.set(s.source, entry);
    }
    return byProvider;
  }

  function renderPerDayChart(wrap, byProvider) {
    destroyChart("perDay");
    const providers = [...byProvider.keys()];
    if (!providers.length) {
      wrap.innerHTML = `<div class="empty">No signals to chart yet.</div>`;
      return;
    }
    const allDays = new Set();
    for (const entry of byProvider.values()) for (const day of entry.byDay.keys()) allDays.add(day);
    const days = [...allDays].sort();
    if (!days.length) {
      wrap.innerHTML = `<div class="empty">No signal has a real received_at timestamp to chart yet.</div>`;
      return;
    }
    const datasets = providers.map((p, i) => ({
      label: p,
      data: days.map((d) => byProvider.get(p).byDay.get(d) || 0),
      backgroundColor: CHART_PALETTE[i % CHART_PALETTE.length],
    }));
    wrap.innerHTML = `<div class="chart-container"><canvas id="tr09-perday-chart"></canvas></div>`;
    charts.perDay = renderGroupedBarChart(wrap.querySelector("#tr09-perday-chart"), days, datasets);
  }

  function renderStackedMixChart(wrap, canvasId, chartKey, byProvider, mapKey, colorOrder) {
    destroyChart(chartKey);
    const providers = [...byProvider.keys()];
    if (!providers.length) {
      wrap.innerHTML = `<div class="empty">No signals to chart yet.</div>`;
      return;
    }
    const allKeys = new Set();
    for (const entry of byProvider.values()) for (const k of entry[mapKey].keys()) allKeys.add(k);
    const keys = colorOrder ? colorOrder.filter((k) => allKeys.has(k)).concat([...allKeys].filter((k) => !colorOrder.includes(k))) : [...allKeys];
    const datasets = keys.map((k, i) => ({
      label: k,
      data: providers.map((p) => byProvider.get(p)[mapKey].get(k) || 0),
      backgroundColor: CHART_PALETTE[i % CHART_PALETTE.length],
    }));
    wrap.innerHTML = `<div class="chart-container"><canvas id="${canvasId}"></canvas></div>`;
    charts[chartKey] = renderGroupedBarChart(wrap.querySelector(`#${canvasId}`), providers, datasets, { stacked: true });
  }

  function renderInstrumentChart(wrap, byProvider, selectedProvider) {
    destroyChart("instrument");
    const entry = byProvider.get(selectedProvider);
    if (!entry || entry.symbol.size === 0) {
      wrap.innerHTML = `<div class="empty">No signals from "${escapeHtml(selectedProvider)}" to chart yet.</div>`;
      return;
    }
    const ranked = [...entry.symbol.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10);
    const labels = ranked.map(([symbol]) => symbol);
    const values = ranked.map(([, n]) => n);
    wrap.innerHTML = `<div class="chart-container"><canvas id="tr09-instrument-chart"></canvas></div>`;
    charts.instrument = renderGroupedBarChart(
      wrap.querySelector("#tr09-instrument-chart"),
      labels,
      [{ label: `${selectedProvider} signals by instrument`, data: values, backgroundColor: CHART_PALETTE[0] }]
    );
  }

  // --- Provider quality funnel (real, joined data -- see module docstring
  // for exactly what each stage does and doesn't mean). ---
  async function computeQualityFunnel(ctx, signals, orders, excursions) {
    const ordersBySignal = new Map();
    for (const o of orders) {
      if (!ordersBySignal.has(o.signal_id)) ordersBySignal.set(o.signal_id, []);
      ordersBySignal.get(o.signal_id).push(o);
    }
    const closedPairs = new Set(excursions.map((e) => `${e.account_id}::${e.symbol}`));

    // Bound how many distinct (account_id, symbol) pairs get a real
    // stop-events lookup -- see _STOP_EVENT_LOOKUP_CAP's own comment.
    const pairsNeeded = new Set();
    for (const s of signals) {
      for (const o of ordersBySignal.get(s.id) || []) {
        if (o.status === "filled") pairsNeeded.add(`${o.account_id}::${o.symbol}`);
      }
    }
    const boundedPairs = [...pairsNeeded].slice(0, _STOP_EVENT_LOOKUP_CAP);
    const truncated = pairsNeeded.size > boundedPairs.length;
    const stopEventsByPair = new Map();
    for (const pair of boundedPairs) {
      const [accountId, symbol] = pair.split("::");
      const res = await ctx.fetchJSON(`/positions/${encodeURIComponent(accountId)}/${encodeURIComponent(symbol)}/stop-events`);
      stopEventsByPair.set(pair, res.ok && res.data ? res.data.events || [] : []);
    }

    const byProvider = new Map(); // provider -> {received, order_created, filled, protected, closed}
    for (const s of signals) {
      const stage = byProvider.get(s.source) || { received: 0, order_created: 0, filled: 0, protected: 0, closed: 0 };
      stage.received += 1;
      const matching = ordersBySignal.get(s.id) || [];
      if (matching.length) stage.order_created += 1;
      const filledOrders = matching.filter((o) => o.status === "filled");
      if (filledOrders.length) stage.filled += 1;
      const isProtected = filledOrders.some((o) => {
        const pair = `${o.account_id}::${o.symbol}`;
        const events = stopEventsByPair.get(pair) || [];
        const orderTime = o.executed_at ? Date.parse(o.executed_at) : null;
        return events.some((e) => e.event_type === "stop_placed" && (!orderTime || !e.at || Date.parse(e.at) >= orderTime));
      });
      if (isProtected) stage.protected += 1;
      const isClosed = filledOrders.some((o) => closedPairs.has(`${o.account_id}::${o.symbol}`));
      if (isClosed) stage.closed += 1;
      byProvider.set(s.source, stage);
    }
    return { byProvider, truncated, pairsConsidered: pairsNeeded.size, pairsQueried: boundedPairs.length };
  }

  function renderFunnelChart(wrap, byProvider) {
    destroyChart("funnel");
    const providers = [...byProvider.keys()];
    if (!providers.length) {
      wrap.innerHTML = `<div class="empty">No signals to build a funnel from yet.</div>`;
      return;
    }
    const stages = ["received", "order_created", "filled", "protected", "closed"];
    const stageLabels = ["Received", "Order created", "Filled", "Protected (STOP_PLACED)", "Closed"];
    const datasets = providers.map((p, i) => ({
      label: p,
      data: stages.map((st) => byProvider.get(p)[st] || 0),
      backgroundColor: CHART_PALETTE[i % CHART_PALETTE.length],
    }));
    wrap.innerHTML = `<div class="chart-container"><canvas id="tr09-funnel-chart"></canvas></div>`;
    charts.funnel = renderGroupedBarChart(wrap.querySelector("#tr09-funnel-chart"), stageLabels, datasets);
  }

  // --- C1: real "provider signal vs. our copied execution" gap (see this
  // file's own module docstring section C1 for the exact join and sign
  // convention -- this is the function this batch's load-bearing test in
  // tests/test_tr09_provider_scorecards_correlation.py exercises). ---
  function newGapEntry() {
    return {
      matchedFilledCount: 0,
      slippages: [], // signed, real price units, positive == adverse to the trader (see docstring)
      sizingGaps: [], // signal.quantity - requested_quantity (this app's own sizing choice)
      executionGaps: [], // requested_quantity - filled_quantity (what the broker/risk layer actually did)
      rejectedCount: 0,
      rejections: new Map(), // real order.message -> count
    };
  }

  function computeSignalExecutionGap(signals, orders) {
    const signalById = new Map(signals.map((s) => [s.id, s]));
    const byProvider = new Map();
    for (const o of orders) {
      const sig = signalById.get(o.signal_id);
      if (!sig) continue; // no matching signal on this page's window -- never guessed
      const entry = byProvider.get(sig.source) || newGapEntry();
      byProvider.set(sig.source, entry);

      if (o.status === "rejected" || o.status === "error") {
        entry.rejectedCount += 1;
        const reason = (o.message || "").trim() || "(no reason recorded on this order)";
        entry.rejections.set(reason, (entry.rejections.get(reason) || 0) + 1);
        continue;
      }
      if (o.status !== "filled") continue;
      entry.matchedFilledCount += 1;

      if (isFiniteNum(sig.quantity) && isFiniteNum(o.requested_quantity)) {
        entry.sizingGaps.push(sig.quantity - o.requested_quantity);
      }
      if (isFiniteNum(o.requested_quantity) && isFiniteNum(o.filled_quantity)) {
        entry.executionGaps.push(o.requested_quantity - o.filled_quantity);
      }
      if (isFiniteNum(sig.price) && isFiniteNum(o.filled_price)) {
        const side = (sig.side || "").toLowerCase();
        // BUY: paying MORE than the signal specified is adverse -> positive.
        // SELL: receiving LESS than the signal specified is adverse -> positive.
        // Never computed for a CLOSE signal (no consistent "adverse direction"
        // without knowing the position's own original side, which this join
        // doesn't have) -- honestly skipped, not guessed.
        if (side === "buy") entry.slippages.push(o.filled_price - sig.price);
        else if (side === "sell") entry.slippages.push(sig.price - o.filled_price);
      }
    }
    return byProvider;
  }

  function mean(arr) {
    return arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null;
  }

  function renderSignalExecutionGap(el, byProvider) {
    const providers = [...byProvider.keys()];
    if (!providers.length) {
      StateMatrix.render(el, { state: "empty", emptyMessage: "No order references a signal on this page's window yet -- nothing real to compare." });
      return;
    }
    const rows = providers.map((p) => {
      const e = byProvider.get(p);
      const avgSlippage = mean(e.slippages);
      const avgSizingGap = mean(e.sizingGaps);
      const avgExecutionGap = mean(e.executionGaps);
      return [
        `<span class="mono">${escapeHtml(p)}</span>`,
        e.slippages.length ? `${fmtNum(avgSlippage)} <span class="section-note">(n=${e.slippages.length}, ${avgSlippage > 0 ? "adverse" : avgSlippage < 0 ? "favorable" : "neutral"})</span>` : pill("no real price on both sides", "muted"),
        e.sizingGaps.length ? `${fmtNum(avgSizingGap)} <span class="section-note">(n=${e.sizingGaps.length})</span>` : pill("no real quantity on both sides", "muted"),
        e.executionGaps.length ? `${fmtNum(avgExecutionGap)} <span class="section-note">(n=${e.executionGaps.length})</span>` : pill("no real quantity on both sides", "muted"),
        `${e.rejectedCount}`,
      ];
    });
    const rejectionRows = [];
    for (const p of providers) {
      const e = byProvider.get(p);
      for (const [reason, count] of e.rejections.entries()) {
        rejectionRows.push([`<span class="mono">${escapeHtml(p)}</span>`, escapeHtml(reason), `${count}`]);
      }
    }
    StateMatrix.render(el, {
      state: "ready",
      html: `
        <p class="section-note" style="margin-top:0;">Real, signed comparison of what each provider's signal specified vs. what this service actually did with it, joined by <span class="mono">order.signal_id</span> over the same signals/orders pages this screen already loaded. "Avg slippage" is <span class="mono">filled_price - signal.price</span> for a BUY and <span class="mono">signal.price - filled_price</span> for a SELL (positive == adverse to the trader) -- computed only over filled orders where BOTH the signal's own <span class="mono">price</span> and the order's own <span class="mono">filled_price</span> are real (a market signal with no price contributes no sample). "Avg sizing gap" is <span class="mono">signal.quantity - requested_quantity</span> (this app's own multiplier/fixed_quantity sizing decision, nothing to do with execution). "Avg execution gap" is <span class="mono">requested_quantity - filled_quantity</span> (what the broker/risk layer actually filled of what was requested). "Rejected/errored" counts real orders whose status is rejected or error; their own real <span class="mono">message</span> reasons are broken out below.</p>
        ${table(["Provider", "Avg slippage (price units, +adverse)", "Avg sizing gap (qty)", "Avg execution gap (qty)", "Rejected/errored orders"], rows, "No providers.")}
        ${rejectionRows.length ? `<div style="margin-top:12px;"><h3 class="section-note">Rejection/error reasons (real order.message text)</h3>${table(["Provider", "Reason", "Count"], rejectionRows, "None.")}</div>` : ""}
      `,
    });
  }

  // --- C2: provider scorecard -- funnel counts (already computed) plus
  // real per-source outcome figures from GET /providers/value (see this
  // file's own module docstring section C2). ---
  function aggregateProviderValueBySource(providerValueRows) {
    const bySource = new Map();
    for (const row of providerValueRows) {
      const agg = bySource.get(row.source) || {
        realized_pnl: 0,
        gross_profit: 0,
        gross_loss: 0,
        closing_fills: 0,
        winning_closing_fills: 0,
        entries_opened: 0,
        subscription: row.subscription || null,
        provider_verdict: row.provider_verdict,
        provider_verdict_detail: row.provider_verdict_detail || {},
      };
      agg.realized_pnl += row.realized_pnl || 0;
      agg.gross_profit += row.gross_profit || 0;
      agg.gross_loss += row.gross_loss || 0;
      agg.closing_fills += row.closing_fills || 0;
      agg.winning_closing_fills += row.winning_closing_fills || 0;
      agg.entries_opened += row.entries_opened || 0;
      bySource.set(row.source, agg);
    }
    // Real aggregate profit factor, computed the SAME way
    // app/provider_value.py's own ProviderValue.profit_factor property
    // does (gross_profit / gross_loss, None -- never a fabricated
    // infinity -- when there's no losing fill yet), just summed across
    // this source's (analyst, asset_class) rows first.
    for (const agg of bySource.values()) {
      agg.profit_factor = agg.gross_loss > 0 ? agg.gross_profit / agg.gross_loss : null;
    }
    return bySource;
  }

  function renderProviderScorecard(el, providers, signals, providerValueBySource, routingMappingObj) {
    if (!providers.length) {
      StateMatrix.render(el, { state: "empty", emptyMessage: "No authorized source is configured." });
      return;
    }
    const bySignalSource = new Map(); // provider -> {received, priceCoverage}
    for (const s of signals) {
      const entry = bySignalSource.get(s.source) || { received: 0, priced: 0 };
      entry.received += 1;
      if (isFiniteNum(s.price)) entry.priced += 1;
      bySignalSource.set(s.source, entry);
    }
    const rows = providers.map((p) => {
      const providerId = p.provider_id;
      const sigStats = bySignalSource.get(providerId) || { received: 0, priced: 0 };
      const pv = providerValueBySource.get(providerId);
      const losingFills = pv ? pv.closing_fills - pv.winning_closing_fills : 0;
      const avgWin = pv && pv.winning_closing_fills > 0 ? pv.gross_profit / pv.winning_closing_fills : null;
      const avgLoss = pv && losingFills > 0 ? pv.gross_loss / losingFills : null;
      const expectancy = pv && pv.closing_fills > 0 ? pv.realized_pnl / pv.closing_fills : null;
      const coveragePct = sigStats.received > 0 ? (100 * sigStats.priced) / sigStats.received : null;
      let costBurdenCell;
      if (!pv || !pv.subscription) {
        costBurdenCell = pill("no subscription tracked", "muted");
      } else {
        const detail = pv.provider_verdict_detail || {};
        costBurdenCell = `${fmtNum(pv.subscription.cost_amount)}/${escapeHtml(pv.subscription.billing_cycle)} <span class="section-note">(cost-to-date ${fmtNum(detail.cost_to_date)}, net value ${fmtNum(detail.net_value)})</span>`;
      }
      const drawdownSlot = `tr09-sc-drawdown-${escapeAttr(providerId)}`;
      return {
        cells: [
          `<span class="mono">${escapeHtml(providerId)}</span>`,
          `${sigStats.received}`,
          coveragePct === null ? pill("no signals", "muted") : `${fmtNum(coveragePct)}%`,
          pv && pv.closing_fills > 0 ? `${pv.closing_fills}` : pill("no closing fills replayed", "muted"),
          pv && pv.closing_fills > 0 ? `${fmtNum(100 * (pv.winning_closing_fills / pv.closing_fills))}%` : pill("insufficient data", "muted"),
          pv && pv.profit_factor !== null && pv.profit_factor !== undefined ? fmtNum(pv.profit_factor) : pill("no losing fill yet", "muted"),
          avgWin === null ? pill("no winning fill yet", "muted") : fmtNum(avgWin),
          avgLoss === null ? pill("no losing fill yet", "muted") : fmtNum(avgLoss),
          expectancy === null ? pill("insufficient data", "muted") : fmtNum(expectancy),
          pv ? fmtNum(pv.realized_pnl) : pill("insufficient data", "muted"),
          costBurdenCell,
          capSlot(drawdownSlot),
        ],
        drawdownSlot,
        providerId,
      };
    });
    const parseSlot = "tr09-sc-parse-note";
    const capitalSlot = "tr09-sc-capital-note";
    StateMatrix.render(el, {
      state: "ready",
      html: `
        <p class="section-note" style="margin-top:0;">Outcome columns (Completed episodes onward) are GET /providers/value's real FIFO-lot replay of every confirmed fill GLOBALLY across every account, attributed by each lot's own originating signal -- see app/provider_value.py's own scope notes (a managed-lifecycle stop/target exit never creates an order row, so it is NOT replayed here; a provider whose losses are mostly caught by stops will look better here than its real outcome). "Signal price coverage" is the real fraction of this provider's received signals that carried a non-null <span class="mono">price</span> field (needed for the slippage panel above), never a fabricated data-quality score.</p>
        ${table(
          ["Provider", "Signals received", "Signal price coverage", "Completed episodes", "Win rate", "Profit factor", "Avg win ($)", "Avg loss ($)", "Expectancy ($/episode)", "Net realized P&L", "Cost burden", "Drawdown / Sharpe / Sortino"],
          rows.map((r) => r.cells),
          "No providers."
        )}
        <div style="margin-top:12px;">${capSlot(parseSlot)} ${capSlot(capitalSlot)}</div>
      `,
    });
    mountCapStates(el, [
      [
        parseSlot,
        {
          status: "not_tracked",
          reason: "A signal that fails to parse is never persisted as a row anywhere in this codebase (a Signal object only exists once parsing already succeeded) -- there is no real numerator or denominator for a 'valid parse %' here. GET /sources/{source}/classify-messages (TR-10's parser laboratory) validates PASTED text offline; it never runs against live inbound traffic.",
        },
      ],
      [
        capitalSlot,
        {
          status: "not_tracked",
          reason: "app/capital_allocator.py tracks one GLOBAL deployed-capital ceiling across every account for sizing decisions -- it has no per-provider notional or utilization figure anywhere to report.",
        },
      ],
    ]);
    for (const r of rows) {
      const mapping = routingMappingObj.forProvider(r.providerId);
      mountCapStates(el, [
        [
          r.drawdownSlot,
          mapping.clean
            ? { status: "unsupported", reason: `No per-provider equity time series exists -- see the account-performance-proxy panel below for account "${mapping.accountId}"'s own real drawdown/Sharpe-equivalent/Sortino-equivalent, shown there because this provider has a real, verified clean 1:1 routing mapping to it.` }
            : { status: "not_tracked", reason: `No per-provider equity time series exists anywhere in this codebase, and this provider has no real, clean 1:1 routing mapping to stand in an account's own equity curve for it (${mapping.reason})` },
        ],
      ]);
    }
  }

  // --- C3: provider style analysis -- time-of-day / stop-distance /
  // target-distance / risk-reward (all straight off signal fields already
  // fetched) plus holding-duration (via orders.family_id, see docstring). ---
  function computeStyleDistributions(signals) {
    const byProvider = new Map(); // provider -> {hours: number[24], stopDist: [], targetDist: [], rr: []}
    for (const s of signals) {
      const entry = byProvider.get(s.source) || { hours: new Array(24).fill(0), stopDist: [], targetDist: [], rr: [] };
      const t = Date.parse(s.received_at);
      if (!Number.isNaN(t)) entry.hours[new Date(t).getUTCHours()] += 1;
      if (isFiniteNum(s.price) && isFiniteNum(s.stop_loss)) {
        const stopDist = Math.abs(s.stop_loss - s.price);
        entry.stopDist.push(stopDist);
        if (isFiniteNum(s.take_profit) && stopDist > 0) {
          const targetDist = Math.abs(s.take_profit - s.price);
          entry.targetDist.push(targetDist);
          entry.rr.push(targetDist / stopDist);
        }
      }
      byProvider.set(s.source, entry);
    }
    return byProvider;
  }

  function median(arr) {
    if (!arr.length) return null;
    const sorted = [...arr].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  }

  // --- Real holding-duration: exact orders.family_id link between a
  // managed-lifecycle position's own 'entry' order and its OWN 'close'
  // order (see this file's module docstring section C3). ---
  function computeHoldingDurations(orders) {
    const byFamily = new Map(); // family_id -> {entryAt, closeAt, source}
    const signalSourceByOrderSignal = new Map(); // filled in by caller if needed
    for (const o of orders) {
      if (!o.family_id || o.status !== "filled" || !o.executed_at) continue;
      const fam = byFamily.get(o.family_id) || {};
      if (o.purpose === "entry") fam.entryAt = o.executed_at;
      if (o.purpose === "close") fam.closeAt = o.executed_at;
      byFamily.set(o.family_id, fam);
    }
    const durationsSeconds = [];
    for (const fam of byFamily.values()) {
      if (!fam.entryAt || !fam.closeAt) continue;
      const dt = (Date.parse(fam.closeAt) - Date.parse(fam.entryAt)) / 1000;
      if (Number.isFinite(dt) && dt >= 0) durationsSeconds.push(dt);
    }
    return durationsSeconds;
  }

  function renderStyleAnalysis(el, byProvider, holdingDurationsSeconds) {
    const providers = [...byProvider.keys()];
    if (!providers.length) {
      StateMatrix.render(el, { state: "empty", emptyMessage: "No signals have been received yet -- nothing real to chart." });
      return;
    }
    const rrRows = providers.map((p) => {
      const e = byProvider.get(p);
      return [
        `<span class="mono">${escapeHtml(p)}</span>`,
        e.stopDist.length ? `${fmtNum(mean(e.stopDist))} <span class="section-note">(n=${e.stopDist.length})</span>` : pill("no signal has both price and stop_loss", "muted"),
        e.targetDist.length ? `${fmtNum(mean(e.targetDist))} <span class="section-note">(n=${e.targetDist.length})</span>` : pill("no signal has price, stop_loss and take_profit", "muted"),
        e.rr.length ? `${fmtNum(median(e.rr))} <span class="section-note">(median, n=${e.rr.length})</span>` : pill("insufficient data", "muted"),
      ];
    });
    el.innerHTML = `
      <p class="section-note" style="margin-top:0;">Real distributions computed client-side from the same signals page #tr09-p06 already loaded (never padded to look fuller). Time-of-day/stop-distance/target-distance/risk-reward are per-signal (no attribution ambiguity); holding-duration below is scoped to managed_lifecycle positions only (see panel note).</p>
      <div class="tr-chart-grid" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:16px;">
        <div>
          <h3 class="section-note">Time of day (UTC hour), by provider</h3>
          <label>Provider <select id="tr09-tod-provider">${providers.map((p) => `<option value="${escapeAttr(p)}">${escapeHtml(p)}</option>`).join("")}</select></label>
          <div id="tr09-tod-wrap"></div>
        </div>
        <div>
          <h3 class="section-note">Holding-duration distribution (managed_lifecycle only, minutes)</h3>
          <div id="tr09-holding-wrap"></div>
        </div>
      </div>
      <div style="margin-top:16px;">
        <h3 class="section-note">Stop/target distance and typical risk/reward, by provider</h3>
        ${table(["Provider", "Avg stop distance (price units)", "Avg target distance (price units)", "Median risk/reward (target/stop)"], rrRows, "No providers.")}
      </div>
    `;
    const todSelect = el.querySelector("#tr09-tod-provider");
    const todWrap = el.querySelector("#tr09-tod-wrap");
    function renderTod(providerId) {
      destroyChart("timeOfDay");
      const entry = byProvider.get(providerId);
      const labels = [...Array(24).keys()].map((h) => `${h}:00`);
      todWrap.innerHTML = `<div class="chart-container"><canvas id="tr09-tod-chart"></canvas></div>`;
      charts.timeOfDay = renderGroupedBarChart(todWrap.querySelector("#tr09-tod-chart"), labels, [
        { label: `${providerId} signals by hour (UTC)`, data: entry.hours, backgroundColor: CHART_PALETTE[0] },
      ]);
    }
    renderTod(providers[0]);
    todSelect.addEventListener("change", () => renderTod(todSelect.value));

    const holdingWrap = el.querySelector("#tr09-holding-wrap");
    if (!holdingDurationsSeconds.length) {
      holdingWrap.innerHTML = `<div class="empty">No real entry/close order pair sharing a family_id exists yet (managed_lifecycle only) -- nothing real to chart.</div>`;
    } else {
      const minutes = holdingDurationsSeconds.map((s) => s / 60);
      const max = Math.max(...minutes);
      const bucketCount = Math.min(10, Math.max(1, Math.ceil(Math.sqrt(minutes.length))));
      const bucketSize = max > 0 ? max / bucketCount : 1;
      const buckets = new Array(bucketCount).fill(0);
      for (const m of minutes) {
        const idx = bucketSize > 0 ? Math.min(bucketCount - 1, Math.floor(m / bucketSize)) : 0;
        buckets[idx] += 1;
      }
      const labels = buckets.map((_, i) => `${fmtNum(i * bucketSize)}-${fmtNum((i + 1) * bucketSize)}`);
      holdingWrap.innerHTML = `<div class="chart-container"><canvas id="tr09-holding-chart"></canvas></div>`;
      charts.holding = renderGroupedBarChart(holdingWrap.querySelector("#tr09-holding-chart"), labels, [
        { label: `Closed managed-lifecycle positions (n=${minutes.length})`, data: buckets, backgroundColor: CHART_PALETTE[1] },
      ]);
    }
  }

  function shell() {
    return `
      <section class="tr-panel" id="tr09-p01"><h2>Sources</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr09-p02"><h2>Transport health</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr09-p03"><h2>Parser coverage</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr09-p04"><h2>Rights</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr09-p05"><h2>History jobs</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr09-p06"><h2>Provider behavior</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr09-p07"><h2>Provider quality funnel</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr09-p08"><h2>Account performance proxy</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr09-p09"><h2>Correlation</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr09-p10"><h2>Signal vs. execution: provider intent vs. our copied result</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr09-p11"><h2>Provider scorecard</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr09-p12"><h2>Provider style analysis</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr09-actions"><h2>Actions</h2><div class="tr-panel-body"></div></section>
    `;
  }

  async function load(ctx) {
    const els = {
      sources: ctx.container.querySelector("#tr09-p01 .tr-panel-body"),
      transport: ctx.container.querySelector("#tr09-p02 .tr-panel-body"),
      parser: ctx.container.querySelector("#tr09-p03 .tr-panel-body"),
      rights: ctx.container.querySelector("#tr09-p04 .tr-panel-body"),
      history: ctx.container.querySelector("#tr09-p05 .tr-panel-body"),
      behavior: ctx.container.querySelector("#tr09-p06 .tr-panel-body"),
      funnel: ctx.container.querySelector("#tr09-p07 .tr-panel-body"),
      accountProxy: ctx.container.querySelector("#tr09-p08 .tr-panel-body"),
      correlation: ctx.container.querySelector("#tr09-p09 .tr-panel-body"),
      executionGap: ctx.container.querySelector("#tr09-p10 .tr-panel-body"),
      scorecard: ctx.container.querySelector("#tr09-p11 .tr-panel-body"),
      style: ctx.container.querySelector("#tr09-p12 .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const [providersRes, routingRes, signalsRes, healthRes] = await Promise.all([
      ctx.fetchJSON("/providers"),
      ctx.fetchJSON("/routing-rules"),
      ctx.fetchJSON("/signals?limit=100"),
      ctx.fetchJSON("/health"),
    ]);
    if (providersRes.status === 401 || providersRes.status === 403) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "denied", deniedCode: providersRes.status });
      return;
    }
    if (!providersRes.ok) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "error", message: "Could not load providers." });
      return;
    }

    const providers = (providersRes.data && providersRes.data.providers) || [];
    const routingRules = (routingRes.ok && routingRes.data && routingRes.data.routing_rules) || [];
    const signals = (signalsRes.ok && signalsRes.data && signalsRes.data.signals) || [];

    if (!providers.length) {
      StateMatrix.render(els.sources, {
        state: "empty",
        emptyMessage: "No authorized source is configured.",
        nextRoute: "/trade/sources/new",
        nextLabel: "Source onboarding and parser laboratory (TR-10)",
      });
      for (const key of ["transport", "parser", "rights", "history", "behavior", "funnel", "accountProxy", "correlation", "executionGap", "scorecard", "style"]) {
        StateMatrix.render(els[key], { state: "empty", emptyMessage: "No authorized source is configured." });
      }
      StateMatrix.render(ctx.container.querySelector("#tr09-actions .tr-panel-body"), {
        state: "empty",
        emptyMessage: "No authorized source is configured.",
        nextRoute: "/trade/sources/new",
        nextLabel: "Source onboarding and parser laboratory (TR-10)",
      });
      ctx.setChrome({ asOf: new Date().toISOString() });
      return;
    }

    const lastSeenBySource = new Map();
    for (const s of signals) {
      if (!lastSeenBySource.has(s.source)) lastSeenBySource.set(s.source, s.received_at);
    }

    // Fetched once, unconditionally, and reused by the quality funnel
    // (B4-2), the signal-vs-execution gap panel (C1), and the provider
    // scorecard (C2) below -- never refetched per panel.
    const [ordersRes, excursionsRes, providerValueRes] = await Promise.all([
      ctx.fetchJSON("/orders?limit=500"),
      ctx.fetchJSON("/positions/excursions?limit=500"),
      ctx.fetchJSON("/providers/value"),
    ]);
    const orders = ordersRes.ok && ordersRes.data && Array.isArray(ordersRes.data.orders) ? ordersRes.data.orders : [];
    const excursions = excursionsRes.ok && excursionsRes.data && Array.isArray(excursionsRes.data.excursions) ? excursionsRes.data.excursions : [];
    const providerValueRows = providerValueRes.ok && providerValueRes.data && Array.isArray(providerValueRes.data.providers) ? providerValueRes.data.providers : [];

    // --- Sources table ---
    const sourceRows = providers.map((p) => {
      const mod = transportFor(p.provider_id);
      const hasRule = routingRules.some((r) => r.source === p.provider_id);
      const enabled = p.settings && p.settings.enabled !== false; // null/undefined inherits -> treated as enabled
      const lastSeen = lastSeenBySource.get(p.provider_id);
      const lag = lastSeen ? `${escapeHtml(lastSeen)} (time since last received signal, not a transport lag metric)` : pill("no signal received yet", "muted");
      let status;
      if (!enabled) status = pill("muted (provider-level)", "warn");
      else if (!hasRule) status = pill("admitting but no routing rule -- signals recorded, never routed", "warn");
      else status = pill("admitting and routed", "ok");
      return [
        `<span class="mono" id="tr09-source-${escapeAttr(p.provider_id)}">${escapeHtml(p.provider_id)}</span>`,
        mod ? escapeHtml(mod.transport) : `<span class="tr-not-tracked">not statically mapped</span>`,
        `<span class="tr-not-tracked">not tracked in this build</span>`,
        p.analysts.length ? `${p.analysts.length} analyst(s): ${escapeHtml(p.analysts.map((a) => a.analyst_id).join(", "))}` : pill("no analyst scope (source-wide)", "muted"),
        mod ? (mod.free_text ? "app/sources/text_parser.py (shared grammar)" : "structured payload (no text grammar)") : `<span class="tr-not-tracked">unknown</span>`,
        `<span class="tr-not-tracked">not tracked in this build</span>`,
        lag,
        status,
      ];
    });
    StateMatrix.render(els.sources, {
      state: "ready",
      html: `<p class="section-note">Transport/Parser are inferred from a hand-maintained mirror of this repo's app/sources/*.py list, not a live registry (see this file's own comment) -- Channel/product and Cursor are not tracked per source anywhere in this build.</p>${table(
        ["Source", "Transport", "Channel/product", "Analyst scope", "Parser", "Cursor", "Lag", "Status"],
        sourceRows,
        "No sources."
      )}`,
    });

    // --- Transport health ---
    if (healthRes.ok && healthRes.data) {
      const h = healthRes.data;
      StateMatrix.render(els.transport, {
        state: "unsupported",
        reason:
          `No per-source/transport connectivity telemetry is tracked in this build (no bot heartbeat, no per-collector cursor). ` +
          `The closest real evidence is this service's own worker liveness (public GET /health, service-wide, not per source): ` +
          `status=${escapeHtml(h.status)}, price_monitor_ok=${escapeHtml(String(h.price_monitor_ok))}, reconciler_ok=${escapeHtml(String(h.reconciler_ok))}.`,
      });
    } else {
      StateMatrix.render(els.transport, { state: "unsupported", reason: "No per-source/transport connectivity telemetry is tracked in this build, and the service-wide liveness check could not be reached either." });
    }

    // --- Parser coverage ---
    const freeText = providers.filter((p) => { const m = transportFor(p.provider_id); return m && m.free_text; });
    const structured = providers.filter((p) => { const m = transportFor(p.provider_id); return m && !m.free_text; });
    const unknown = providers.filter((p) => !transportFor(p.provider_id));
    StateMatrix.render(els.parser, {
      state: "ready",
      html: `
        <p class="section-note">Real, code-derived split of configured sources by ingestion parser (see app/sources/*.py). Use "Run parser validation" (TR-10) against real or pasted message text to see actual dispositions -- this panel only shows which grammar family applies, never a fabricated coverage percentage.</p>
        <ul>
          <li>Shared free-text grammar (app/sources/text_parser.py): ${freeText.length ? escapeHtml(freeText.map((p) => p.provider_id).join(", ")) : pill("none configured", "muted")}</li>
          <li>Structured payload sources (no text grammar involved): ${structured.length ? escapeHtml(structured.map((p) => p.provider_id).join(", ")) : pill("none configured", "muted")}</li>
          <li>Not statically mapped (unknown transport): ${unknown.length ? escapeHtml(unknown.map((p) => p.provider_id).join(", ")) : pill("none", "muted")}</li>
        </ul>
        <div class="tr-controls-row"><a href="#/trade/sources/new">Open parser laboratory (TR-10)</a></div>
      `,
    });

    // --- Rights ---
    StateMatrix.render(els.rights, {
      state: "unsupported",
      reason: "No rights/resale-grant model exists anywhere in this codebase (no rights_grant_id field, no commercial-use tracking table) -- there is nothing real to display here.",
    });

    // --- History jobs (real: GET /signals, immutable row ids) ---
    if (!signals.length) {
      StateMatrix.render(els.history, { state: "empty", emptyMessage: "No signal has been received from any source yet." });
    } else {
      const rows = signals.slice(0, 25).map((s) => [
        `<span class="mono">${escapeHtml(String(s.id))}</span>`,
        `<a href="#tr09-source-${escapeAttr(s.source)}">${escapeHtml(s.source)}</a>`,
        escapeHtml(s.symbol),
        escapeHtml(s.side),
        escapeHtml(s.received_at),
        pill("received (immutable record)", "ok"),
      ]);
      StateMatrix.render(els.history, {
        state: "ready",
        html: `<p class="section-note">This build has no separate async "import job" concept -- a received signal is itself the durable, immutable record. Showing the ${Math.min(25, signals.length)} most recent of ${signals.length} loaded (server allows up to 500 via GET /signals?limit=).</p>${table(
          ["Signal ID", "Source", "Symbol", "Side", "Received at", "Status"],
          rows,
          "No signals."
        )}`,
      });
    }

    // --- Actions row (TR-09-A01..A03) ---
    const actionsEl = ctx.container.querySelector("#tr09-actions .tr-panel-body");
    const actionRows = providers.map((p) => {
      const enabled = p.settings && p.settings.enabled !== false;
      return `
        <div class="tr-controls-row">
          <span class="mono">${escapeHtml(p.provider_id)}</span>
          <button type="button" class="danger" data-pause-source="${escapeAttr(p.provider_id)}" ${enabled ? "" : "disabled"}>Pause new admissions</button>
          ${enabled ? "" : `<span class="section-note">Already paused.</span>`}
        </div>
      `;
    }).join("");
    StateMatrix.render(actionsEl, {
      state: "ready",
      html: `
        <div class="tr-controls-row"><a href="#/trade/sources/new">Add source (TR-10)</a> &middot; <a href="#/providers/add">+ Add Signal Provider (Provider/Source/Connection onboarding wizard, TR-17)</a></div>
        <h3 class="section-note" style="margin-top:12px;">Pause new admissions (TR-09-A03)</h3>
        <p class="section-note">Sets this exact provider's own enabled=false via the same, already-tested POST /providers/{provider_id} upsert -- no other provider, analyst, or account setting is changed. Existing routing/exit behavior for open positions is unaffected.</p>
        ${actionRows}
      `,
    });
    actionsEl.querySelectorAll("[data-pause-source]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const providerId = btn.getAttribute("data-pause-source");
        const provider = providers.find((p) => p.provider_id === providerId);
        if (!provider) return;
        if (!confirm(`Pause new admissions for source "${providerId}"? This does not affect any existing position or order.`)) return;
        try {
          await postJSON(`/providers/${encodeURIComponent(providerId)}`, {
            display_name: provider.display_name || "",
            multiplier: provider.settings.multiplier,
            fixed_quantity: provider.settings.fixed_quantity,
            managed_lifecycle: provider.settings.managed_lifecycle,
            enabled: false,
          });
        } catch (err) {
          alert(`Could not pause source: ${err.message}`);
        }
        await load(ctx);
      });
    });

    // ================================================================
    // Phase B4/B5: provider behavior / quality funnel / account proxy /
    // correlation -- all built from data this load() already has, plus
    // GET /orders, GET /positions/excursions and a bounded number of
    // GET /positions/.../stop-events calls (see module docstring).
    // ================================================================

    // --- B4-1: Provider behavior charts ---
    if (!signals.length) {
      StateMatrix.render(els.behavior, { state: "empty", emptyMessage: "No signals have been received yet -- nothing real to chart." });
    } else {
      const byProvider = computeProviderBehavior(signals);
      const providerIds = [...byProvider.keys()];
      StateMatrix.render(els.behavior, {
        state: "ready",
        html: `
          <p class="section-note" style="margin-top:0;">Computed client-side from the same ${signals.length} most-recently-received signals "History jobs" above shows (GET /signals?limit=100) -- real counts, grouped by provider, never padded to look fuller.</p>
          <div class="tr-chart-grid" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:16px;">
            <div><h3 class="section-note">Signals per day, by provider</h3><div id="tr09-perday-wrap"></div></div>
            <div><h3 class="section-note">Long vs short mix, by provider</h3><div id="tr09-longshort-wrap"></div></div>
            <div><h3 class="section-note">Asset-class mix, by provider</h3><div id="tr09-assetclass-wrap"></div></div>
            <div>
              <h3 class="section-note">Instrument concentration</h3>
              <label>Provider
                <select id="tr09-instrument-provider">
                  ${providerIds.map((p) => `<option value="${escapeAttr(p)}">${escapeHtml(p)}</option>`).join("")}
                </select>
              </label>
              <div id="tr09-instrument-wrap"></div>
            </div>
          </div>
        `,
      });
      renderPerDayChart(els.behavior.querySelector("#tr09-perday-wrap"), byProvider);
      renderStackedMixChart(els.behavior.querySelector("#tr09-longshort-wrap"), "tr09-longshort-chart", "longShort", byProvider, "side", ["buy", "sell"]);
      renderStackedMixChart(els.behavior.querySelector("#tr09-assetclass-wrap"), "tr09-assetclass-chart", "assetClass", byProvider, "assetClass", null);
      const instrumentSelect = els.behavior.querySelector("#tr09-instrument-provider");
      const instrumentWrap = els.behavior.querySelector("#tr09-instrument-wrap");
      renderInstrumentChart(instrumentWrap, byProvider, providerIds[0]);
      instrumentSelect.addEventListener("change", () => {
        renderInstrumentChart(instrumentWrap, byProvider, instrumentSelect.value);
      });
    }

    // --- B4-2: Provider quality funnel ---
    if (!signals.length) {
      StateMatrix.render(els.funnel, { state: "empty", emptyMessage: "No signals have been received yet -- nothing real to build a funnel from." });
    } else {
      const { byProvider: funnelByProvider, truncated, pairsConsidered, pairsQueried } = await computeQualityFunnel(ctx, signals, orders, excursions);
      const truncationNote = truncated
        ? ` Only ${pairsQueried} of ${pairsConsidered} distinct account/symbol pairs among this page's filled orders were queried for stop-events (capped, to bound how many requests one page load makes) -- "protected"/"closed" undercounts the true total when this note appears.`
        : "";
      StateMatrix.render(els.funnel, {
        state: "ready",
        html: `
          <p class="section-note" style="margin-top:0;">Real join, per provider: received (a signal row exists) -> order created (a real GET /orders row references it) -> filled (that order's status is "filled") -> protected (Phase A4's real STOP_PLACED event exists for that filled order's account/symbol, at or after its own executed_at) -> closed (that account/symbol pair appears in Phase A1's real closed-position excursions). "Protected"/"closed" are keyed by account+symbol, not this exact signal alone -- two signals sharing an account/symbol can both show the same real stop/close event.${truncationNote}</p>
          <div id="tr09-funnel-wrap"></div>
        `,
      });
      renderFunnelChart(els.funnel.querySelector("#tr09-funnel-wrap"), funnelByProvider);
    }

    // --- B4-3: Account performance proxy (ONLY clean 1:1 mappings) ---
    destroyEquityCharts();
    {
      const mapping = routingMapping(routingRules);
      const qualified = [];
      const unsupportedEntries = [];
      for (const p of providers) {
        const result = mapping.forProvider(p.provider_id);
        if (result.clean) qualified.push({ providerId: p.provider_id, accountId: result.accountId });
        else unsupportedEntries.push({ providerId: p.provider_id, reason: result.reason });
      }
      const unsupportedHtml = unsupportedEntries.length
        ? `<div style="margin-top:12px;"><h3 class="section-note">Not attributable to any single account</h3>${table(
            ["Provider", "Reason"],
            unsupportedEntries.map((e) => [`<span class="mono">${escapeHtml(e.providerId)}</span>`, escapeHtml(e.reason)]),
            "No providers."
          )}</div>`
        : "";
      if (!qualified.length) {
        StateMatrix.render(els.accountProxy, {
          state: "unsupported",
          reason: "No provider currently has a real, verified clean 1:1 routing mapping to exactly one destination account (see the table below for why each configured provider is disqualified) -- this codebase has no per-provider equity attribution model to fall back to.",
        });
        if (unsupportedEntries.length) {
          els.accountProxy.innerHTML += unsupportedHtml;
        }
      } else {
        const [equityResults, statsResults] = await Promise.all([
          Promise.all(qualified.map((q) => ctx.fetchJSON(`/accounts/${encodeURIComponent(q.accountId)}/equity-history?limit=1000`))),
          Promise.all(qualified.map((q) => ctx.fetchJSON(`/accounts/${encodeURIComponent(q.accountId)}/statistics`))),
        ]);
        const cardsHtml = qualified
          .map((q, i) => {
            const snapshots = equityResults[i].ok && equityResults[i].data ? equityResults[i].data.snapshots || [] : [];
            const statsData = statsResults[i].ok && statsResults[i].data ? statsResults[i].data : null;
            const canvasId = `tr09-equity-chart-${q.accountId.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
            const statsRows = statsData
              ? [
                  ["Sample count", fmtNum(statsData.sample_count)],
                  ["Mean P&L delta", statsData.mean_pnl_delta === null ? pill("insufficient history", "muted") : fmtNum(statsData.mean_pnl_delta)],
                  ["Volatility (P&L delta)", statsData.volatility_pnl_delta === null ? pill("insufficient history", "muted") : fmtNum(statsData.volatility_pnl_delta)],
                  ["Sharpe-equivalent", statsData.sharpe_equivalent === null ? pill("insufficient history", "muted") : fmtNum(statsData.sharpe_equivalent)],
                  ["Sortino-equivalent", statsData.sortino_equivalent === null ? pill("insufficient history", "muted") : fmtNum(statsData.sortino_equivalent)],
                  ["Max drawdown", statsData.max_drawdown === null ? pill("insufficient history", "muted") : fmtNum(statsData.max_drawdown)],
                ]
              : [];
            return `
              <div class="tr-panel" style="margin-top:12px;">
                <h3 class="section-note" style="margin-top:0;">Provider "${escapeHtml(q.providerId)}" -- proxy via its one, exclusively-routed account "${escapeHtml(q.accountId)}"</h3>
                <p class="section-note">This is account "${escapeHtml(q.accountId)}"'s own real cumulative P&amp;L/statistics (Phase A3/A5) -- shown here only because a real, verified routing check confirmed this provider's signals go to this one account and no other provider shares it. It is not renamed to "the provider's own performance."</p>
                ${
                  snapshots.length >= 2
                    ? `<div class="chart-container"><canvas id="${canvasId}"></canvas></div>`
                    : `<div class="empty">Fewer than 2 real equity snapshots exist yet for this account -- no real curve to chart.</div>`
                }
                ${statsRows.length ? table(["Statistic", "Value"], statsRows, "No statistics.") : ""}
              </div>
            `;
          })
          .join("");
        StateMatrix.render(els.accountProxy, { state: "ready", html: cardsHtml + unsupportedHtml });
        qualified.forEach((q, i) => {
          const snapshots = equityResults[i].ok && equityResults[i].data ? equityResults[i].data.snapshots || [] : [];
          if (snapshots.length < 2) return;
          const canvasId = `tr09-equity-chart-${q.accountId.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
          const canvas = els.accountProxy.querySelector(`#${canvasId}`);
          if (!canvas) return;
          const chart = new Chart(canvas.getContext("2d"), {
            type: "line",
            data: {
              labels: snapshots.map((s) => s.captured_at),
              datasets: [
                {
                  label: `${q.accountId} cumulative P&L`,
                  data: snapshots.map((s) => s.cumulative_pnl),
                  borderColor: "#3ddc84",
                  backgroundColor: "rgba(61, 220, 132, 0.15)",
                  fill: true,
                  tension: 0,
                  pointRadius: 2,
                },
              ],
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: false } } },
          });
          equityCharts.set(canvasId, chart);
        });
      }
    }

    // --- B5: Correlation between any two accounts ---
    {
      const accountsRes = await ctx.fetchJSON("/accounts");
      const accounts = accountsRes.ok && accountsRes.data && Array.isArray(accountsRes.data.accounts) ? accountsRes.data.accounts : [];
      if (accounts.length < 2) {
        StateMatrix.render(els.correlation, {
          state: "unsupported",
          reason: `Correlation needs at least 2 destination accounts to compare -- only ${accounts.length} configured. A network-graph/dendrogram visualization is not built either way (it needs 3+ real correlated series to be more informative than a single number; with likely only 1-2 real demo accounts here, that visual would be misleading overkill even once a 2nd account exists).`,
        });
      } else {
        const optionsHtml = accounts.map((a) => `<option value="${escapeAttr(a.account_id)}">${escapeHtml(a.account_id)}</option>`).join("");
        StateMatrix.render(els.correlation, {
          state: "ready",
          html: `
            <p class="section-note" style="margin-top:0;">Real Pearson correlation (Phase A5, GET /accounts/correlation) between two accounts' own cumulative_pnl snapshot series, matched by overlapping captured_at timestamps. Used as a real proxy for provider/strategy correlation only where each account's own routing is itself unambiguous (see the account-performance-proxy panel above) -- this panel compares accounts directly, not providers, and never fabricates a 0 when there is too little real overlapping data.</p>
            <div class="tr-controls-row">
              <label>Account A <select id="tr09-corr-a">${optionsHtml}</select></label>
              <label>Account B <select id="tr09-corr-b">${optionsHtml}</select></label>
              <button type="button" id="tr09-corr-run">Compute correlation</button>
            </div>
            <div id="tr09-corr-result"></div>
          `,
        });
        const selectA = els.correlation.querySelector("#tr09-corr-a");
        const selectB = els.correlation.querySelector("#tr09-corr-b");
        if (accounts.length > 1) selectB.value = accounts[1].account_id;
        const resultEl = els.correlation.querySelector("#tr09-corr-result");

        async function runCorrelation() {
          const a = selectA.value;
          const b = selectB.value;
          if (a === b) {
            resultEl.innerHTML = `<div class="empty">Pick two different accounts.</div>`;
            return;
          }
          resultEl.innerHTML = `<div class="sm-state sm-state-loading">Computing…</div>`;
          const res = await ctx.fetchJSON(`/accounts/correlation?account_a=${encodeURIComponent(a)}&account_b=${encodeURIComponent(b)}`);
          if (!res.ok || !res.data) {
            resultEl.innerHTML = `<div class="empty">Could not compute correlation.</div>`;
            return;
          }
          const d = res.data;
          if (d.correlation === null || d.correlation === undefined) {
            resultEl.innerHTML = `<div class="empty">Insufficient overlapping data: only ${d.sample_count} real overlapping snapshot(s) between "${escapeHtml(a)}" and "${escapeHtml(b)}" -- never shown as a fabricated 0.</div>`;
          } else {
            resultEl.innerHTML = `<p><strong>${fmtNum(d.correlation)}</strong> Pearson correlation over ${d.sample_count} real overlapping snapshots between "${escapeHtml(a)}" and "${escapeHtml(b)}".</p><p class="section-note">${escapeHtml(d.note)}</p>`;
          }
        }
        els.correlation.querySelector("#tr09-corr-run").addEventListener("click", runCorrelation);
        // Real initial run against the default pair -- not a fabricated
        // placeholder result while the panel waits for a click.
        await runCorrelation();
      }
    }

    // --- C1: Signal vs. execution gap (real, see module docstring) ---
    {
      const gapByProvider = computeSignalExecutionGap(signals, orders);
      renderSignalExecutionGap(els.executionGap, gapByProvider);
    }

    // --- C2: Provider scorecard (real, see module docstring) ---
    {
      const providerValueBySource = aggregateProviderValueBySource(providerValueRows);
      const mappingForScorecard = routingMapping(routingRules);
      renderProviderScorecard(els.scorecard, providers, signals, providerValueBySource, mappingForScorecard);
    }

    // --- C3: Provider style analysis (real, see module docstring) ---
    {
      if (!signals.length) {
        StateMatrix.render(els.style, { state: "empty", emptyMessage: "No signals have been received yet -- nothing real to chart." });
      } else {
        const styleByProvider = computeStyleDistributions(signals);
        const holdingDurationsSeconds = computeHoldingDurations(orders);
        renderStyleAnalysis(els.style, styleByProvider, holdingDurationsSeconds);
      }
    }

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr09 = {
    title: "Signal providers and collectors",
    breadcrumb: "Trade / Sources",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = shell();
      await load(ctx);
      ctx.registerPoll("tr09", 10000, () => load(ctx));
    },
  };
  Router.register("/trade/sources", "tr09");
})();
