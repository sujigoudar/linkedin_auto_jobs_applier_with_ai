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
 */
(function () {
  "use strict";

  function panelShell() {
    return `
      <section class="tr-panel" id="tr01-p01"><h2>Identity / environment</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p02"><h2>Safety summary</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p03"><h2>Account risk cards</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p04"><h2>P&amp;L and exposure</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p05"><h2>Priority incidents</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p06"><h2>Recent activity</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p07"><h2>Open positions</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p08"><h2>Allocation</h2><div class="tr-panel-body"></div></section>
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

    const ordersRes = await ctx.fetchJSON("/orders?limit=15");
    if (ordersRes.status === 401 || ordersRes.status === 403) {
      StateMatrix.render(panelEls.activity, { state: "denied", deniedCode: ordersRes.status });
    } else if (!ordersRes.ok) {
      StateMatrix.render(panelEls.activity, { state: "error", message: "Could not load recent activity." });
    } else {
      const orders = (ordersRes.data && ordersRes.data.orders) || [];
      if (!orders.length) {
        StateMatrix.render(panelEls.activity, { state: "empty", emptyMessage: "No recent activity." });
      } else {
        const rows = orders.map((o) => [
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
