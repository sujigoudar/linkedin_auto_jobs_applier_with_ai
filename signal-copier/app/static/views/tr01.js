/* TR-01: Trading command center (`#/trade`).
 *
 * Real backing data, reusing existing endpoints (no new backend route --
 * see this batch's report for why: every panel below is answerable from
 * data app/main.py already exposes):
 *   - Identity/environment, Safety summary: GET /health
 *   - Account risk cards: GET /accounts + GET /accounts/{id}/balance
 *     (M-TR-01-01 "Verified net liquidation" = AccountBalance.equity, the
 *     broker's own live figure -- never summed across accounts with
 *     different brokers/currencies, since AccountBalance has no currency
 *     field to prove they match; shown per-account instead)
 *   - P&L and exposure: GET /accounts/{id}/economics (realized P&L per
 *     account) + GET /positions (open exposure count)
 *   - Priority incidents: GET /positions' managed_lifecycles[].halted
 *     (a real, code-computed halt flag from CloseArbiter -- not invented)
 *   - Recent activity: GET /orders
 *   - Open positions: GET /positions' real open positions, enriched with
 *     GET /positions' own managed_lifecycles[].mae/mfe (PU-A1, already
 *     landed) for the subset that are managed-lifecycle positions --
 *     "n/a" (not "0") for a plain-account position or one with no real
 *     price observation yet, never a fabricated excursion.
 *   - Allocation donuts (asset class / broker / account): real open
 *     positions from GET /positions, grouped by asset class (joined from
 *     GET /signals by symbol -- see `assetClassMapFromSignals` below for
 *     the honest caveat), by account's broker (GET /accounts), and by
 *     account_id directly. The metric charted is a real, exact OPEN
 *     POSITION COUNT per bucket -- never a dollar exposure, since neither
 *     `positions` nor `signals` stores a current market price to compute
 *     one from (see app/db.py's `positions` table: account_id, symbol,
 *     net_quantity, updated_at -- no price column at all).
 *
 * M-TR-01-02 "Reserved risk" has NO backing data anywhere in this
 * codebase (no committed/reserved-risk ledger is exposed by any endpoint
 * -- app/capital_allocator.py's own reservations are internal, not
 * surfaced via any GET route) -- rendered "unsupported" honestly rather
 * than invented.
 *
 * Deliberately NOT built in this batch (see this batch's report for the
 * full list and exactly what each one is missing):
 *   - Equity curve, P&L curve, underwater/drawdown curve, daily P&L bars,
 *     exposure history: all need a real persisted equity/balance time
 *     series. `GET /accounts/{id}/balance` and `/economics` only ever
 *     expose a CURRENT/aggregate value (see /economics' own docstring:
 *     "never a simulated equity curve") -- there is no Phase A3
 *     equity/returns history snapshot table yet to chart from.
 *   - Capital state (stacked bar/area): same gap -- no persisted
 *     cash/margin/reserved-risk time series exists either.
 *   - P&L contribution waterfall: needs a real fees/slippage/financing
 *     cost-component breakdown. app/economics.py's own module docstring
 *     says P&L is reported gross of fees (no fee column anywhere in
 *     `orders`), and there is no slippage or financing figure tracked
 *     anywhere in this build -- a waterfall built from just one real
 *     number (realized P&L) with fabricated cost splits would misstate
 *     where P&L actually came from.
 *   - Risk heatmap: needs a real risk-utilization-by-account/asset-class
 *     figure (e.g. gross or notional exposure against some real capacity
 *     ceiling, per account x asset class). This build tracks quantities,
 *     not notional/market-value exposure (no price column on `positions`),
 *     and app/capital_allocator.py's reservations are process-internal,
 *     not surfaced via any GET endpoint -- there is no real utilization
 *     ratio to compute a heatmap cell from.
 *
 * Phase B8 (additive, new "Risk" panel -- p09 -- everything above this
 * point is unchanged Phase B1 content):
 *   - Rolling volatility / Sharpe-equivalent / Sortino-equivalent / max
 *     drawdown (+ duration): real, per account, from
 *     GET /accounts/{id}/statistics (app/statistics.py, Phase A5 --
 *     already landed, already enforces its own honest minimum-sample
 *     thresholds). Every field renders "n/a" -- NEVER a fabricated 0 --
 *     exactly when that endpoint itself returns `null` for that field,
 *     because the account's real snapshot history is too short. This
 *     view does not re-implement or second-guess A5's thresholds; it
 *     only renders whatever the endpoint honestly reports.
 *   - Sharpe-equivalent / Sortino-equivalent are labeled EXACTLY as A5
 *     itself labels them (see app/statistics.py's own docstring: implicit
 *     zero risk-free rate, since this codebase stores no risk-free-rate
 *     figure anywhere -- never presented as a real "Sharpe ratio").
 *   - Cross-account correlation: real Pearson correlation per real
 *     account pair, from GET /accounts/correlation (app/statistics.py's
 *     `compute_pairwise_correlation`, Phase A5) -- "n/a" when that pair
 *     has fewer than `MIN_CORRELATION_SAMPLES` real overlapping equity
 *     snapshots (the endpoint's own honest floor), never a fabricated
 *     0/NaN-as-zero. Only rendered when 2+ accounts exist (a single
 *     account has no pair to correlate against).
 *
 * Deliberately NOT built in Phase B8 -- two DIFFERENT reasons, worth
 * telling apart for anyone later deciding whether/how to merge branches:
 *   - VaR / Expected Shortfall: NO REAL DATA MODEL EXISTS ANYWHERE IN
 *     THIS CODEBASE for either. Both need a real returns/P&L-delta
 *     DISTRIBUTION model (a real historical or parametric distribution
 *     to take a real quantile of) -- this build has only the same raw
 *     `cumulative_pnl` snapshot series app/statistics.py already exposes
 *     as mean/stdev, and no VaR/ES computation of any kind anywhere in
 *     this repo to reuse. Building one from scratch for this panel would
 *     be inventing a whole new statistical model, not charting an
 *     existing real figure -- out of scope for an additive dashboard
 *     slice.
 *   - Real-time drawdown circuit-breaker state (PAUSE_NEW_ENTRIES /
 *     REQUIRE_REVIEW) and persisted placement-rate-limit headroom: THESE
 *     ARE REAL AND ALREADY BUILT, but on a SEPARATE, NOT-YET-MERGED
 *     branch (`claude/signal-copier-safety-features` --
 *     app/drawdown_governor.py, app/placement_rate_limiter.py). That
 *     branch does not exist on this one; `import app.drawdown_governor`
 *     or `app.placement_rate_limiter` would 404/ImportError here. This is
 *     a deliberate separate integration decision (see this batch's task
 *     brief), not a data gap -- do not build a placeholder panel that
 *     assumes either module's state.
 *   - Liquidity-risk heatmap: NO REAL DATA MODEL EXISTS ANYWHERE IN THIS
 *     CODEBASE -- no bid/ask spread, order-book depth, or any other
 *     liquidity figure is tracked or exposed by any endpoint.
 *
 * Phase C (this batch, additive -- 2026-09 design review: "Command Center
 * should become the most important screen ... the ingredients are correct
 * but the hierarchy is wrong"): a first-viewport KPI band + "Attention
 * required" queue (Components.renderKPIBand / renderAttentionQueue, new
 * shared components) ABOVE everything built in Phase B1/B8, plus a
 * notional-exposure/loss-at-stop panel (p10) supplementing (never
 * replacing -- see `test_tr01_allocation_donuts.py`'s load-bearing exact
 * position-COUNT assertions) the existing count donuts. Every figure below
 * is either a real number already computed elsewhere in this file/this
 * codebase, or an honest `Components.renderCapabilityState` "not tracked"
 * -- nothing here is invented:
 *   - Trading mode: `GET /system/info`'s real `standby_mode` (app/config.py
 *     -- true means the process serves GET/HEAD/OPTIONS only, no order can
 *     reach the engine, regardless of any individual route's own logic).
 *   - Active accounts / Net liquidation / Deployed+Reserved capital: same
 *     real `GET /accounts` (`enabled`) and `GET /capital-allocation`
 *     (`deployed_notional`/`reserved_notional`, Phase B7) this file already
 *     reads elsewhere. Net liquidation is deliberately NOT summed across
 *     accounts here either -- same M-TR-01-01 currency-normalization gap
 *     already documented above -- rendered `not_tracked` via
 *     Components.renderCapabilityState instead of a misleading total.
 *   - Day P&L / Total P&L: Total P&L sums each account's real
 *     `GET /accounts/{id}/economics` `realized_pnl` (only when every
 *     account's read succeeded, else `not_tracked`). Day P&L sums each
 *     account's real `GET /accounts/{id}/equity-history` `cumulative_pnl`
 *     change over its OWN real snapshots spanning the last ~24h (never a
 *     shorter, silently-mislabeled window) -- an account with no real 24h
 *     of persisted snapshot history yet is honestly excluded, and the
 *     whole tile renders `not_tracked` if no account qualifies. Same
 *     unverified-shared-currency caveat as Net liquidation, disclosed once
 *     in a shared note rather than repeated per tile.
 *   - Open risk / Unprotected exposure: BOTH read the exact same per-
 *     lifecycle computation (`computeLifecycleRiskRows`, shared with the
 *     new p10 loss-at-stop panel, so the arithmetic is never duplicated).
 *     Unprotected exposure = real `uncovered_quantity` count/quantity
 *     across managed-lifecycle positions (same field this file's existing
 *     M-TR-01-03 "Protection deficit" already reads). Open risk = real
 *     `covered_quantity * abs(entry_price - stop_price)` summed ONLY over
 *     lifecycles that are fully covered, stop-confirmed, with both prices
 *     known -- `not_tracked` when zero lifecycles qualify (a real "$0"
 *     would be indistinguishable from "unknown," which this codebase never
 *     allows). Scope is managed-lifecycle positions only, same as
 *     M-TR-01-03 -- a plain-account position's protection state isn't
 *     tracked anywhere in this build (see PU-A1 note above).
 *   - Unknown orders: the exact same "unresolved pending_exit/pending_entry"
 *     signal as this file's existing M-TR-01-04.
 *   - Critical incidents: real halted-lifecycle count -- the same signal
 *     TR-13 labels "high" severity (halt is a real PositionCloseArbiter
 *     decision, not a UI-invented severity).
 *   - Attention queue: critical rows for each halted / uncovered managed
 *     lifecycle (same real fields as above); warning rows for a provider
 *     whose `GET /signals`-derived last-received-signal age exceeds a
 *     UI-chosen staleness threshold (the AGE itself is real -- TR-09
 *     already renders this same timestamp as "Lag" -- the THRESHOLD for
 *     when to surface it is this panel's own judgment call, documented
 *     inline); info rows for a real count of `GET /orders` status=pending.
 *     Deliberately NOT built: a broker-vs-internal-ledger discrepancy row
 *     -- TR-13's own docstring already establishes this build exposes no
 *     live broker-side position readback via any GET endpoint (store-side
 *     coverage only), so this condition type is omitted rather than
 *     fabricated.
 */
