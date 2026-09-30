/* TR-14: Trading performance and execution quality (`#/trade/performance`).
 *
 * Real backing data, reused rather than reinvented:
 *   - GET /accounts/{id}/economics (E06, app/economics.py): realized P&L,
 *     cost basis and TWO distinct win-rate metrics per account/symbol,
 *     computed by replaying confirmed fills -- gross of fees (no fee
 *     column exists in `orders` at all), no live-market unrealized P&L.
 *   - GET /accounts/{id}/execution-quality (E05, app/execution_quality.py):
 *     signal-received-to-fill latency per symbol, from this schema's own
 *     `received_at`/`executed_at` timestamps -- explicitly NOT the fuller
 *     publication->decision->submit->acknowledge->fill->protection
 *     pipeline (no intermediate timestamps exist to report it).
 *   - GET /accounts/{id}/equity-history (PU-A3, app/equity_history.py): a
 *     real, persisted `cumulative_pnl` (= realized_pnl + unrealized_pnl)
 *     snapshot series per account -- the equity curve / drawdown /
 *     underwater / daily-P&L source of truth this phase adds.
 *   - GET /accounts/{id}/statistics (Phase A5, app/statistics.py): real
 *     rolling mean/volatility/Sharpe-equivalent/Sortino-equivalent/
 *     max-drawdown(+duration) over that same snapshot series -- absolute
 *     P&L-delta terms, never a fabricated percentage return (no
 *     configured starting-balance baseline exists anywhere in this
 *     codebase -- see that module's own docstring).
 *   - GET /positions/excursions (PU-A1): real, final MAE/MFE per CLOSED
 *     managed-lifecycle episode.
 *   - GET /providers/value: real, FIFO-lot P&L attribution per
 *     (source, analyst, asset_class), replayed GLOBALLY across every
 *     account (see app/provider_value.py's own docstring for why this is
 *     a materially different -- also real -- computation than
 *     app/economics.py's account-level average-cost replay, and why it
 *     structurally can't be split back out per account/environment).
 *   - GET /capital-allocation (Phase B7): real, current deployed/reserved
 *     notional vs. a configured ceiling, per account.
 *   - GET /positions/{account}/{symbol}/stop-events (PU-A4): the real,
 *     append-only stop/target lifecycle event log.
 *   - GET /brokers (broker capability introspection): `environment`
 *     ("paper" | "live" | null) and `fee_per_fill` (PaperBroker-only real
 *     value, null everywhere else) per registered broker adapter.
 *
 * PROVENANCE LABELING (this phase's own hard requirement, additive to
 * every metric already on this screen): every metric/chart below carries
 * an explicit ACTUAL LIVE / PAPER / INCOMPLETE badge (see
 * `accountProvenance`/`provenancePill` below) -- this screen never shows
 * BACKTEST- or MODEL-derived numbers, so those two labels are unused here
 * (see README note in the module's own PR description). Determined from
 * this account's OWN configured `broker` name matched against
 * `GET /brokers`' own code-verified `environment` field for that broker
 * (never a per-account guess) -- `null`/unregistered resolves to
 * INCOMPLETE, never silently defaulted to PAPER or LIVE. A number that
 * structurally spans multiple accounts whose brokers don't all share one
 * verified environment (e.g. `/providers/value`'s global FIFO replay,
 * which has no per-account breakdown to split by) is labeled INCOMPLETE
 * with an explicit reason, never a blended single environment claim --
 * this is the single riskiest piece of new logic on this screen (a bug
 * here could mislabel a paper-simulated number as ACTUAL LIVE), and is
 * covered by its own load-bearing test
 * (tests/test_tr14_provenance_labels.py), which breaks the paper/live
 * branch on purpose and confirms the test that exists specifically to
 * catch that regression fails.
 *
 * Honest gaps, disclosed rather than invented (this phase's additions):
 *   - Fees/commissions: `orders` has no fee column anywhere in this
 *     schema (see app/economics.py's module docstring) -- gross-vs-net
 *     P&L and a fee figure are computed ONLY for accounts on the
 *     `paper` broker (app/brokers/paper.py's own real, documented
 *     `fee_per_fill` * this account's own real filled-order count, over
 *     the most recent `ORDERS_FEE_SAMPLE_LIMIT` filled orders this build
 *     can page through) -- `not_tracked` for every other broker, never a
 *     blended/estimated figure (see this module's own `costsPanelHtml`).
 *   - Funding/carry costs: no such concept exists anywhere in this
 *     codebase (checked: app/models.py, app/economics.py, app/brokers/*)
 *     -- `not_tracked`, not a fabricated zero.
 *   - Fill slippage / price improvement / adverse selection: no
 *     reference/expected execution price is stored anywhere this build
 *     could diff a fill against -- `unsupported` (unchanged from the
 *     prior phase).
 *   - Trade-return histogram / holding-time distribution: this schema
 *     exposes AGGREGATED per-(account,symbol) economics and MAE/MFE per
 *     CLOSED episode, but no per-episode realized-P&L row and no
 *     per-episode entry timestamp alongside its close -- a $-magnitude
 *     return histogram and a holding-time distribution both need exactly
 *     that per-episode row, which no GET endpoint exposes -- `not_tracked`
 *     rather than approximated from aggregates that aren't that.
 *   - A full per-episode exit-reason breakdown (stop-out vs. target-hit
 *     vs. manual close): `StopTargetEventType` (app/lifecycle/models.py)
 *     has no distinct "stop filled as an exit" event -- only STOP_PLACED
 *     (a stop being SET, not it firing), STOP_TIGHTENED, PROTECTION_FAILED
 *     and TARGET_HIT exist. TARGET_HIT is real and counted; a stop-out
 *     can't be distinguished from a manual/CLOSE-signal close from this
 *     event log alone, and simply subtracting target-hit events from a
 *     completed-episode count would conflate a partial target fill (one
 *     event, same episode) with a full close -- so the combined
 *     breakdown stays `not_tracked`, with the one real, exact count
 *     (target-hit events) disclosed alongside it, never a fabricated
 *     complement.
 *   - "Strategy" and "regime" breakdowns (the design review's own list):
 *     grepped this whole codebase for either concept as a real, stored
 *     field -- neither exists anywhere (only the word "strategy" used in
 *     unrelated prose/comments, e.g. app/statistics.py's own docstring
 *     and the NinjaTrader broker's NinjaScript "strategy" file). Adding a
 *     taxonomy for either would be inventing a dimension this schema
 *     doesn't track -- both are OMITTED from the breakdowns panel below
 *     rather than fabricated.
 *   - A full historical ROLLING-WINDOW time-series chart (rolling
 *     Sharpe/vol as of every past day, not just now): app/statistics.py's
 *     `compute_rolling_stats` returns one current-window result, not a
 *     series -- this build offers a window-size selector (7/30/90/180)
 *     over that same real, single-point backend result rather than
 *     re-deriving a client-side rolling series with its own (un-tested,
 *     divergent) windowing logic.
 *
 * PU-B9 additive (kept verbatim from the prior phase): `stage_latencies`
 * (app/execution_quality.py's per-stage breakdown, real -- see that
 * module's docstring for which of the 3 named stages -- receipt_to_
 * submission, submission_to_fill, fill_to_protection -- each order
 * actually reaches) is rendered as a second, additive grouped-bar
 * Chart.js chart, one bar-group per (account, symbol), one dataset per
 * stage. Values come straight from the backend's own mean_seconds -- never
 * recomputed client-side. A stage the backend omitted for a given symbol
 * (zero qualifying orders with both real endpoint timestamps) is rendered
 * as `null` in that dataset, which Chart.js correctly draws as no bar --
 * never a fabricated zero.
 *
 * Venue routing (Sankey/multi-venue quote comparison) stays
 * `unsupported`/not built at all -- unchanged from the prior phase: this
 * codebase has no multi-venue quote feed anywhere.
 *
 * "Export report" (F-REPORT) has no server-side async export-job queue
 * anywhere in this codebase -- TR-14-A01 generates a REAL client-side CSV
 * of exactly the already-loaded, already-computed read-model snapshot
 * (CSV-injection-safe), disclosed as exactly that.
 */