(function () {
  "use strict";

  function panelShell() {
    return `
      <section class="tr-panel" id="tr01-kpi"><h2>At a glance</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-attention"><h2>Attention required</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p01"><h2>Identity / environment</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p02"><h2>Safety summary</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p03"><h2>Account risk cards</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p04"><h2>P&amp;L and exposure</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p05"><h2>Priority incidents</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p06"><h2>Recent activity</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p07"><h2>Open positions</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p08"><h2>Allocation</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p10"><h2>Exposure by notional / loss at stop</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p09"><h2>Risk</h2><div class="tr-panel-body"></div></section>
    `;
  }

  // Fixed categorical palette, dark-surface-validated (dataviz skill's
  // validate_palette.js, --mode dark --surface #141926, against this
  // app's own --panel token) -- ALL CHECKS PASS for these 6 steps.
  // Color follows the ENTITY, never its rank: asset class gets a fixed
  // hue per class below; broker/account get a stable hue by sorted-label
  // order (there is no fixed universe of brokers/accounts to hang a
  // permanent identity mapping off of the way there is for AssetClass).
  const DONUT_PALETTE = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"];
  const DONUT_OTHER_COLOR = "#8b93a7"; // var(--muted) -- "Other"/unknown bucket, never a categorical slot

  const ASSET_CLASS_COLOR = {
    crypto: DONUT_PALETTE[0],
    forex: DONUT_PALETTE[1],
    equity: DONUT_PALETTE[2],
    option: DONUT_PALETTE[3],
    future: DONUT_PALETTE[4],
  };

  // Real (symbol -> asset_class) map from GET /signals, newest-first --
  // the FIRST match per symbol is kept (i.e. the most recent signal for
  // that symbol), since `signals` carries no account_id to join on
  // directly (routing decides destinations; the signal itself doesn't
  // know which accounts it went to) and a symbol's real-world asset
  // class does not change between signals. An open position whose symbol
  // never appears in the queried signal window (older than `limit`, or a
  // position seeded by some other path) has NO reliable asset class here
  // -- it is bucketed "unknown" honestly, never guessed.
  function assetClassMapFromSignals(signals) {
    const map = new Map();
    for (const s of signals) {
      if (!map.has(s.symbol) && s.asset_class) map.set(s.symbol, s.asset_class);
    }
    return map;
  }

  // Groups `items` by `keyFn(item)` into an exact count per label, sorted
  // by count desc (ties broken alphabetically for a stable render order
  // across polls). Beyond `maxSlots` distinct labels, the smallest
  // remainder folds into "Other" (dataviz anti-pattern: never a generated
  // hue past the validated slot count).
  function aggregateCounts(items, keyFn, maxSlots) {
    const counts = new Map();
    for (const item of items) {
      const key = keyFn(item);
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    const entries = Array.from(counts.entries()).sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1));
    if (entries.length <= maxSlots) return entries;
    const kept = entries.slice(0, maxSlots - 1);
    const rest = entries.slice(maxSlots - 1).reduce((sum, [, n]) => sum + n, 0);
    kept.push(["Other", rest]);
    return kept;
  }

  // Renders a statistics field EXACTLY as GET /accounts/{id}/statistics
  // reports it -- `pill("n/a", "muted")` when the backend itself sent
  // `null` (insufficient real sample history, per app/statistics.py's own
  // documented thresholds), NEVER a fabricated 0/"—". This is the
  // load-bearing rendering rule for the whole Risk panel: no default-to-
  // zero fallback anywhere below.
  function statVal(v) {
    return v === null || v === undefined ? pill("n/a", "muted") : fmtNum(v);
  }

  // Real wall-clock drawdown duration (seconds -> "Xd Yh Zm"), honoring
  // the same null-means-unsupported rule as statVal above.
  function fmtDuration(seconds) {
    if (seconds === null || seconds === undefined) return pill("n/a", "muted");
    let s = Math.round(seconds);
    const days = Math.floor(s / 86400);
    s -= days * 86400;
    const hours = Math.floor(s / 3600);
    s -= hours * 3600;
    const minutes = Math.floor(s / 60);
    const parts = [];
    if (days) parts.push(`${days}d`);
    if (hours || days) parts.push(`${hours}h`);
    parts.push(`${minutes}m`);
    return parts.join(" ");
  }

  const donutCharts = {}; // canvasId -> live Chart.js instance (destroy-and-recreate)

  function renderDonut(container, canvasId, entries, colorFn, totalLabel) {
    if (donutCharts[canvasId]) {
      donutCharts[canvasId].destroy();
      delete donutCharts[canvasId];
    }
    const wrap = container.querySelector(`#${canvasId}-wrap`);
    if (!wrap) return;
    if (!entries.length) {
      wrap.innerHTML = `<div class="empty">No open positions to allocate.</div>`;
      return;
    }
    const total = entries.reduce((sum, [, n]) => sum + n, 0);
    const labels = entries.map(([label]) => label);
    const values = entries.map(([, n]) => n);
    const colors = entries.map(([label]) => colorFn(label));
    const rows = entries.map(([label, n]) => [
      escapeHtml(label),
      fmtNum(n),
      `${((n / total) * 100).toFixed(1)}%`,
    ]);
    wrap.innerHTML = `<div class="chart-container" style="height:220px;"><canvas id="${canvasId}"></canvas></div>
      ${table(["Bucket", "Open positions", "Share"], rows, "No open positions.")}`;
    donutCharts[canvasId] = new Chart(wrap.querySelector(`canvas#${canvasId}`).getContext("2d"), {
      type: "doughnut",
      data: { labels, datasets: [{ data: values, backgroundColor: colors, borderColor: "#141926", borderWidth: 2 }] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "bottom", labels: { color: "#e6e9f0", boxWidth: 12, font: { size: 11 } } },
          tooltip: {
            callbacks: {
              label: (ctxItem) => {
                const n = ctxItem.parsed;
                return ` ${ctxItem.label}: ${n} (${((n / total) * 100).toFixed(1)}%) of ${total} ${totalLabel}`;
              },
            },
          },
        },
      },
    });
  }

  // A donut of real notional exposure (quantity * entry price), not a raw
  // position count -- the dataviz review's own complaint ("four positions
  // consisting of $50,000 BTC, $10,000 AAPL and two $100 positions should
  // not visually look like 50/25/25 exposure"). Separate canvas/instance
  // from renderDonut's count donuts above -- this SUPPLEMENTS them (see
  // test_tr01_allocation_donuts.py's load-bearing exact-count assertions
  // on those, which this never touches), it does not replace them.
  function renderNotionalDonut(container, canvasId, entries, colorFn) {
    if (donutCharts[canvasId]) {
      donutCharts[canvasId].destroy();
      delete donutCharts[canvasId];
    }
    const wrap = container.querySelector(`#${canvasId}-wrap`);
    if (!wrap) return;
    if (!entries.length) {
      wrap.innerHTML = `<div class="empty">No open position has a real, known notional to chart (see coverage note above).</div>`;
      return;
    }
    const total = entries.reduce((sum, [, n]) => sum + n, 0);
    const labels = entries.map(([label]) => label);
    const values = entries.map(([, n]) => n);
    const colors = entries.map(([label]) => colorFn(label));
    const rows = entries.map(([label, n]) => [
      escapeHtml(label),
      fmtNum(n),
      `${((n / total) * 100).toFixed(1)}%`,
    ]);
    wrap.innerHTML = `<div class="chart-container" style="height:220px;"><canvas id="${canvasId}"></canvas></div>
      ${table(["Bucket", "Notional (entry price x quantity)", "Share"], rows, "No priced open positions.")}`;
    donutCharts[canvasId] = new Chart(wrap.querySelector(`canvas#${canvasId}`).getContext("2d"), {
      type: "doughnut",
      data: { labels, datasets: [{ data: values, backgroundColor: colors, borderColor: "#141926", borderWidth: 2 }] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "bottom", labels: { color: "#e6e9f0", boxWidth: 12, font: { size: 11 } } },
          tooltip: {
            callbacks: {
              label: (ctxItem) => {
                const n = ctxItem.parsed;
                return ` ${ctxItem.label}: ${fmtNum(n)} (${((n / total) * 100).toFixed(1)}%) of ${fmtNum(total)} total notional`;
              },
            },
          },
        },
      },
    });
  }

  let riskBarChart = null; // destroy-and-recreate, same convention as donutCharts above

  // Aggregate real loss-at-stop (see computeLifecycleRiskRows below) by
  // asset class -- shares the exact same per-lifecycle figures the Open
  // risk KPI sums, never a second computation of the arithmetic.
  function renderRiskBarChart(container, canvasId, entries) {
    if (riskBarChart) {
      riskBarChart.destroy();
      riskBarChart = null;
    }
    const wrap = container.querySelector(`#${canvasId}-wrap`);
    if (!wrap) return;
    if (!entries.length) {
      wrap.innerHTML = `<div class="empty">No managed-lifecycle position currently has a fully covered, stop-confirmed, priced loss-at-stop to chart.</div>`;
      return;
    }
    wrap.innerHTML = `<div class="chart-container" style="height:${Math.max(140, entries.length * 40)}px;"><canvas id="${canvasId}"></canvas></div>`;
    const labels = entries.map(([label]) => label);
    const values = entries.map(([, n]) => n);
    riskBarChart = new Chart(wrap.querySelector(`canvas#${canvasId}`).getContext("2d"), {
      type: "bar",
      data: {
        labels,
        datasets: [{ label: "Loss at stop", data: values, backgroundColor: "rgba(255, 93, 59, 0.55)", borderColor: "#ff5d3b", borderWidth: 1 }],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctxItem) => ` ${fmtNum(ctxItem.parsed.x)} loss at stop` } },
        },
        scales: { x: { title: { display: true, text: "Loss at stop (covered_quantity x |entry - stop|)" } } },
      },
    });
  }

  // Sums `valueFn(item)` per `keyFn(item)` bucket, sorted desc, folding the
  // smallest remainder past `maxSlots` into "Other" -- the notional/risk
  // analogue of `aggregateCounts` above (same anti-pattern avoidance: never
  // a generated hue/bucket past the validated slot count).
  function aggregateSum(items, keyFn, valueFn, maxSlots) {
    const sums = new Map();
    for (const item of items) {
      const key = keyFn(item);
      sums.set(key, (sums.get(key) || 0) + valueFn(item));
    }
    const entries = Array.from(sums.entries()).sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1));
    if (entries.length <= maxSlots) return entries;
    const kept = entries.slice(0, maxSlots - 1);
    const rest = entries.slice(maxSlots - 1).reduce((sum, [, n]) => sum + n, 0);
    kept.push(["Other", rest]);
    return kept;
  }

  // The ONE real per-lifecycle protection/risk computation this whole
  // Phase C section shares -- the Open risk KPI, the Unprotected exposure
  // KPI, the attention queue's unprotected rows, and the p10 notional/
  // loss-at-stop panel all read from this, never re-deriving the
  // arithmetic. Only considers OPEN managed-lifecycle positions
  // (owned_quantity > 0) -- a plain-account position has no protection
  // state tracked anywhere in this build (see PU-A1 note above), so it is
  // out of scope here exactly as it already is for M-TR-01-03.
  function computeLifecycleRiskRows(lifecycles) {
    return lifecycles
      .filter((l) => l.owned_quantity > 0)
      .map((l) => {
        const hasEntry = l.entry_price !== null && l.entry_price !== undefined;
        const hasStopPrice = l.stop_price !== null && l.stop_price !== undefined;
        const stopConfirmed = l.stop_status === "stop_confirmed";
        const unprotected = l.uncovered_quantity > 0;
        const notional = hasEntry ? l.owned_quantity * l.entry_price : null;
        const uncoveredNotional = unprotected && hasEntry ? l.uncovered_quantity * l.entry_price : null;
        let riskAtStop = null;
        if (!unprotected && stopConfirmed && hasEntry && hasStopPrice && l.covered_quantity > 0) {
          riskAtStop = l.covered_quantity * Math.abs(l.entry_price - l.stop_price);
        }
        return {
          account_id: l.account_id,
          symbol: l.symbol,
          owned_quantity: l.owned_quantity,
          uncovered_quantity: l.uncovered_quantity,
          unprotected,
          notional,
          uncoveredNotional,
          riskAtStop,
          riskUnknown: !unprotected && !(riskAtStop !== null),
        };
      });
  }

  // A UI-chosen staleness threshold applied to a REAL timestamp (time since
  // this source's last received signal, GET /signals -- the exact same
  // figure TR-09 already renders as its honestly-labeled "Lag," not a
  // transport/collector lag metric). The threshold itself is a judgment
  // call this panel makes, not fabricated telemetry -- no per-source
  // heartbeat/SLA config exists anywhere in this codebase to read one from.
  const STALE_SOURCE_THRESHOLD_SECONDS = 15 * 60;

  // Real conditions only -- see this file's module docstring (Phase C) for
  // exactly which GET endpoint backs each severity, and why a broker/
  // internal-ledger discrepancy row is deliberately omitted (no live
  // broker-side readback is exposed by any GET endpoint -- see TR-13).
  function buildAttentionItems({ lifecycles, providers, signals, orders }) {
    const items = [];

    for (const l of lifecycles.filter((x) => x.halted)) {
      items.push({
        severity: "critical",
        text: `${l.account_id} · ${l.symbol} halted -- ${l.halt_reason || "reason unknown"}`,
        correlationId: `${l.account_id}:${l.symbol}`,
      });
    }
    for (const l of lifecycles.filter((x) => x.uncovered_quantity > 0)) {
      items.push({
        severity: "critical",
        text: `${l.account_id} · ${fmtNum(l.uncovered_quantity)} ${l.symbol} shares have no confirmed working stop`,
        correlationId: `${l.account_id}:${l.symbol}`,
      });
    }

    const lastSeenBySource = new Map();
    for (const s of signals) {
      if (!lastSeenBySource.has(s.source)) lastSeenBySource.set(s.source, s.received_at);
    }
    const now = Date.now();
    for (const p of providers) {
      const enabled = !p.settings || p.settings.enabled !== false;
      if (!enabled) continue;
      const lastSeen = lastSeenBySource.get(p.provider_id);
      if (!lastSeen) continue;
      const ageSeconds = (now - new Date(lastSeen).getTime()) / 1000;
      if (Number.isFinite(ageSeconds) && ageSeconds > STALE_SOURCE_THRESHOLD_SECONDS) {
        items.push({
          severity: "warning",
          text: `${p.provider_id} · source stream stale`,
          ageSeconds,
          correlationId: p.provider_id,
        });
      }
    }

    const pending = orders.filter((o) => o.status === "pending");
    if (pending.length) {
      items.push({ severity: "info", text: `${pending.length} entry order(s) awaiting fill` });
    }

    return items;
  }

  // Renders the KPI band + its Level-3 caveats, the attention queue, and
  // the notional/loss-at-stop panel (p10) -- entirely additive, reads-only
  // data this file's own docstring documents, never touching the Phase
  // B1/B8 panels' own fetch/render logic above.
  async function loadGlanceSection(ctx, panelEls, { accountsRes, positionsRes, ordersRes }) {
    if (accountsRes.status === 401 || accountsRes.status === 403 || positionsRes.status === 401 || positionsRes.status === 403) {
      const code = accountsRes.status === 401 || accountsRes.status === 403 ? accountsRes.status : positionsRes.status;
      StateMatrix.render(panelEls.kpi, { state: "denied", deniedCode: code });
      StateMatrix.render(panelEls.attention, { state: "denied", deniedCode: code });
      StateMatrix.render(panelEls.exposure, { state: "denied", deniedCode: code });
      return;
    }
    if (!accountsRes.ok || !positionsRes.ok) {
      StateMatrix.render(panelEls.kpi, { state: "error", message: "Could not load accounts/positions." });
      StateMatrix.render(panelEls.attention, { state: "error", message: "Could not load accounts/positions." });
      StateMatrix.render(panelEls.exposure, { state: "error", message: "Could not load accounts/positions." });
      return;
    }

    const accounts = (accountsRes.data && accountsRes.data.accounts) || [];
    const lifecycles = (positionsRes.data && positionsRes.data.managed_lifecycles) || [];
    const orders = (ordersRes.ok && ordersRes.data && ordersRes.data.orders) || [];
    const halted = lifecycles.filter((l) => l.halted);
    const unknownOps = lifecycles.filter((l) => l.pending_exit || l.pending_entry).length;

    const [capitalRes, systemInfoRes, providersRes, signalsRes] = await Promise.all([
      ctx.fetchJSON("/capital-allocation"),
      ctx.fetchJSON("/system/info"),
      ctx.fetchJSON("/providers"),
      ctx.fetchJSON("/signals?limit=500"),
    ]);

    const economics = await Promise.all(
      accounts.map((a) => ctx.fetchJSON(`/accounts/${encodeURIComponent(a.account_id)}/economics`))
    );

    const capitalAccounts = (capitalRes.ok && capitalRes.data && capitalRes.data.accounts) || [];
    const deployedTotal = capitalAccounts.reduce((sum, a) => sum + (a.deployed_notional || 0), 0);
    const reservedTotal = capitalAccounts.reduce((sum, a) => sum + (a.reserved_notional || 0), 0);
    const capitalDegraded = !capitalRes.ok;

    // Total P&L: real per-account GET /accounts/{id}/economics realized_pnl,
    // summed -- only when EVERY account's read succeeded (a partial sum
    // that silently drops a failed account would understate real P&L).
    const economicsOk = economics.every((r) => r.ok);
    const totalPnl = economicsOk ? economics.reduce((sum, r) => sum + (r.data.realized_pnl || 0), 0) : null;

    // Day P&L: real per-account GET /accounts/{id}/equity-history
    // cumulative_pnl change, but ONLY over an account's own real snapshots
    // that genuinely span (close to) the last 24h -- never a shorter
    // window silently mislabeled "day." equity_snapshot_interval_seconds
    // (real, GET /system/info) sets the tolerance for "close to."
    const snapshotIntervalSeconds = (systemInfoRes.ok && systemInfoRes.data && systemInfoRes.data.equity_snapshot_interval_seconds) || 300;
    const sinceIso = new Date(Date.now() - 24 * 3600 * 1000).toISOString();
    const equityHistories = await Promise.all(
      accounts.map((a) => ctx.fetchJSON(`/accounts/${encodeURIComponent(a.account_id)}/equity-history?since=${encodeURIComponent(sinceIso)}&limit=2000`))
    );
    let dayPnlTotal = 0;
    let dayPnlCoveredCount = 0;
    equityHistories.forEach((r) => {
      if (!r.ok) return;
      const snaps = (r.data && r.data.snapshots) || [];
      if (snaps.length < 2) return;
      const first = snaps[0];
      const last = snaps[snaps.length - 1];
      const firstAgeMs = Date.now() - new Date(first.captured_at).getTime();
      const targetMs = 24 * 3600 * 1000;
      if (firstAgeMs < targetMs - 2 * snapshotIntervalSeconds * 1000) return; // genuinely doesn't reach back ~24h yet
      dayPnlTotal += last.cumulative_pnl - first.cumulative_pnl;
      dayPnlCoveredCount += 1;
    });
    const dayPnlAvailable = dayPnlCoveredCount > 0;

    // Open risk / Unprotected exposure: one shared computation.
    const riskRows = computeLifecycleRiskRows(lifecycles);
    const unprotectedRows = riskRows.filter((r) => r.unprotected);
    const unprotectedCount = unprotectedRows.length;
    const unprotectedQty = unprotectedRows.reduce((sum, r) => sum + r.uncovered_quantity, 0);
    const pricedRiskRows = riskRows.filter((r) => r.riskAtStop !== null);
    const openRiskTotal = pricedRiskRows.reduce((sum, r) => sum + r.riskAtStop, 0);
    const openRiskAvailable = pricedRiskRows.length > 0;
    const riskUnknownCount = riskRows.filter((r) => r.riskUnknown).length;

    const activeAccounts = accounts.filter((a) => a.enabled).length;
    const standbyMode = systemInfoRes.ok && systemInfoRes.data ? systemInfoRes.data.standby_mode : null;

    // Every path below is a real "ready" render -- StateMatrix.render's own
    // "ready" case would do exactly this (removeAttribute + innerHTML),
    // but these three panels build real DOM nodes via Components.render*
    // rather than an HTML string, so it's done directly here instead of
    // through StateMatrix's html-string-only "ready" case.
    panelEls.kpi.removeAttribute("aria-busy");
    panelEls.attention.removeAttribute("aria-busy");
    panelEls.exposure.removeAttribute("aria-busy");

    // --- KPI band ---
    const items = [
      standbyMode === null
        ? { label: "Trading mode", value: "Unknown", tone: "neutral", sublabel: "Could not read GET /system/info" }
        : standbyMode
        ? { label: "Trading mode", value: "Standby", tone: "warn", sublabel: "STANDBY_MODE set -- no order can reach the engine" }
        : { label: "Trading mode", value: "Live", tone: "ok", sublabel: "Standby mode is off" },
      {
        label: "Active accounts",
        value: fmtNum(activeAccounts),
        tone: activeAccounts === 0 && accounts.length > 0 ? "warn" : "neutral",
        sublabel: `${accounts.length} configured`,
      },
      { label: "Net liquidation", value: "Not summed", tone: "neutral", sublabel: "see note below" },
      dayPnlAvailable
        ? { label: "Day P&L", value: fmtNum(dayPnlTotal), tone: dayPnlTotal >= 0 ? "ok" : "warn", sublabel: `${dayPnlCoveredCount}/${accounts.length} accounts have ~24h history` }
        : { label: "Day P&L", value: "Not tracked", tone: "neutral", sublabel: "see note below" },
      economicsOk
        ? { label: "Total P&L", value: fmtNum(totalPnl), tone: totalPnl >= 0 ? "ok" : "warn", sublabel: "realized, sum of all accounts" }
        : { label: "Total P&L", value: "Not tracked", tone: "neutral", sublabel: "see note below" },
      { label: "Deployed capital", value: fmtNum(deployedTotal), tone: "neutral", sublabel: capitalDegraded ? "degraded this refresh" : `${capitalAccounts.length} accounts` },
      { label: "Reserved capital", value: fmtNum(reservedTotal), tone: "neutral", sublabel: capitalDegraded ? "degraded this refresh" : `${capitalAccounts.length} accounts` },
      openRiskAvailable
        ? { label: "Open risk", value: fmtNum(openRiskTotal), tone: "neutral", sublabel: `${pricedRiskRows.length} priced position(s)${riskUnknownCount ? `, ${riskUnknownCount} unknown` : ""}` }
        : { label: "Open risk", value: "Not tracked", tone: "neutral", sublabel: "see note below" },
      {
        label: "Unprotected exposure",
        value: fmtNum(unprotectedCount),
        tone: unprotectedCount > 0 ? "crit" : "ok",
        sublabel: unprotectedCount > 0 ? `${fmtNum(unprotectedQty)} shares uncovered` : "0 uncovered managed positions",
      },
      { label: "Unknown orders", value: fmtNum(unknownOps), tone: unknownOps > 0 ? "warn" : "ok", sublabel: "unresolved pending entry/exit" },
      { label: "Critical incidents", value: fmtNum(halted.length), tone: halted.length > 0 ? "crit" : "ok", sublabel: "halted managed positions" },
    ];

    const kpiHost = document.createElement("div");
    Components.renderKPIBand(kpiHost, { items });

    const caveats = [];
    caveats.push({
      title: "Net liquidation",
      status: "not_tracked",
      reason: "This build has no per-account currency field (see app/models.py's AccountBalance) to prove every configured account's broker-reported equity is in the same currency, so summing them into one figure would be an unverified number, not a real one (same M-TR-01-01 gap this file's Account risk cards panel already discloses per-account below).",
    });
    if (!dayPnlAvailable) {
      caveats.push({
        title: "Day P&L",
        status: "not_tracked",
        reason: "No configured account yet has real, persisted equity-history snapshots (GET /accounts/{id}/equity-history, app/equity_history.py) spanning a full ~24h window -- computing a 'day' change from a shorter real window would silently mislabel it.",
      });
    }
    if (!economicsOk) {
      caveats.push({
        title: "Total P&L",
        status: "not_tracked",
        reason: "GET /accounts/{id}/economics could not be read for one or more accounts this refresh -- a partial sum would understate real realized P&L.",
      });
    }
    if (!openRiskAvailable) {
      caveats.push({
        title: "Open risk",
        status: "not_tracked",
        reason: "No managed-lifecycle position is currently fully covered, stop-confirmed, and has both a known entry price and a known broker-confirmed stop price -- a real '$0' would be indistinguishable from 'unknown,' which this codebase never allows (see app/lifecycle/models.py's ProtectionStatus).",
      });
    }

    panelEls.kpi.innerHTML = "";
    panelEls.kpi.appendChild(kpiHost);
    const caveatsWrap = document.createElement("div");
    caveatsWrap.className = "tr-controls-row";
    caveatsWrap.style.cssText = "margin-top:10px; gap:24px; flex-wrap:wrap;";
    caveatsWrap.innerHTML = caveats
      .map((c) => `<div><span class="section-note">${escapeHtml(c.title)}: </span><span class="tr01-caveat" data-title="${escapeAttr(c.title)}"></span></div>`)
      .join("");
    panelEls.kpi.appendChild(caveatsWrap);
    caveats.forEach((c) => {
      const el = caveatsWrap.querySelector(`.tr01-caveat[data-title="${c.title.replace(/"/g, '\\"')}"]`);
      if (el) Components.renderCapabilityState(el, { status: c.status, reason: c.reason });
    });
    panelEls.kpi.insertAdjacentHTML(
      "beforeend",
      `<p class="section-note" style="margin-top:8px;">Cross-account totals above (Day/Total P&L, Deployed/Reserved capital) assume every configured account's figures share one implicit currency/unit -- this build stores no per-account currency field anywhere to verify that (the same gap that keeps Net liquidation from being summed at all).</p>`
    );

    // --- Attention required ---
    const attentionItems = buildAttentionItems({
      lifecycles,
      providers: (providersRes.ok && providersRes.data && providersRes.data.providers) || [],
      signals: (signalsRes.ok && signalsRes.data && signalsRes.data.signals) || [],
      orders,
    });
    Components.renderAttentionQueue(panelEls.attention, { items: attentionItems });

    // --- p10: notional exposure + loss-at-stop, by asset class ---
    const assetClassBySymbol = signalsRes.ok
      ? assetClassMapFromSignals((signalsRes.data && signalsRes.data.signals) || [])
      : new Map();
    const pricedRows = riskRows.filter((r) => r.notional !== null);
    const unpricedCount = riskRows.length - pricedRows.length;
    const notionalEntries = aggregateSum(
      pricedRows,
      (r) => assetClassBySymbol.get(r.symbol) || "unknown",
      (r) => r.notional,
      6
    );
    const riskByAssetClass = aggregateSum(
      pricedRiskRows,
      (r) => assetClassBySymbol.get(r.symbol) || "unknown",
      (r) => r.riskAtStop,
      6
    );

    panelEls.exposure.innerHTML = `<p class="section-note">Real notional (entry price x quantity) and loss-at-stop (covered_quantity x |entry - stop|), by asset class -- both computed ONLY from managed-lifecycle positions with a known entry price (this build stores no current market price for a plain-account position anywhere -- see this file's own docstring). ${
      riskRows.length ? `${pricedRows.length} of ${riskRows.length} open managed-lifecycle position(s) have a known notional${unpricedCount ? `; ${unpricedCount} excluded (no entry price yet)` : ""}.` : "No open managed-lifecycle position exists right now."
    }</p>
      <div class="tr-controls-row" style="align-items:flex-start; gap:24px; flex-wrap:wrap;">
        <div style="flex:1; min-width:260px;"><h3 class="section-note">Notional exposure by asset class</h3><div id="tr01-donut-notional-wrap"></div></div>
        <div style="flex:1; min-width:260px;"><h3 class="section-note">Loss at stop by asset class</h3><div id="tr01-riskbar-wrap"></div></div>
      </div>`;
    renderNotionalDonut(panelEls.exposure, "tr01-donut-notional", notionalEntries, (label) => ASSET_CLASS_COLOR[label] || DONUT_OTHER_COLOR);
    renderRiskBarChart(panelEls.exposure, "tr01-riskbar", riskByAssetClass);
  }

  async function load(ctx) {
    const panelEls = {
      identity: ctx.container.querySelector("#tr01-p01 .tr-panel-body"),
      safety: ctx.container.querySelector("#tr01-p02 .tr-panel-body"),
      risk: ctx.container.querySelector("#tr01-p03 .tr-panel-body"),
      pnl: ctx.container.querySelector("#tr01-p04 .tr-panel-body"),
      incidents: ctx.container.querySelector("#tr01-p05 .tr-panel-body"),
      activity: ctx.container.querySelector("#tr01-p06 .tr-panel-body"),
      openPositions: ctx.container.querySelector("#tr01-p07 .tr-panel-body"),
      allocation: ctx.container.querySelector("#tr01-p08 .tr-panel-body"),
      risk2: ctx.container.querySelector("#tr01-p09 .tr-panel-body"),
      kpi: ctx.container.querySelector("#tr01-kpi .tr-panel-body"),
      attention: ctx.container.querySelector("#tr01-attention .tr-panel-body"),
      exposure: ctx.container.querySelector("#tr01-p10 .tr-panel-body"),
    };
    for (const el of Object.values(panelEls)) StateMatrix.render(el, { state: "loading" });

    const health = await ctx.fetchJSON("/health");
    if (health.status === 401 || health.status === 403) {
      for (const el of Object.values(panelEls)) StateMatrix.render(el, { state: "denied", deniedCode: health.status });
      return;
    }
    if (!health.ok) {
      for (const el of Object.values(panelEls)) StateMatrix.render(el, { state: "error", message: "Could not reach the service." });
      return;
    }
    const h = health.data || {};

    StateMatrix.render(panelEls.identity, {
      state: "ready",
      html: `<p>Environment: <strong>private_execution</strong> (single-owner engine). Access scope: <strong>private_owner</strong>.</p>
             <p class="section-note">This is this service's own record of what it has sent/tracked -- not a live broker read (see each panel's own data source).</p>`,
    });

    const safetyRows = [
      ["Database", boolPill(h.database_ok)],
      ["Price monitor", boolPill(h.price_monitor_ok)],
      ["Order reconciler", boolPill(h.reconciler_ok)],
      ["Provider scout", h.provider_scout_ok === null || h.provider_scout_ok === undefined ? pill("n/a", "muted") : boolPill(h.provider_scout_ok)],
      ["Relay (commercial export)", h.relay_ok === null || h.relay_ok === undefined ? pill("not configured", "muted") : boolPill(h.relay_ok)],
    ];
    StateMatrix.render(panelEls.safety, {
      state: "ready",
      html: table(["Subsystem", "Status"], safetyRows.map(([k, v]) => [escapeHtml(k), v]), "No subsystem status available."),
    });

    const [accountsRes, positionsRes] = await Promise.all([ctx.fetchJSON("/accounts"), ctx.fetchJSON("/positions")]);
    if (accountsRes.status === 401 || accountsRes.status === 403) {
      StateMatrix.render(panelEls.risk, { state: "denied", deniedCode: accountsRes.status });
      StateMatrix.render(panelEls.pnl, { state: "denied", deniedCode: accountsRes.status });
    } else if (!accountsRes.ok) {
      StateMatrix.render(panelEls.risk, { state: "error", message: "Could not load accounts." });
      StateMatrix.render(panelEls.pnl, { state: "error", message: "Could not load accounts." });
    } else {
      const accounts = (accountsRes.data && accountsRes.data.accounts) || [];
      if (!accounts.length) {
        StateMatrix.render(panelEls.risk, {
          state: "empty",
          emptyMessage: "No brokerage account is configured for this workspace.",
          nextRoute: "/trade/positions",
          nextLabel: "Go to Positions and allocations",
        });
        StateMatrix.render(panelEls.pnl, { state: "empty", emptyMessage: "No brokerage account is configured for this workspace." });
      } else {
        const balances = await Promise.all(
          accounts.map((a) => ctx.fetchJSON(`/accounts/${encodeURIComponent(a.account_id)}/balance`))
        );
        const riskRows = accounts.map((a, i) => {
          const b = balances[i].ok ? balances[i].data : null;
          const netLiq = b && b.equity !== null && b.equity !== undefined ? fmtNum(b.equity) : pill("unknown", "muted");
          return [
            `<span class="mono">${escapeHtml(a.account_id)}</span>`,
            `<span class="mono">${escapeHtml(a.broker)}</span>`,
            netLiq,
            boolPill(a.enabled, "enabled", "disabled"),
          ];
        });
        StateMatrix.render(panelEls.risk, {
          state: "ready",
          html: `<p class="section-note">M-TR-01-01: verified net liquidation (broker-reported equity), not summed across accounts -- different accounts may use different brokers/currencies and this build has no cross-account currency normalization.</p>${table(
            ["Account", "Broker", "Verified net liquidation", "Enabled"],
            riskRows,
            "No accounts."
          )}`,
        });

        const economics = await Promise.all(
          accounts.map((a) => ctx.fetchJSON(`/accounts/${encodeURIComponent(a.account_id)}/economics`))
        );
        const positions = (positionsRes.ok && positionsRes.data && positionsRes.data.positions) || [];
        const lifecycles = (positionsRes.ok && positionsRes.data && positionsRes.data.managed_lifecycles) || [];
        const deficitCount = lifecycles.filter((l) => l.uncovered_quantity > 0).length;
        // The snapshot only ever carries a pending_exit/pending_entry while
        // it is still unresolved (app/main.py's _managed_lifecycle_snapshot
        // reads it straight off the lifecycle, which clears each field the
        // moment resolve_pending_exit/resolve_pending_entry finishes) -- so
        // presence alone means "unknown operation," no extra flag to check.
        const unknownOps = lifecycles.filter((l) => l.pending_exit || l.pending_entry).length;
        const pnlRows = accounts.map((a, i) => {
          const e = economics[i].ok ? economics[i].data : null;
          const pnl = e ? fmtNum(e.realized_pnl) : pill("unknown", "muted");
          const cls = e && e.realized_pnl >= 0 ? "ok-text" : "bad-text";
          return [`<span class="mono">${escapeHtml(a.account_id)}</span>`, `<span class="num ${cls}">${pnl}</span>`];
        });
        StateMatrix.render(panelEls.pnl, {
          state: "ready",
          html: `${table(["Account", "Realized P&amp;L"], pnlRows, "No accounts.")}
                 <div class="econ-stats" style="margin-top:10px;">
                   <div><span class="muted">Open positions (all accounts)</span><br><span class="num">${positions.length}</span></div>
                   <div><span class="muted">M-TR-01-03 Protection deficit (allocations uncovered)</span><br><span class="num">${deficitCount}</span></div>
                   <div><span class="muted">M-TR-01-04 Unknown operations</span><br><span class="num">${unknownOps}</span></div>
                 </div>
                 <div class="tr-unsupported-note">${escapeHtml("M-TR-01-02 Reserved risk: not available -- this build exposes no committed/reserved-risk ledger via any read endpoint.")}</div>`,
        });
      }
    }

    if (positionsRes.status === 401 || positionsRes.status === 403) {
      StateMatrix.render(panelEls.incidents, { state: "denied", deniedCode: positionsRes.status });
    } else if (!positionsRes.ok) {
      StateMatrix.render(panelEls.incidents, { state: "error", message: "Could not load incidents." });
    } else {
      const lifecycles = (positionsRes.data && positionsRes.data.managed_lifecycles) || [];
      const halted = lifecycles.filter((l) => l.halted);
      if (!halted.length) {
        StateMatrix.render(panelEls.incidents, { state: "ready", html: `<p class="section-note">No halted managed-lifecycle positions right now.</p>` });
      } else {
        const rows = halted.map((l) => [
          `<span class="mono">${escapeHtml(l.account_id)}</span>`,
          `<span class="mono">${escapeHtml(l.symbol)}</span>`,
          pill(l.halt_reason || "halted", "bad"),
          `<a href="#/trade/positions/${encodeURIComponent(l.account_id)}/${encodeURIComponent(l.symbol)}">Open position</a>`,
        ]);
        StateMatrix.render(panelEls.incidents, { state: "ready", html: table(["Account", "Symbol", "Reason", ""], rows, "No incidents.") });
      }
    }

    // limit=100 (was 15): the same fetch now also backs the Attention
    // queue's real "orders awaiting fill" count below (loadGlanceSection)
    // -- the Recent activity table itself still only ever shows the 15
    // most recent (orders is already newest-first), so this changes no
    // rendered content in this panel.
    const ordersRes = await ctx.fetchJSON("/orders?limit=100");
    if (ordersRes.status === 401 || ordersRes.status === 403) {
      StateMatrix.render(panelEls.activity, { state: "denied", deniedCode: ordersRes.status });
    } else if (!ordersRes.ok) {
      StateMatrix.render(panelEls.activity, { state: "error", message: "Could not load recent activity." });
    } else {
      const orders = (ordersRes.data && ordersRes.data.orders) || [];
      if (!orders.length) {
        StateMatrix.render(panelEls.activity, { state: "empty", emptyMessage: "No recent activity." });
      } else {
        const rows = orders.slice(0, 15).map((o) => [
          o.executed_at || "—",
          `<span class="mono">${escapeHtml(o.account_id)}</span>`,
          `<span class="mono">${escapeHtml(o.symbol || "—")}</span>`,
          escapeHtml(o.side || "—"),
          o.status === "filled" ? pill("filled", "ok") : o.status === "pending" ? pill("pending", "warn") : pill(o.status || "—", "bad"),
        ]);
        StateMatrix.render(panelEls.activity, { state: "ready", html: table(["Executed at", "Account", "Symbol", "Side", "Status"], rows, "No recent activity.") });
      }
    }

    // --- Open positions + Allocation donuts: both built from the same
    // real GET /positions read (positionsRes, already fetched above),
    // enriched with GET /signals (for asset class, joined by symbol) and
    // GET /accounts (for broker, joined by account_id -- accountsRes is
    // already fetched above too). ---
    if (positionsRes.status === 401 || positionsRes.status === 403) {
      StateMatrix.render(panelEls.openPositions, { state: "denied", deniedCode: positionsRes.status });
      StateMatrix.render(panelEls.allocation, { state: "denied", deniedCode: positionsRes.status });
    } else if (!positionsRes.ok) {
      StateMatrix.render(panelEls.openPositions, { state: "error", message: "Could not load open positions." });
      StateMatrix.render(panelEls.allocation, { state: "error", message: "Could not load open positions." });
    } else {
      const positions = (positionsRes.data && positionsRes.data.positions) || [];
      const lifecycles = (positionsRes.data && positionsRes.data.managed_lifecycles) || [];
      const lifecycleByKey = new Map(lifecycles.map((l) => [`${l.account_id}::${l.symbol}`, l]));

      const signalsRes = await ctx.fetchJSON("/signals?limit=500");
      const assetClassBySymbol = signalsRes.ok
        ? assetClassMapFromSignals((signalsRes.data && signalsRes.data.signals) || [])
        : new Map();
      const assetClassJoinDegraded = !signalsRes.ok;

      const brokerByAccount = new Map(
        ((accountsRes.ok && accountsRes.data && accountsRes.data.accounts) || []).map((a) => [a.account_id, a.broker])
      );

      function assetClassFor(p) {
        return assetClassBySymbol.get(p.symbol) || "unknown";
      }
      function brokerFor(p) {
        return brokerByAccount.get(p.account_id) || "unknown";
      }

      if (!positions.length) {
        StateMatrix.render(panelEls.openPositions, {
          state: "empty",
          emptyMessage: "No open positions right now.",
          nextRoute: "/trade/positions",
          nextLabel: "Go to Positions and allocations",
        });
        StateMatrix.render(panelEls.allocation, { state: "empty", emptyMessage: "No open positions to allocate." });
      } else {
        const posRows = positions.map((p) => {
          const l = lifecycleByKey.get(`${p.account_id}::${p.symbol}`);
          const mae = l && l.has_price_data && l.mae !== null && l.mae !== undefined ? fmtNum(l.mae) : pill("n/a", "muted");
          const mfe = l && l.has_price_data && l.mfe !== null && l.mfe !== undefined ? fmtNum(l.mfe) : pill("n/a", "muted");
          const ac = assetClassBySymbol.get(p.symbol);
          return [
            `<a href="#/trade/positions/${encodeURIComponent(p.account_id)}/${encodeURIComponent(p.symbol)}">${escapeHtml(p.account_id)}</a>`,
            `<span class="mono">${escapeHtml(p.symbol)}</span>`,
            ac ? escapeHtml(ac) : pill("unknown", "muted"),
            `<span class="num">${fmtNum(p.net_quantity)}</span>`,
            mae,
            mfe,
          ];
        });
        StateMatrix.render(panelEls.openPositions, {
          state: "ready",
          html: `<p class="section-note">Every non-flat tracked position, across all accounts (GET /positions). MAE/MFE (PU-A1) are the real in-progress excursion figures for managed-lifecycle positions only -- "n/a" for a plain-account position or one with no real price observation yet, never a fabricated 0. Asset class is joined from this symbol's most recent GET /signals row (this build stores no asset class on the position itself) -- "unknown" when no matching signal exists in the most recent 500.</p>${table(
            ["Account", "Symbol", "Asset class", "Net quantity", "MAE", "MFE"],
            posRows,
            "No open positions."
          )}`,
        });

        const assetClassEntries = aggregateCounts(positions, assetClassFor, 6);
        const brokerEntries = aggregateCounts(positions, brokerFor, 6);
        const accountEntries = aggregateCounts(positions, (p) => p.account_id, 6);

        let colorIndex = 0;
        const brokerColors = new Map();
        const accountColors = new Map();
        function stableColor(map, label) {
          if (label === "Other" || label === "unknown") return DONUT_OTHER_COLOR;
          if (!map.has(label)) map.set(label, DONUT_PALETTE[colorIndex++ % DONUT_PALETTE.length]);
          return map.get(label);
        }

        StateMatrix.render(panelEls.allocation, {
          state: "ready",
          html: `${assetClassJoinDegraded ? `<p class="tr-unsupported-note">Asset-class allocation is degraded: GET /signals could not be read this refresh, so every open position shows as "unknown" below.</p>` : ""}
                 <p class="section-note">All three donuts chart the same real metric -- an exact OPEN POSITION COUNT per bucket -- never a dollar exposure (neither GET /positions nor GET /signals stores a current market price to compute one from).</p>
                 <div class="tr-controls-row" style="align-items:flex-start; gap:24px; flex-wrap:wrap;">
                   <div style="flex:1; min-width:220px;"><h3 class="section-note">By asset class</h3><div id="tr01-donut-assetclass-wrap"></div></div>
                   <div style="flex:1; min-width:220px;"><h3 class="section-note">By broker</h3><div id="tr01-donut-broker-wrap"></div></div>
                   <div style="flex:1; min-width:220px;"><h3 class="section-note">By account</h3><div id="tr01-donut-account-wrap"></div></div>
                 </div>`,
        });
        renderDonut(panelEls.allocation, "tr01-donut-assetclass", assetClassEntries, (label) => ASSET_CLASS_COLOR[label] || DONUT_OTHER_COLOR, "open positions");
        renderDonut(panelEls.allocation, "tr01-donut-broker", brokerEntries, (label) => stableColor(brokerColors, label), "open positions");
        renderDonut(panelEls.allocation, "tr01-donut-account", accountEntries, (label) => stableColor(accountColors, label), "open positions");
      }
    }

    // --- Risk panel (Phase B8): rolling volatility/Sharpe-equivalent/
    // Sortino-equivalent/max-drawdown per account (GET
    // /accounts/{id}/statistics) + cross-account correlation (GET
    // /accounts/correlation) -- both real, from app/statistics.py (Phase
    // A5, already landed). Reuses accountsRes, already fetched above. ---
    if (accountsRes.status === 401 || accountsRes.status === 403) {
      StateMatrix.render(panelEls.risk2, { state: "denied", deniedCode: accountsRes.status });
    } else if (!accountsRes.ok) {
      StateMatrix.render(panelEls.risk2, { state: "error", message: "Could not load accounts." });
    } else {
      const accounts = (accountsRes.data && accountsRes.data.accounts) || [];
      if (!accounts.length) {
        StateMatrix.render(panelEls.risk2, {
          state: "empty",
          emptyMessage: "No brokerage account is configured for this workspace.",
        });
      } else {
        const statsResList = await Promise.all(
          accounts.map((a) => ctx.fetchJSON(`/accounts/${encodeURIComponent(a.account_id)}/statistics`))
        );
        const statsDegraded = statsResList.some((r) => !r.ok);

        const statsRows = accounts.map((a, i) => {
          const r = statsResList[i];
          const s = r.ok ? r.data : null;
          if (!s) {
            return [`<span class="mono">${escapeHtml(a.account_id)}</span>`, pill("unknown", "muted"), pill("unknown", "muted"), pill("unknown", "muted"), pill("unknown", "muted"), pill("unknown", "muted")];
          }
          return [
            `<span class="mono">${escapeHtml(a.account_id)}</span>`,
            statVal(s.volatility_pnl_delta),
            statVal(s.sharpe_equivalent),
            statVal(s.sortino_equivalent),
            statVal(s.max_drawdown),
            fmtDuration(s.max_drawdown_duration_seconds),
          ];
        });

        let correlationHtml = "";
        if (accounts.length >= 2) {
          const pairs = [];
          for (let i = 0; i < accounts.length; i++) {
            for (let j = i + 1; j < accounts.length; j++) {
              pairs.push([accounts[i].account_id, accounts[j].account_id]);
            }
          }
          const corrResList = await Promise.all(
            pairs.map(([a, b]) => ctx.fetchJSON(`/accounts/correlation?account_a=${encodeURIComponent(a)}&account_b=${encodeURIComponent(b)}`))
          );
          const corrDegraded = corrResList.some((r) => !r.ok);
          const corrRows = pairs.map(([a, b], i) => {
            const r = corrResList[i];
            const c = r.ok ? r.data : null;
            return [
              `<span class="mono">${escapeHtml(a)}</span>`,
              `<span class="mono">${escapeHtml(b)}</span>`,
              c ? fmtNum(c.sample_count) : pill("unknown", "muted"),
              c ? statVal(c.correlation) : pill("unknown", "muted"),
            ];
          });
          correlationHtml = `
            <h3 class="section-note" style="margin-top:16px;">Cross-account correlation</h3>
            <p class="section-note">Real Pearson correlation of each account pair's real cumulative_pnl snapshot series (GET /accounts/correlation), matched by overlapping capture time. "n/a" when a pair has fewer than the endpoint's own documented minimum overlapping-sample floor -- never a fabricated 0/NaN.</p>
            ${corrDegraded ? `<p class="tr-unsupported-note">Correlation is degraded for one or more pairs this refresh -- could not reach GET /accounts/correlation.</p>` : ""}
            <div id="tr01-risk-correlation-wrap">${table(["Account A", "Account B", "Overlapping samples", "Correlation"], corrRows, "No account pairs.")}</div>`;
        } else {
          correlationHtml = `<p class="section-note">Cross-account correlation needs at least 2 accounts to form a pair -- only ${accounts.length} account configured.</p>`;
        }

        StateMatrix.render(panelEls.risk2, {
          state: "ready",
          html: `<p class="section-note">Real, per-account statistics from GET /accounts/{id}/statistics (app/statistics.py, Phase A5), computed from this account's real persisted cumulative_pnl snapshot series -- absolute P&amp;L-delta terms, never a percentage return (this codebase has no configured per-account starting-balance/capital figure to divide by). Sharpe-equivalent/Sortino-equivalent use an implicit risk-free rate of 0 (no risk-free-rate figure is stored anywhere in this codebase) and are labeled "equivalent," never a real Sharpe/Sortino ratio. "n/a" means the backend itself returned null because this account's real snapshot history is too short for that statistic -- never a fabricated 0.</p>
                 ${statsDegraded ? `<p class="tr-unsupported-note">Statistics are degraded for one or more accounts this refresh -- could not reach GET /accounts/{id}/statistics.</p>` : ""}
                 <div id="tr01-risk-stats-wrap">${table(["Account", "Volatility (P&amp;L delta)", "Sharpe-equivalent", "Sortino-equivalent", "Max drawdown", "Max drawdown duration"], statsRows, "No accounts.")}</div>
                 ${correlationHtml}
                 <div class="tr-unsupported-note">Not built here -- Value-at-Risk / Expected Shortfall: no real returns-distribution model exists anywhere in this codebase to take a real quantile of. Risk-limit-utilization / drawdown-circuit-breaker state (PAUSE_NEW_ENTRIES/REQUIRE_REVIEW) and persisted placement-rate-limit headroom: these are real and already built, but on a separate, not-yet-merged branch (app/drawdown_governor.py, app/placement_rate_limiter.py on claude/signal-copier-safety-features) -- not available on this branch. Liquidity-risk heatmap: no real liquidity data (spread, order-book depth) exists anywhere in this codebase.</div>`,
        });
      }
    }

    await loadGlanceSection(ctx, panelEls, { accountsRes, positionsRes, ordersRes });

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr01 = {
    title: "Trading command center",
    breadcrumb: "Trade",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = panelShell();
      await load(ctx);
      ctx.registerPoll("tr01", 10000, () => load(ctx));
    },
  };
  Router.register("/trade", "tr01");
})();