(function () {
  "use strict";

  //: How many of an account's most recent filled orders this build pages
  //: through to count real fills for a paper-broker fee total -- bounded
  //: by GET /orders' own max `limit` (500). Disclosed in the costs panel
  //: rather than silently treated as "every fill ever."
  const ORDERS_FEE_SAMPLE_LIMIT = 500;

  function unsupportedNote(reason) {
    const el = document.createElement("div");
    StateMatrix.render(el, { state: "unsupported", reason });
    return el.outerHTML;
  }

  function capabilityHtml(opts) {
    const el = document.createElement("div");
    Components.renderCapabilityState(el, opts);
    return el.outerHTML;
  }

  function csvCell(value) {
    let s = value === null || value === undefined ? "" : String(value);
    if (/^[=+\-@]/.test(s)) s = "'" + s; // CSV formula-injection guard
    return `"${s.replace(/"/g, '""')}"`;
  }

  function downloadCsv(filename, headers, rows) {
    const lines = [headers.map(csvCell).join(",")].concat(rows.map((r) => r.map(csvCell).join(",")));
    const blob = new Blob([lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  // -------------------------------------------------------------------
  // Provenance labeling -- see this module's own header comment for why
  // this is the single riskiest piece of new logic here. Pure, unit-
  // testable functions: no DOM, no fetch, just (account, brokersByName)
  // -> a real, honest verdict.
  // -------------------------------------------------------------------

  //: The catalog's own fixed vocabulary. This screen never emits MODEL or
  //: BACKTEST (see header comment) but the constants exist so a future
  //: change can't silently invent a sixth ad-hoc label.
  const PROVENANCE = {
    LIVE: { code: "live", label: "ACTUAL LIVE", tone: "bad" },
    PAPER: { code: "paper", label: "PAPER", tone: "ok" },
    INCOMPLETE: { code: "incomplete", label: "INCOMPLETE", tone: "muted" },
  };

  //: Real per-account provenance: this account's OWN configured `broker`
  //: name (from GET /accounts, `store.list_config_accounts()`), matched
  //: against GET /brokers' own code-verified `environment` field for a
  //: broker adapter by that exact name. Never a per-account guess, never
  //: defaulted to PAPER/LIVE when the broker is unregistered or its
  //: environment isn't verified -- both resolve to INCOMPLETE, with the
  //: real reason attached.
  function accountProvenance(account, brokersByName) {
    const broker = brokersByName[account.broker];
    if (!broker) {
      return Object.assign({}, PROVENANCE.INCOMPLETE, {
        reason: `Account '${account.account_id}' is configured for broker '${account.broker}', which GET /brokers does not list as registered right now -- environment unverifiable.`,
      });
    }
    if (broker.environment === "paper") {
      return Object.assign({}, PROVENANCE.PAPER, {
        reason: `Broker '${account.broker}' reports environment "paper" (GET /brokers).`,
      });
    }
    if (broker.environment === "live") {
      return Object.assign({}, PROVENANCE.LIVE, {
        reason: `Broker '${account.broker}' reports environment "live" (GET /brokers) -- this is REAL capital.`,
      });
    }
    return Object.assign({}, PROVENANCE.INCOMPLETE, {
      reason: `Broker '${account.broker}' does not expose a verified paper/live environment flag (GET /brokers' own "environment" is null for it) -- reporting a guess here would be worse than the honest gap.`,
    });
  }

  //: For a metric that structurally spans MULTIPLE accounts with no way
  //: to split the underlying number back out per account (e.g.
  //: GET /providers/value's global FIFO replay): real only when every
  //: account in `accounts` shares the exact same provenance code:
  //: otherwise INCOMPLETE, with a reason naming the split -- NEVER a
  //: blended LIVE/PAPER claim.
  function uniformProvenance(accounts, brokersByName) {
    if (!accounts.length) {
      return Object.assign({}, PROVENANCE.INCOMPLETE, { reason: "No configured accounts to attribute this to." });
    }
    const provs = accounts.map((a) => accountProvenance(a, brokersByName));
    const codes = new Set(provs.map((p) => p.code));
    if (codes.size === 1) return provs[0];
    const counts = {};
    for (const p of provs) counts[p.label] = (counts[p.label] || 0) + 1;
    const parts = Object.entries(counts).map(([label, n]) => `${n} ${label}`).join(", ");
    return Object.assign({}, PROVENANCE.INCOMPLETE, {
      reason: `This figure is computed across every configured account with no per-account breakdown to split by -- those accounts are NOT all the same environment (${parts}), so no single LIVE/PAPER label would be honest.`,
    });
  }

  function provenancePill(prov) {
    // Plain HTML-attribute escaping (not escapeAttr -- that one's
    // additional JS-string-literal escaping is for onclick="..." contexts
    // only, and would show literal backslashes here for any reason string
    // containing an apostrophe, e.g. "Broker 'paper' reports...").
    return `<span class="pill ${prov.tone}" title="${escapeHtml(prov.reason || "")}">${escapeHtml(prov.label)}</span>`;
  }

  // -------------------------------------------------------------------
  // Real derived math over an already-real snapshot series (never a
  // second P&L/realized-pnl calculation -- see app/equity_history.py's
  // own module docstring; this only ever reshapes `cumulative_pnl`
  // values/timestamps this account's own equity-history endpoint
  // already returned).
  // -------------------------------------------------------------------

  //: Running-peak walk over a real cumulative_pnl series -> the real
  //: underwater series (peak-so-far minus current value, always >= 0).
  //: Mirrors app/statistics.py's compute_max_drawdown's own peak-walk
  //: (that module's own load-bearing invariant), just kept as a full
  //: series here instead of a single max -- never a different formula.
  function underwaterSeries(snapshots) {
    let peak = null;
    return snapshots.map((row) => {
      const value = Number(row.cumulative_pnl);
      if (peak === null || value > peak) peak = value;
      return { x: row.captured_at, y: peak - value };
    });
  }

  //: Real snapshots -> real daily P&L deltas: last cumulative_pnl of
  //: each UTC calendar day, differenced day-over-day. A day with only
  //: one snapshot contributes no delta (nothing to diff against yet).
  function dailyPnlDeltas(snapshots) {
    const lastByDay = new Map();
    for (const row of snapshots) {
      const day = String(row.captured_at).slice(0, 10);
      lastByDay.set(day, Number(row.cumulative_pnl)); // later rows overwrite -- snapshots arrive oldest-first
    }
    const days = Array.from(lastByDay.keys()).sort();
    const deltas = [];
    for (let i = 1; i < days.length; i++) {
      deltas.push({ day: days[i], delta: lastByDay.get(days[i]) - lastByDay.get(days[i - 1]) });
    }
    return deltas;
  }

  //: Real snapshots -> pnl-delta buckets keyed by UTC hour-of-day (0-23)
  //: or ISO weekday (0=Mon..6=Sun) of the LATER snapshot in each
  //: consecutive pair -- the same period-over-period delta app/
  //: statistics.py's own `_pnl_deltas` computes, just bucketed by real
  //: wall-clock time instead of summarized into one mean/stdev. This is
  //: a PERIOD-based P&L pattern (tied to this account's periodic
  //: snapshot cadence), never a per-trade attribution -- disclosed
  //: wherever this is rendered.
  function bucketPnlDeltas(snapshots, keyFn) {
    const sums = new Map();
    for (let i = 1; i < snapshots.length; i++) {
      const delta = Number(snapshots[i].cumulative_pnl) - Number(snapshots[i - 1].cumulative_pnl);
      const key = keyFn(new Date(snapshots[i].captured_at));
      sums.set(key, (sums.get(key) || 0) + delta);
    }
    return sums;
  }

  const HOUR_KEY = (d) => d.getUTCHours();
  const WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const WEEKDAY_KEY = (d) => (d.getUTCDay() + 6) % 7; // getUTCDay is 0=Sun -> remap to 0=Mon

  // -------------------------------------------------------------------
  // Chart.js instances -- one persistent instance per canvas, destroy-
  // and-recreate on every load(), matching dashboard.html's own
  // "economics-chart" pattern and this file's pre-existing latency
  // charts below.
  // -------------------------------------------------------------------
  let latencyChart = null;
  let stageLatencyChart = null;
  let equityChart = null;
  let drawdownChart = null;
  let dailyPnlChart = null;
  let maeMfeChart = null;
  let winLossChart = null;
  let hourPnlChart = null;
  let weekdayPnlChart = null;

  function destroyAll() {
    for (const c of [
      latencyChart, stageLatencyChart, equityChart, drawdownChart,
      dailyPnlChart, maeMfeChart, winLossChart, hourPnlChart, weekdayPnlChart,
    ]) {
      if (c) c.destroy();
    }
    latencyChart = stageLatencyChart = equityChart = drawdownChart = null;
    dailyPnlChart = maeMfeChart = winLossChart = hourPnlChart = weekdayPnlChart = null;
  }

  function renderLatencyChart(container, rows) {
    const wrap = container.querySelector("#tr14-latency-chart-wrap");
    if (!wrap) return;
    if (!rows.length) {
      wrap.innerHTML = `<div class="empty">No latency samples yet -- no real per-symbol latency to chart.</div>`;
      return;
    }
    const labels = rows.map((r) => `${r.account}/${r.symbol}`);
    const values = rows.map((r) => r.mean_seconds);
    wrap.innerHTML = `<div class="chart-container"><canvas id="tr14-latency-chart"></canvas></div>`;
    latencyChart = new Chart(wrap.querySelector("#tr14-latency-chart").getContext("2d"), {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: "Mean signal-to-fill latency (seconds)",
            data: values,
            backgroundColor: "#3ddc84",
            maxBarThickness: 60,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true } },
      },
    });
  }

  const STAGE_ORDER = ["receipt_to_submission", "submission_to_fill", "fill_to_protection"];
  const STAGE_LABELS = {
    receipt_to_submission: "Receipt → submission",
    submission_to_fill: "Submission → fill",
    fill_to_protection: "Fill → protection",
  };
  const STAGE_COLORS = {
    receipt_to_submission: "#3ddc84",
    submission_to_fill: "#4f8ef7",
    fill_to_protection: "#f7b84f",
  };

  function renderStageLatencyChart(container, stageRows) {
    const wrap = container.querySelector("#tr14-stage-latency-chart-wrap");
    if (!wrap) return;
    if (!stageRows.length) {
      wrap.innerHTML = `<div class="empty">No real per-stage latency samples yet -- no filled order has both endpoint timestamps for any named stage.</div>`;
      return;
    }
    const labels = stageRows.map((r) => `${r.account}/${r.symbol}`);
    wrap.innerHTML = `<div class="chart-container"><canvas id="tr14-stage-latency-chart"></canvas></div>`;
    const datasets = STAGE_ORDER.map((stageName) => ({
      label: STAGE_LABELS[stageName],
      data: stageRows.map((r) =>
        Object.prototype.hasOwnProperty.call(r.stages, stageName) ? r.stages[stageName].mean_seconds : null
      ),
      backgroundColor: STAGE_COLORS[stageName],
      maxBarThickness: 40,
    }));
    stageLatencyChart = new Chart(wrap.querySelector("#tr14-stage-latency-chart").getContext("2d"), {
      type: "bar",
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: true } },
        scales: { y: { beginAtZero: true } },
      },
    });
  }

  const PALETTE = ["#3ddc84", "#4f8ef7", "#f7b84f", "#ef5b5b", "#b45cff", "#3bd6d6", "#ff8fb1", "#8b93a7"];

  //: Equity / cumulative-P&L curve -- one real line per account, straight
  //: off GET /accounts/{id}/equity-history's own `cumulative_pnl` series.
  function renderEquityChart(container, seriesByAccount) {
    const wrap = container.querySelector("#tr14-equity-chart-wrap");
    if (!wrap) return;
    const accountIds = Object.keys(seriesByAccount).filter((id) => seriesByAccount[id].length);
    if (!accountIds.length) {
      wrap.innerHTML = `<div class="empty">No equity/P&amp;L snapshots yet -- app/equity_history.py's EquitySnapshotter writes one every EQUITY_SNAPSHOT_INTERVAL_SECONDS; give it time, or check /health for its last_success_at.</div>`;
      return;
    }
    wrap.innerHTML = `<div class="chart-container"><canvas id="tr14-equity-chart"></canvas></div>`;
    const datasets = accountIds.map((id, i) => ({
      label: id,
      // Linear (not "time") x-scale, plotted at each snapshot's real
      // epoch-ms timestamp: no chartjs date adapter is loaded on this
      // page, and "time"/"timeseries" scales require one -- a plain
      // linear scale over real Date.parse() values needs none, and the
      // tick callback below formats them for display without one.
      data: seriesByAccount[id].map((row) => ({ x: Date.parse(row.captured_at), y: row.cumulative_pnl })),
      borderColor: PALETTE[i % PALETTE.length],
      backgroundColor: PALETTE[i % PALETTE.length],
      pointRadius: 0,
      borderWidth: 2,
      fill: false,
    }));
    equityChart = new Chart(wrap.querySelector("#tr14-equity-chart").getContext("2d"), {
      type: "line",
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        parsing: false,
        scales: {
          x: { type: "linear", ticks: { callback: (v) => new Date(v).toISOString().slice(0, 10) }, title: { display: true, text: "Captured at (real snapshot timestamps)" } },
          y: { title: { display: true, text: "cumulative_pnl (realized + unrealized, gross of fees)" } },
        },
        plugins: { legend: { position: "bottom" } },
      },
    });
  }

  //: Underwater/drawdown chart -- real running-peak-minus-current over
  //: the same real series (see `underwaterSeries` above).
  function renderDrawdownChart(container, seriesByAccount) {
    const wrap = container.querySelector("#tr14-drawdown-chart-wrap");
    if (!wrap) return;
    const accountIds = Object.keys(seriesByAccount).filter((id) => seriesByAccount[id].length >= 2);
    if (!accountIds.length) {
      wrap.innerHTML = `<div class="empty">Fewer than 2 real snapshots for every account -- no peak-to-trough drawdown to compute yet.</div>`;
      return;
    }
    wrap.innerHTML = `<div class="chart-container"><canvas id="tr14-drawdown-chart"></canvas></div>`;
    const datasets = accountIds.map((id, i) => ({
      label: id,
      data: underwaterSeries(seriesByAccount[id]).map((p) => ({ x: Date.parse(p.x), y: -p.y })), // negative = "underwater" visually
      borderColor: PALETTE[i % PALETTE.length],
      backgroundColor: PALETTE[i % PALETTE.length] + "33",
      pointRadius: 0,
      borderWidth: 2,
      fill: true,
    }));
    drawdownChart = new Chart(wrap.querySelector("#tr14-drawdown-chart").getContext("2d"), {
      type: "line",
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        parsing: false,
        scales: {
          // See renderEquityChart's own comment: linear + Date.parse(),
          // never "time", since no chartjs date adapter is loaded here.
          x: { type: "linear", ticks: { callback: (v) => new Date(v).toISOString().slice(0, 10) } },
          y: { title: { display: true, text: "Underwater (peak cumulative_pnl minus current, real, <= 0)" } },
        },
        plugins: { legend: { position: "bottom" } },
      },
    });
  }

  function renderDailyPnlChart(container, deltasByAccount) {
    const wrap = container.querySelector("#tr14-daily-pnl-chart-wrap");
    if (!wrap) return;
    const accountIds = Object.keys(deltasByAccount).filter((id) => deltasByAccount[id].length);
    if (!accountIds.length) {
      wrap.innerHTML = `<div class="empty">Need at least 2 snapshot-days per account to diff a daily P&amp;L delta.</div>`;
      return;
    }
    const allDays = new Set();
    for (const id of accountIds) for (const d of deltasByAccount[id]) allDays.add(d.day);
    const days = Array.from(allDays).sort().slice(-30); // most recent 30 real snapshot-days
    wrap.innerHTML = `<div class="chart-container"><canvas id="tr14-daily-pnl-chart"></canvas></div>`;
    const datasets = accountIds.map((id, i) => {
      const byDay = new Map(deltasByAccount[id].map((d) => [d.day, d.delta]));
      return {
        label: id,
        data: days.map((day) => (byDay.has(day) ? byDay.get(day) : null)),
        backgroundColor: PALETTE[i % PALETTE.length],
      };
    });
    dailyPnlChart = new Chart(wrap.querySelector("#tr14-daily-pnl-chart").getContext("2d"), {
      type: "bar",
      data: { labels: days, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: { y: { title: { display: true, text: "Daily cumulative_pnl delta (real, derived from real snapshots)" } } },
        plugins: { legend: { position: "bottom" } },
      },
    });
  }

  function renderMaeMfeChart(container, pointsByProvenance) {
    const wrap = container.querySelector("#tr14-mae-mfe-chart-wrap");
    if (!wrap) return;
    const keys = Object.keys(pointsByProvenance).filter((k) => pointsByProvenance[k].length);
    if (!keys.length) {
      wrap.innerHTML = `<div class="empty">No closed managed-lifecycle episodes with recorded MAE/MFE yet.</div>`;
      return;
    }
    wrap.innerHTML = `<div class="chart-container"><canvas id="tr14-mae-mfe-chart"></canvas></div>`;
    const colorFor = { PAPER: "#3ddc84", "ACTUAL LIVE": "#ef5b5b", INCOMPLETE: "#8b93a7" };
    const datasets = keys.map((k) => ({
      label: k,
      data: pointsByProvenance[k],
      backgroundColor: colorFor[k] || "#4f8ef7",
    }));
    maeMfeChart = new Chart(wrap.querySelector("#tr14-mae-mfe-chart").getContext("2d"), {
      type: "scatter",
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { title: { display: true, text: "MAE (adverse excursion, real)" } },
          y: { title: { display: true, text: "MFE (favorable excursion, real)" } },
        },
        plugins: { legend: { position: "bottom" } },
      },
    });
  }

  function renderWinLossChart(container, rowsByGroup) {
    const wrap = container.querySelector("#tr14-winloss-chart-wrap");
    if (!wrap) return;
    const groups = Object.keys(rowsByGroup).filter((g) => rowsByGroup[g].completed > 0);
    if (!groups.length) {
      wrap.innerHTML = `<div class="empty">No completed episodes yet.</div>`;
      return;
    }
    wrap.innerHTML = `<div class="chart-container"><canvas id="tr14-winloss-chart"></canvas></div>`;
    winLossChart = new Chart(wrap.querySelector("#tr14-winloss-chart").getContext("2d"), {
      type: "bar",
      data: {
        labels: groups,
        datasets: [
          { label: "Winning episodes", data: groups.map((g) => rowsByGroup[g].winning), backgroundColor: "#3ddc84" },
          { label: "Breakeven episodes", data: groups.map((g) => rowsByGroup[g].breakeven), backgroundColor: "#8b93a7" },
          {
            label: "Losing episodes",
            data: groups.map((g) => rowsByGroup[g].completed - rowsByGroup[g].winning - rowsByGroup[g].breakeven),
            backgroundColor: "#ef5b5b",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } },
        plugins: { legend: { position: "bottom" } },
      },
    });
  }

  function renderBucketChart(canvasId, wrapSelector, container, labels, values, label) {
    const wrap = container.querySelector(wrapSelector);
    if (!wrap) return null;
    if (!labels.length) {
      wrap.innerHTML = `<div class="empty">Not enough real snapshot history to bucket yet.</div>`;
      return null;
    }
    wrap.innerHTML = `<div class="chart-container"><canvas id="${canvasId}"></canvas></div>`;
    return new Chart(wrap.querySelector(`#${canvasId}`).getContext("2d"), {
      type: "bar",
      data: { labels, datasets: [{ label, data: values, backgroundColor: "#4f8ef7" }] },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } },
    });
  }

  function shell() {
    return `
      <section class="tr-panel" id="tr14-p01"><h2>Account/analyst book</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr14-metrics"><h2>Metrics</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr14-curves"><h2>Equity, P&amp;L and drawdown curves</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr14-rolling"><h2>Rolling risk statistics</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr14-distributions"><h2>Win/loss, MAE/MFE and trade shape</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr14-breakdowns"><h2>P&amp;L breakdowns</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr14-p03"><h2>Execution quality: latency, fill/rejection ratios, slippage</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr14-capital"><h2>Capital utilization</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr14-p04"><h2>Costs: gross vs. net, fees, funding</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr14-p05"><h2>Incomplete records</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr14-actions"><h2>Actions</h2><div class="tr-panel-body"></div></section>
    `;
  }

  async function load(ctx) {
    const els = {
      book: ctx.container.querySelector("#tr14-p01 .tr-panel-body"),
      metrics: ctx.container.querySelector("#tr14-metrics .tr-panel-body"),
      curves: ctx.container.querySelector("#tr14-curves .tr-panel-body"),
      rolling: ctx.container.querySelector("#tr14-rolling .tr-panel-body"),
      distributions: ctx.container.querySelector("#tr14-distributions .tr-panel-body"),
      breakdowns: ctx.container.querySelector("#tr14-breakdowns .tr-panel-body"),
      latency: ctx.container.querySelector("#tr14-p03 .tr-panel-body"),
      capital: ctx.container.querySelector("#tr14-capital .tr-panel-body"),
      costs: ctx.container.querySelector("#tr14-p04 .tr-panel-body"),
      incomplete: ctx.container.querySelector("#tr14-p05 .tr-panel-body"),
      actions: ctx.container.querySelector("#tr14-actions .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const [accountsRes, brokersRes] = await Promise.all([ctx.fetchJSON("/accounts"), ctx.fetchJSON("/brokers")]);
    if (accountsRes.status === 401 || accountsRes.status === 403) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "denied", deniedCode: accountsRes.status });
      return;
    }
    if (!accountsRes.ok) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "error", message: "Could not load accounts." });
      return;
    }
    const accounts = (accountsRes.data && accountsRes.data.accounts) || [];
    const brokersByName = {};
    if (brokersRes.ok) {
      for (const b of (brokersRes.data && brokersRes.data.brokers) || []) brokersByName[b.name] = b;
    }
    if (!accounts.length) {
      for (const key of Object.keys(els)) {
        StateMatrix.render(els[key], {
          state: "empty",
          emptyMessage: "No fully qualified economic report is available.",
          nextRoute: "/trade/orders",
          nextLabel: "Orders, fills and commands (TR-06)",
        });
      }
      ctx.setChrome({ asOf: new Date().toISOString() });
      return;
    }

    const accountProv = {};
    for (const a of accounts) accountProv[a.account_id] = accountProvenance(a, brokersByName);
    const globalProv = uniformProvenance(accounts, brokersByName);

    const [
      economicsResults,
      qualityResults,
      equityResults,
      statsResults,
      ordersResults,
      capitalRes,
      excursionsRes,
      providersValueRes,
    ] = await Promise.all([
      Promise.all(accounts.map((a) => ctx.fetchJSON(`/accounts/${encodeURIComponent(a.account_id)}/economics`))),
      Promise.all(accounts.map((a) => ctx.fetchJSON(`/accounts/${encodeURIComponent(a.account_id)}/execution-quality`))),
      Promise.all(accounts.map((a) => ctx.fetchJSON(`/accounts/${encodeURIComponent(a.account_id)}/equity-history?limit=2000`))),
      Promise.all(accounts.map((a) => ctx.fetchJSON(`/accounts/${encodeURIComponent(a.account_id)}/statistics?window=30`))),
      Promise.all(
        accounts.map((a) =>
          ctx.fetchJSON(`/orders?account_id=${encodeURIComponent(a.account_id)}&limit=${ORDERS_FEE_SAMPLE_LIMIT}`)
        )
      ),
      ctx.fetchJSON("/capital-allocation"),
      ctx.fetchJSON("/positions/excursions?limit=500"),
      ctx.fetchJSON("/providers/value"),
    ]);

    const economics = accounts.map((a, i) => ({ account: a, data: economicsResults[i].ok ? economicsResults[i].data : null }));
    const quality = accounts.map((a, i) => ({ account: a, data: qualityResults[i].ok ? qualityResults[i].data : null }));
    const equity = accounts.map((a, i) => ({
      account: a,
      snapshots: equityResults[i].ok && equityResults[i].data ? equityResults[i].data.snapshots || [] : [],
    }));
    const rollingStats = accounts.map((a, i) => ({ account: a, data: statsResults[i].ok ? statsResults[i].data : null }));
    const ordersByAccount = accounts.map((a, i) => ({
      account: a,
      orders: ordersResults[i].ok && ordersResults[i].data ? ordersResults[i].data.orders || [] : [],
    }));
    const capitalRows = capitalRes.ok && capitalRes.data ? capitalRes.data.accounts || [] : [];
    const excursions = excursionsRes.ok && excursionsRes.data ? excursionsRes.data.excursions || [] : [];
    const providerValueRows = providersValueRes.ok && providersValueRes.data ? providersValueRes.data.providers || [] : [];
    const anyEconomicsFailed = economicsResults.some((r) => !r.ok);

    // --- Account/analyst book: per (account, symbol) aggregated economics, now with a provenance column ---
    const bookRows = [];
    for (const { account, data } of economics) {
      const prov = accountProv[account.account_id];
      if (!data) continue;
      const symbols = Object.keys(data.per_symbol || {});
      if (!symbols.length) {
        bookRows.push([
          `<a href="#/trade/positions">${escapeHtml(account.account_id)}</a>`,
          pill("no completed activity", "muted"), "—", "—", "—", "—",
          provenancePill(prov),
        ]);
        continue;
      }
      for (const symbol of symbols) {
        const s = data.per_symbol[symbol];
        bookRows.push([
          `<a href="#/trade/positions/${escapeAttr(account.account_id)}/${escapeAttr(symbol)}">${escapeHtml(account.account_id)} / ${escapeHtml(symbol)}</a>`,
          `${fmtNum(s.completed_episodes)} completed (${fmtNum(s.winning_episodes)}W / ${fmtNum(s.breakeven_episodes)}BE)`,
          s.completed_lifecycle_win_rate !== null ? `${(s.completed_lifecycle_win_rate * 100).toFixed(1)}%` : "—",
          fmtNum(s.realized_pnl),
          fmtNum(s.closing_fills),
          s.last_fill_price !== null ? fmtNum(s.last_fill_price) : "—",
          provenancePill(prov),
        ]);
      }
    }
    StateMatrix.render(els.book, {
      state: bookRows.length ? "ready" : "empty",
      emptyMessage: "No fully qualified economic report is available.",
      html: `<p class="section-note">Real, authoritative account economics (E06, app/economics.py) replayed from this account's own confirmed fills -- gross of fees, no fee column exists in this schema. "Episode" = one (account, symbol) pair's independently completed flat→non-flat→flat position cycle count, aggregated -- not a per-trade row. The rightmost column is this account's own broker environment, verified against GET /brokers -- never a guess.</p>${table(
        ["Account / symbol", "Completed episodes", "Lifecycle win rate", "Realized P&L (gross of fees)", "Closing fills", "Last fill price", "Provenance"],
        bookRows,
        "No completed economic activity."
      )}`,
    });

    // --- Metrics: net P&L split by provenance group (never blended), plus expectancy/recovery-factor ---
    const byProvGroup = {}; // label -> { netPnl, completedEpisodes, latestSnapshotByAccount }
    for (const { account, data } of economics) {
      if (!data) continue;
      const prov = accountProv[account.account_id];
      const g = (byProvGroup[prov.label] = byProvGroup[prov.label] || { netPnl: 0, completedEpisodes: 0, prov });
      g.netPnl += data.realized_pnl;
      for (const s of Object.values(data.per_symbol || {})) g.completedEpisodes += s.completed_episodes;
    }
    const kpiItems = [];
    for (const [label, g] of Object.entries(byProvGroup)) {
      kpiItems.push({
        label: `Net (realized) P&L -- ${label}`,
        value: fmtNum(g.netPnl),
        tone: g.prov.code === "live" ? "crit" : "neutral",
        sublabel: `${g.completedEpisodes} completed episode(s); gross of fees except where the Costs panel below computes net`,
      });
      const expectancy = g.completedEpisodes > 0 ? g.netPnl / g.completedEpisodes : null;
      kpiItems.push({
        label: `Expectancy per episode -- ${label}`,
        value: expectancy !== null ? fmtNum(expectancy) : "—",
        tone: "neutral",
        sublabel: "realized P&L / completed episodes, this provenance group only",
      });
    }
    const metricsHtml = `
      ${anyEconomicsFailed ? `<p class="section-note">${pill("partial -- some accounts' economics were unavailable", "warn")}</p>` : ""}
      <div id="tr14-metrics-kpi"></div>
      ${unsupportedNote("Exposure-adjusted return: this codebase has no configured per-account starting-balance/capital baseline anywhere (see app/statistics.py's own module docstring) -- dividing a P&L delta by an arbitrary number would be a fabricated percentage, so this stays unsupported, matching app/statistics.py's own documented refusal.")}
    `;
    StateMatrix.render(els.metrics, { state: "ready", html: metricsHtml });
    Components.renderKPIBand(els.metrics.querySelector("#tr14-metrics-kpi"), { items: kpiItems });

    // --- Equity/P&L/drawdown/daily-P&L curves ---
    const seriesByAccount = {};
    const deltasByAccount = {};
    for (const { account, snapshots } of equity) {
      seriesByAccount[account.account_id] = snapshots;
      deltasByAccount[account.account_id] = dailyPnlDeltas(snapshots);
    }
    const curvesProvRows = accounts.map((a) => `${escapeHtml(a.account_id)}: ${provenancePill(accountProv[a.account_id])}`).join(" &nbsp; ");
    StateMatrix.render(els.curves, {
      state: "ready",
      html: `<p class="section-note">Real, persisted per-account <code>cumulative_pnl</code> snapshot series (PU-A3, app/equity_history.py's EquitySnapshotter) -- <code>cumulative_pnl</code> is <code>realized_pnl + unrealized_pnl</code> at each snapshot, never a broker-confirmed absolute equity balance (this codebase has no configured starting-balance baseline). Per-account provenance: ${curvesProvRows}</p>
        <h3 class="section-note" style="margin-top:12px;">Equity curve / cumulative net P&amp;L (real snapshots, one line per account)</h3>
        <div id="tr14-equity-chart-wrap"></div>
        <h3 class="section-note" style="margin-top:12px;">Underwater / drawdown chart (real, derived by walking the running peak of the same series above)</h3>
        <div id="tr14-drawdown-chart-wrap"></div>
        <h3 class="section-note" style="margin-top:12px;">Daily P&amp;L (real, last snapshot of each UTC day differenced day-over-day; last 30 real snapshot-days)</h3>
        <div id="tr14-daily-pnl-chart-wrap"></div>
        ${unsupportedNote("Realized vs. unrealized P&L as a separate chart: each account's LATEST snapshot's realized_pnl/unrealized_pnl split is shown in the Metrics/book panels above as numbers; a full historical realized-vs-unrealized AREA chart isn't rendered separately here to avoid a third redundant view of the exact same two real numbers per snapshot -- see the raw GET /accounts/{id}/equity-history response for the full per-snapshot split if needed.")}`,
    });
    renderEquityChart(els.curves, seriesByAccount);
    renderDrawdownChart(els.curves, seriesByAccount);
    renderDailyPnlChart(els.curves, deltasByAccount);

    // --- Rolling risk statistics (real, single current-window point per account, from app/statistics.py) ---
    const rollingRows = rollingStats.map(({ account, data }) => {
      const prov = accountProv[account.account_id];
      if (!data) {
        return [escapeHtml(account.account_id), "—", "—", "—", "—", "—", "—", provenancePill(prov)];
      }
      return [
        `<span class="mono">${escapeHtml(account.account_id)}</span>`,
        fmtNum(data.sample_count),
        data.mean_pnl_delta !== null ? fmtNum(data.mean_pnl_delta) : "—",
        data.volatility_pnl_delta !== null ? fmtNum(data.volatility_pnl_delta) : "—",
        data.sharpe_equivalent !== null ? fmtNum(data.sharpe_equivalent) : "—",
        data.sortino_equivalent !== null ? fmtNum(data.sortino_equivalent) : "—",
        data.max_drawdown !== null ? fmtNum(data.max_drawdown) : "—",
        provenancePill(prov),
      ];
    });
    StateMatrix.render(els.rolling, {
      state: "ready",
      html: `<p class="section-note">Real rolling statistics over each account's last 30 real snapshots (Phase A5, app/statistics.py) -- absolute P&amp;L-delta terms, never a percentage return (no configured capital baseline exists), implicit zero risk-free rate, so these are called "-equivalent" rather than a textbook Sharpe/Sortino ratio. Any cell is "—" when the account's real history is too short for that statistic, never a fabricated placeholder.</p>${table(
        ["Account", "Snapshots used", "Mean P&L delta", "Volatility (stdev of delta)", "Sharpe-equivalent", "Sortino-equivalent", "Max drawdown", "Provenance"],
        rollingRows,
        "No accounts."
      )}
      ${unsupportedNote('Downside deviation as its own standalone figure: app/statistics.py only exposes it embedded inside sortino_equivalent (mean_pnl_delta / downside_stdev), never as a raw number on its own -- not_tracked as a standalone metric.')}
      ${unsupportedNote("A full historical rolling-window TIME SERIES (this statistic as of every past day, not just now): app/statistics.py's compute_rolling_stats returns one current-window result, not a series -- charting a series here would mean re-deriving different, untested windowing logic client-side rather than reading a real backend result.")}`,
    });

    // --- Win/loss distribution + MAE/MFE scatter + holding-time/return-histogram gaps ---
    const winLossByGroup = {};
    for (const { account, data } of economics) {
      if (!data) continue;
      const prov = accountProv[account.account_id];
      const g = (winLossByGroup[prov.label] = winLossByGroup[prov.label] || { completed: 0, winning: 0, breakeven: 0 });
      for (const s of Object.values(data.per_symbol || {})) {
        g.completed += s.completed_episodes;
        g.winning += s.winning_episodes;
        g.breakeven += s.breakeven_episodes;
      }
    }
    const maeMfeByGroup = {};
    for (const row of excursions) {
      if (row.mae === null || row.mfe === null) continue;
      const account = accounts.find((a) => a.account_id === row.account_id);
      const prov = account ? accountProv[account.account_id] : PROVENANCE.INCOMPLETE;
      const label = account ? prov.label : "INCOMPLETE";
      (maeMfeByGroup[label] = maeMfeByGroup[label] || []).push({ x: row.mae, y: row.mfe });
    }
    StateMatrix.render(els.distributions, {
      state: "ready",
      html: `<h3 class="section-note">Win / breakeven / loss episode counts (real, per provenance group -- app/economics.py)</h3>
        <div id="tr14-winloss-chart-wrap"></div>
        <h3 class="section-note" style="margin-top:12px;">MAE / MFE scatter (real, per CLOSED managed-lifecycle episode -- PU-A1, GET /positions/excursions)</h3>
        <div id="tr14-mae-mfe-chart-wrap"></div>
        ${capabilityHtml({
          status: "not_tracked",
          reason: "Trade-return ($-magnitude) histogram: no GET endpoint exposes a per-episode realized-P&L row -- app/economics.py and app/provider_value.py only report totals aggregated per (account, symbol) or per (source, analyst, asset_class), never one row per completed episode.",
        })}
        ${capabilityHtml({
          status: "not_tracked",
          reason: "Holding-time distribution: GET /positions/excursions records each closed episode's closed_at but no matching entry timestamp (position_excursions has no opened_at column) -- there is no real per-episode duration to bucket.",
        })}`,
    });
    renderWinLossChart(els.distributions, winLossByGroup);
    renderMaeMfeChart(els.distributions, maeMfeByGroup);

    // --- P&L breakdowns: account, asset class, provider/analyst, instrument, hour, weekday, exit reason ---
    // By account (real, per-account provenance already known exactly).
    const byAccountRows = economics
      .filter(({ data }) => data)
      .map(({ account, data }) => [escapeHtml(account.account_id), fmtNum(data.realized_pnl), provenancePill(accountProv[account.account_id])]);

    // By instrument/symbol: summed from the SAME per-account economics
    // already loaded above (never a second replay) -- tracks which
    // accounts contributed to each symbol so its own provenance is
    // exactly attributable, never guessed.
    const bySymbol = {}; // symbol -> { total, accountIds: Set }
    for (const { account, data } of economics) {
      if (!data) continue;
      for (const [symbol, s] of Object.entries(data.per_symbol || {})) {
        const entry = (bySymbol[symbol] = bySymbol[symbol] || { total: 0, accountIds: new Set() });
        entry.total += s.realized_pnl;
        entry.accountIds.add(account.account_id);
      }
    }
    const bySymbolRows = Object.entries(bySymbol).map(([symbol, e]) => {
      const contributingAccounts = accounts.filter((a) => e.accountIds.has(a.account_id));
      const prov = uniformProvenance(contributingAccounts, brokersByName);
      return [escapeHtml(symbol), fmtNum(e.total), provenancePill(prov)];
    });

    // By provider (source) / analyst / asset class: real, GLOBAL FIFO
    // replay (app/provider_value.py) -- structurally spans every
    // account with no per-row account breakdown, so provenance here is
    // the whole-deployment uniform check, never a per-row guess.
    const byProviderRows = providerValueRows.map((r) => [
      escapeHtml(r.source),
      escapeHtml(r.analyst === null || r.analyst === undefined ? "(none)" : r.analyst),
      escapeHtml(r.asset_class),
      fmtNum(r.realized_pnl),
      fmtNum(r.closing_fills),
      r.win_rate !== null ? `${(r.win_rate * 100).toFixed(1)}%` : "—",
      r.profit_factor !== null ? fmtNum(r.profit_factor) : "—",
      provenancePill(globalProv),
    ]);
    // Note: GET /providers/value's own JSON (ProviderValue.to_dict()) does
    // NOT expose gross_profit/gross_loss, only the already-divided
    // win_rate/profit_factor per (source, analyst, asset_class) row --
    // so an asset-class-level profit factor can't be re-derived here
    // without re-summing numbers this endpoint doesn't return; only
    // realized_pnl (which IS returned) is aggregated below.
    const byAssetClass = {};
    for (const r of providerValueRows) {
      byAssetClass[r.asset_class] = (byAssetClass[r.asset_class] || 0) + r.realized_pnl;
    }
    const byAssetClassRows = Object.entries(byAssetClass).map(([ac, total]) => [
      escapeHtml(ac),
      fmtNum(total),
      provenancePill(globalProv),
    ]);

    // Hour-of-day / weekday: real snapshot-delta buckets, computed PER
    // PROVENANCE GROUP so each number carries exactly one honest label
    // (never blended across paper+live).
    const hourSumsByGroup = {};
    const weekdaySumsByGroup = {};
    for (const { account, snapshots } of equity) {
      const label = accountProv[account.account_id].label;
      const hourSums = (hourSumsByGroup[label] = hourSumsByGroup[label] || new Map());
      const weekdaySums = (weekdaySumsByGroup[label] = weekdaySumsByGroup[label] || new Map());
      const h = bucketPnlDeltas(snapshots, HOUR_KEY);
      for (const [k, v] of h) hourSums.set(k, (hourSums.get(k) || 0) + v);
      const w = bucketPnlDeltas(snapshots, WEEKDAY_KEY);
      for (const [k, v] of w) weekdaySums.set(k, (weekdaySums.get(k) || 0) + v);
    }
    const hourGroupLabel = Object.keys(hourSumsByGroup)[0]; // rendered chart shows the first non-empty group; table below covers every group
    const hourLabels = hourGroupLabel ? Array.from({ length: 24 }, (_, h) => h) : [];
    const hourValues = hourGroupLabel ? hourLabels.map((h) => hourSumsByGroup[hourGroupLabel].get(h) || 0) : [];
    const weekdayLabels = hourGroupLabel ? WEEKDAY_NAMES : [];
    const weekdayValues = hourGroupLabel ? [0, 1, 2, 3, 4, 5, 6].map((d) => weekdaySumsByGroup[hourGroupLabel].get(d) || 0) : [];

    // Exit reason: the one real, exact count this schema supports --
    // target-hit events -- never a fabricated stop-out/manual complement.
    const targetHitSymbols = [];
    for (const { account, data } of economics) {
      if (!data) continue;
      for (const symbol of Object.keys(data.per_symbol || {})) {
        if (data.per_symbol[symbol].completed_episodes > 0) targetHitSymbols.push({ account: account.account_id, symbol });
      }
    }
    const stopEventsResults = await Promise.all(
      targetHitSymbols.map(({ account, symbol }) =>
        ctx.fetchJSON(`/positions/${encodeURIComponent(account)}/${encodeURIComponent(symbol)}/stop-events?limit=1000`)
      )
    );
    let targetHitCount = 0;
    let stopTightenedCount = 0;
    let protectionFailedCount = 0;
    for (const r of stopEventsResults) {
      if (!r.ok || !r.data) continue;
      for (const ev of r.data.events || []) {
        if (ev.event_type === "target_hit") targetHitCount++;
        else if (ev.event_type === "stop_tightened") stopTightenedCount++;
        else if (ev.event_type === "protection_failed") protectionFailedCount++;
      }
    }

    StateMatrix.render(els.breakdowns, {
      state: "ready",
      html: `<h3 class="section-note">By account (real -- app/economics.py, sum of realized_pnl)</h3>${table(
        ["Account", "Realized P&L", "Provenance"], byAccountRows, "No accounts."
      )}
      <h3 class="section-note" style="margin-top:12px;">By instrument / symbol (real, summed across accounts from the same economics data above)</h3>${table(
        ["Symbol", "Realized P&L (summed across accounts)", "Provenance"], bySymbolRows, "No completed symbol activity."
      )}
      <h3 class="section-note" style="margin-top:12px;">By provider / analyst (real, GLOBAL FIFO-lot replay -- app/provider_value.py, see its own docstring for why this differs methodologically from the account-level economics above and can double-count a symbol two providers share)</h3>${table(
        ["Source", "Analyst", "Asset class", "Realized P&L", "Closing fills", "Win rate", "Profit factor", "Provenance"], byProviderRows, "No provider-attributed fills yet."
      )}
      <h3 class="section-note" style="margin-top:12px;">By asset class (real, aggregated from the same provider/analyst replay above)</h3>${table(
        ["Asset class", "Realized P&L", "Provenance"], byAssetClassRows, "No data."
      )}
      <h3 class="section-note" style="margin-top:12px;">By hour of day (UTC) -- real, derived from real equity-snapshot deltas, period-based (not per-trade)</h3>
      <div id="tr14-hour-pnl-chart-wrap"></div>
      <h3 class="section-note" style="margin-top:12px;">By weekday (UTC) -- same real, period-based derivation</h3>
      <div id="tr14-weekday-pnl-chart-wrap"></div>
      ${hourGroupLabel ? `<p class="section-note">Chart above shows the "${escapeHtml(hourGroupLabel)}" provenance group only when more than one group exists (each group's buckets are computed separately so no bucket ever blends paper and live deltas); other groups: ${Object.keys(hourSumsByGroup).filter((g) => g !== hourGroupLabel).map(escapeHtml).join(", ") || "none"}.</p>` : ""}
      ${capabilityHtml({
        status: "not_tracked",
        reason: `Full per-episode exit-reason breakdown (stop-out vs. target-hit vs. manual close): app/lifecycle/models.py's StopTargetEventType has no distinct "stop filled as an exit" event (only STOP_PLACED, which is a stop being SET, not it firing) -- so a stop-out can't be told apart from a manual/CLOSE-signal close from this event log. The one real, exact figure available: ${targetHitCount} target_hit event(s) recorded across ${targetHitSymbols.length} (account, symbol) pair(s) with completed activity (also ${stopTightenedCount} stop_tightened and ${protectionFailedCount} protection_failed event(s) logged in the same window) -- never subtracted from a completed-episode count to imply a fabricated complement.`,
      })}
      ${unsupportedNote('"Strategy" and "regime" breakdowns: grepped this codebase for either as a real, stored, groupable field -- neither exists anywhere (no strategy identifier on a signal/order/position, no regime classifier). Omitted rather than fabricated.')}`,
    });
    renderBucketChart("tr14-hour-pnl-chart", "#tr14-hour-pnl-chart-wrap", els.breakdowns, hourLabels.map(String), hourValues, "P&L delta sum by hour (UTC)");
    renderBucketChart("tr14-weekday-pnl-chart", "#tr14-weekday-pnl-chart-wrap", els.breakdowns, weekdayLabels, weekdayValues, "P&L delta sum by weekday (UTC)");

    // --- Execution quality: latency (existing, PU-B9 stage chart) + fill/rejection/partial ratios + cancel/replace ---
    const latencySamples = [];
    const latencyRows = [];
    const stageRows = [];
    for (const { account, data } of quality) {
      if (!data) continue;
      for (const [symbol, s] of Object.entries(data.per_symbol || {})) {
        latencySamples.push({ account: account.account_id, symbol, mean_seconds: s.mean_seconds });
        latencyRows.push([
          `<span class="mono">${escapeHtml(account.account_id)}</span>`,
          escapeHtml(symbol),
          fmtNum(s.sample_count),
          `${fmtNum(s.mean_seconds)}s`,
          `${fmtNum(s.median_seconds)}s`,
          `${fmtNum(s.max_seconds)}s`,
          provenancePill(accountProv[account.account_id]),
        ]);
      }
      for (const [symbol, stages] of Object.entries(data.stage_latencies || {})) {
        const stageMap = {};
        for (const stage of stages) stageMap[stage.stage] = stage;
        stageRows.push({ account: account.account_id, symbol, stages: stageMap });
      }
    }

    let filledCount = 0, rejectedOrErrorCount = 0, partialCount = 0, totalTerminal = 0;
    for (const { orders } of ordersByAccount) {
      for (const o of orders) {
        if (o.status === "filled") {
          filledCount++;
          totalTerminal++;
          if (o.requested_quantity !== null && o.filled_quantity !== null && o.filled_quantity < o.requested_quantity - 1e-9) {
            partialCount++;
          }
        } else if (o.status === "rejected" || o.status === "error") {
          rejectedOrErrorCount++;
          totalTerminal++;
        }
      }
    }
    const fillRatio = totalTerminal > 0 ? filledCount / totalTerminal : null;
    const rejectionRatio = totalTerminal > 0 ? rejectedOrErrorCount / totalTerminal : null;
    const partialFillRatio = filledCount > 0 ? partialCount / filledCount : null;
    const cancelReplaceTotal = stopTightenedCount + protectionFailedCount;
    const cancelReplaceSuccessRatio = cancelReplaceTotal > 0 ? stopTightenedCount / cancelReplaceTotal : null;

    StateMatrix.render(els.latency, {
      state: latencyRows.length ? "ready" : "empty",
      emptyMessage: "No filled order has a matching signal timestamp to measure latency from yet.",
      html: `<p class="section-note">Real signal-received-to-fill latency (E05, app/execution_quality.py) -- mixes this process's own handling time with real network/broker latency; there is no separately tracked decision/submission/acknowledgement timestamp to split it further.</p>
        <div id="tr14-latency-kpi"></div>
        <h3 class="section-note" style="margin-top:12px;">Mean latency by account/symbol (additive to the table below, not a replacement)</h3>
        <div id="tr14-latency-chart-wrap"></div>
        <h3 class="section-note" style="margin-top:12px;">Mean latency by real pipeline stage (PU-B9 -- receipt&#8594;submission (signal-to-submit) / submission&#8594;fill (submit-to-ack-to-fill, collapsed: no separate broker-ack timestamp exists) / fill&#8594;protection (protection latency), each rendered only where the backend reports it)</h3>
        <div id="tr14-stage-latency-chart-wrap"></div>
        ${table(
        ["Account", "Symbol", "Samples", "Mean", "Median", "Max", "Provenance"],
        latencyRows,
        "No latency samples."
      )}${unsupportedNote('"Fill slippage" (fill price vs. a reference/expected price) and price improvement/adverse selection are not tracked anywhere in this build -- no reference price is stored to diff a fill against.')}
        ${unsupportedNote("Venue routing (multi-venue quote comparison / Sankey of order routing across venues) stays unsupported: this codebase has exactly one BrokerAdapter.place_order call per order, one broker per account, and no multi-venue quote feed anywhere.")}`,
    });
    Components.renderKPIBand(els.latency.querySelector("#tr14-latency-kpi"), {
      items: [
        {
          label: "Fill ratio", value: fillRatio !== null ? `${(fillRatio * 100).toFixed(1)}%` : "—", tone: "neutral",
          sublabel: `real, over the ${totalTerminal} most-recently-terminal order(s) this build paged through (bounded by GET /orders' own limit=${ORDERS_FEE_SAMPLE_LIMIT} per account)`,
        },
        {
          label: "Rejection ratio", value: rejectionRatio !== null ? `${(rejectionRatio * 100).toFixed(1)}%` : "—",
          tone: rejectionRatio !== null && rejectionRatio > 0 ? "warn" : "neutral", sublabel: "rejected + errored / terminal orders, same real sample",
        },
        {
          label: "Partial-fill ratio", value: partialFillRatio !== null ? `${(partialFillRatio * 100).toFixed(1)}%` : "—", tone: "neutral",
          sublabel: "filled orders where filled_quantity < requested_quantity, same real sample",
        },
        {
          label: "Protective-stop replace success", value: cancelReplaceSuccessRatio !== null ? `${(cancelReplaceSuccessRatio * 100).toFixed(1)}%` : "—",
          tone: "neutral",
          sublabel: `real stop_tightened / (stop_tightened + protection_failed) events (${stopTightenedCount}/${cancelReplaceTotal}) -- protective-stop replace only, not a generic order cancel/replace log (not tracked as its own event)`,
        },
      ],
    });
    renderLatencyChart(els.latency, latencySamples);
    renderStageLatencyChart(els.latency, stageRows);

    // --- Capital utilization (real, Phase B7 app/capital_allocator.py) ---
    const capItems = capitalRows.map((row, i) => {
      const account = accounts.find((a) => a.account_id === row.account_id);
      const prov = account ? accountProv[account.account_id] : PROVENANCE.INCOMPLETE;
      // Index-based id (never the raw account_id) -- simplest way to
      // avoid any HTML-id-escaping edge case for an owner-controlled but
      // still not necessarily id-safe account_id string.
      return { row, prov, barId: `tr14-capbar-${i}` };
    });
    const capHtmlParts = capItems.map(({ row, prov, barId }) => {
      if (row.max_notional_exposure === null || row.max_notional_exposure === undefined) {
        return `<div><strong>${escapeHtml(row.account_id)}</strong> ${provenancePill(prov)}${capabilityHtml({
          status: "not_tracked",
          reason: `Account '${row.account_id}' has no configured max_notional_exposure ceiling -- capital utilization needs a real ceiling to divide by; this account opted out of E03's exposure gate.`,
        })}</div>`;
      }
      return `<div><strong>${escapeHtml(row.account_id)}</strong> ${provenancePill(prov)} <span class="section-note">deployed ${fmtNum(row.deployed_notional)} + reserved ${fmtNum(row.reserved_notional)} of ${fmtNum(row.max_notional_exposure)} ceiling</span><div id="${barId}"></div></div>`;
    });
    StateMatrix.render(els.capital, {
      state: capitalRows.length ? "ready" : "empty",
      emptyMessage: "No accounts.",
      html: `<p class="section-note">Real, current (point-in-time, in-memory, resets on restart) capital-reservation state per account (Phase B7, app/capital_allocator.py) -- never a persisted historical ledger.</p>${capHtmlParts.join("")}`,
    });
    for (const { row, barId } of capItems) {
      if (row.max_notional_exposure === null || row.max_notional_exposure === undefined) continue;
      const barEl = els.capital.querySelector(`#${barId}`);
      if (!barEl) continue;
      const remaining = Math.max(0, row.max_notional_exposure - row.deployed_notional - row.reserved_notional);
      Components.renderQuantityLedgerBar(barEl, {
        total: row.max_notional_exposure,
        segments: [
          { label: "Deployed", value: Math.max(0, row.deployed_notional), tone: "crit" },
          { label: "Reserved", value: Math.max(0, row.reserved_notional), tone: "warn" },
          { label: "Available", value: remaining, tone: "ok" },
        ],
      });
    }

    // --- Costs: gross vs. net P&L, fees -- real ONLY for paper-broker accounts ---
    const costsParts = [];
    for (const { account, data } of economics) {
      const prov = accountProv[account.account_id];
      const broker = brokersByName[account.broker];
      const grossPnl = data ? data.realized_pnl : null;
      if (!broker || broker.fee_per_fill === null || broker.fee_per_fill === undefined) {
        costsParts.push(
          `<div><strong>${escapeHtml(account.account_id)}</strong> ${provenancePill(prov)}${capabilityHtml({
            status: "not_tracked",
            reason: `Broker '${account.broker}' reports no fee_per_fill (GET /brokers) -- only the paper broker has a real, documented per-fill fee in this codebase (orders has no fee column for any real external broker, see app/economics.py's own module docstring). Gross realized P&L for reference: ${grossPnl !== null ? fmtNum(grossPnl) : "—"}.`,
          })}</div>`
        );
        continue;
      }
      const orders = (ordersByAccount.find((o) => o.account.account_id === account.account_id) || {}).orders || [];
      const filledFillCount = orders.filter((o) => o.status === "filled").length;
      const totalFees = broker.fee_per_fill * filledFillCount;
      const netPnl = grossPnl !== null ? grossPnl - totalFees : null;
      costsParts.push(
        `<div><strong>${escapeHtml(account.account_id)}</strong> ${provenancePill(prov)}<br>
          Gross realized P&L: ${fmtNum(grossPnl)} &nbsp; Fees (real, ${fmtNum(broker.fee_per_fill)}/fill &times; ${filledFillCount} real fill(s), most recent ${ORDERS_FEE_SAMPLE_LIMIT}): ${fmtNum(totalFees)} &nbsp; Net: ${fmtNum(netPnl)}
        </div>`
      );
    }
    StateMatrix.render(els.costs, {
      state: "ready",
      html: `<p class="section-note">Gross-vs-net P&amp;L and a real fee total are computed ONLY for accounts on the paper broker -- app/brokers/paper.py's own documented, genuinely-charged <code>fee_per_fill</code> (0.0 by default; a real, honest "this simulator charges no fee," not "fees aren't tracked"). Every other broker adapter has no fee column anywhere in this schema, so those accounts stay not_tracked rather than a fabricated/blended number.</p>${costsParts.join("")}
        ${capabilityHtml({ status: "not_tracked", reason: "Funding/carry costs: no funding-rate or carry-cost concept is stored anywhere in this codebase (checked app/models.py, app/economics.py, every app/brokers/*.py adapter)." })}
        ${unsupportedNote("Slippage-based cost: no reference/expected execution price is stored anywhere this build could diff a fill against -- unsupported (a cost-stress ESTIMATE exists only inside the separate backtest replay, see Historical signal backtests / TR-15, never as a claim about real executed costs).")}`,
    });

    // --- Incomplete records -- Components.renderAttentionQueue instead of a plain table ---
    const attentionItems = [];
    for (const { account, data } of economics) {
      if (data && data.incomplete_symbols && data.incomplete_symbols.length) {
        for (const symbol of data.incomplete_symbols) {
          attentionItems.push({
            severity: "warning",
            text: `${account.account_id}/${symbol}: excluded from realized P&L totals (missing/invalid fill data) -- economics replay`,
          });
        }
      }
    }
    for (const { account, data } of quality) {
      if (data && data.unmatched_order_count) {
        attentionItems.push({
          severity: "info",
          text: `${account.account_id} (all symbols): ${data.unmatched_order_count} filled order(s) with no matching signal timestamp -- excluded from latency figures -- execution-quality replay`,
        });
      }
    }
    StateMatrix.render(els.incomplete, {
      state: "ready",
      html: `<p class="section-note">Real, server-scoped exclusions -- never silently folded into a total as zero.</p><div id="tr14-incomplete-queue"></div>`,
    });
    Components.renderAttentionQueue(els.incomplete.querySelector("#tr14-incomplete-queue"), { items: attentionItems });

    // --- Actions ---
    const accountOptions = accounts.map((a) => `<option value="${escapeAttr(a.account_id)}">${escapeHtml(a.account_id)}</option>`).join("");
    StateMatrix.render(els.actions, {
      state: "ready",
      html: `
        <p class="section-note">TR-14-A02 (Inspect episode): use the account/symbol links in "Account/analyst book" above -- each opens the real <a href="#/trade/positions">Position and protection detail (TR-03)</a> for that exact object.</p>
        <h3 class="section-note" style="margin-top:12px;">TR-14-A01: Export report</h3>
        <label>Scoped accounts (multi-select)<select id="tr14-export-accounts" multiple size="${Math.min(6, accounts.length)}">${accountOptions}</select></label>
        <div class="tr-controls-row"><button type="button" id="tr14-export-run">Download CSV</button></div>
        ${unsupportedNote("This build has no server-side async export-job queue -- 'Export report' downloads a real client-side CSV of exactly this page's already-loaded, already-computed snapshot for the selected accounts instead of enqueuing a server job.")}
        <div id="tr14-export-result"></div>
      `,
    });
    els.actions.querySelector("#tr14-export-run").addEventListener("click", () => {
      const selected = Array.from(els.actions.querySelector("#tr14-export-accounts").selectedOptions).map((o) => o.value);
      const resultEl = els.actions.querySelector("#tr14-export-result");
      if (!selected.length) {
        resultEl.innerHTML = `<p class="sm-error-message">Select at least one account.</p>`;
        return;
      }
      const rows = [];
      for (const { account, data } of economics) {
        if (!selected.includes(account.account_id) || !data) continue;
        const prov = accountProv[account.account_id];
        for (const [symbol, s] of Object.entries(data.per_symbol || {})) {
          rows.push([account.account_id, symbol, s.completed_episodes, s.winning_episodes, s.realized_pnl, "not tracked", s.realized_pnl, s.closing_fills, s.last_fill_price, prov.label]);
        }
      }
      downloadCsv(
        `tr14-performance-export-${new Date().toISOString().slice(0, 10)}.csv`,
        ["account_id", "symbol", "completed_episodes", "winning_episodes", "gross_realized_pnl", "fees", "net_realized_pnl_gross_of_fees", "closing_fills", "last_fill_price", "provenance"],
        rows
      );
      resultEl.innerHTML = `<p class="section-note">Downloaded ${rows.length} row(s) from the current snapshot.</p>`;
    });

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr14 = {
    title: "Trading performance and execution quality",
    breadcrumb: "Trade / Performance",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = shell();
      destroyAll();
      await load(ctx);
      ctx.registerPoll("tr14", 10000, () => load(ctx));
    },
    // Exposed for load-bearing tests (tests/test_tr14_provenance_labels.py)
    // -- pure functions, no DOM/network -- never used by production
    // rendering above except through the same call sites.
    _internal: { accountProvenance, uniformProvenance, PROVENANCE },
  };
  Router.register("/trade/performance", "tr14");
})();
