/* TR-15: Historical signal backtests (`#/trade/backtests`).
 *
 * Real backing capability:
 *   - POST /backtest (app/main.py's run_backtest, app/backtest/replay.py's
 *     BacktestEngine) -- replays this service's own already-received,
 *     immutable signal history (SignalStore) against caller-supplied local
 *     OHLC CSV files (app/backtest/models.py's CsvPriceHistoryProvider).
 *     Synchronous, in-process, never touches a live broker adapter (see
 *     app/backtest/replay.py's module docstring) -- no live order is ever
 *     placed from this screen. Optional linear cost-stress (E07 bounded,
 *     app/backtest/cost_stress.py) is a flat slippage_bps/fee_per_trade
 *     stress test, never a real broker fee schedule.
 *   - TR-15 persistence (NEW): every POST /backtest run is now durably
 *     saved server-side (app/db.py's `backtest_runs` table, keyed by a
 *     real SHA-256 config hash over the actual replay inputs -- see
 *     app/main.py's `compute_backtest_config_hash`). GET /backtest/runs
 *     lists every persisted run; GET /backtest/runs/{id} returns one run's
 *     full detail (including its trades, a real cumulative-P&L equity
 *     curve, and max drawdown computed by app/statistics.py's
 *     `compute_max_drawdown`). Reloading this screen no longer loses a
 *     completed run.
 *   - GET /backtest/runs/{id}/trades/{signal_id}/market-path re-reads the
 *     run's own persisted CSV path to give the trade explorer a real
 *     historical price path around one specific trade.
 *
 * Honest gaps, disclosed rather than invented (rendered via
 * Components.renderCapabilityState, never bare prose pretending
 * something works):
 *   - No dataset/policy/cost-scenario/resource-profile REGISTRY exists --
 *     this build's real inputs are exactly what POST /backtest's own
 *     BacktestRequest takes.
 *   - No async job QUEUE exists -- POST /backtest runs synchronously.
 *     "Resume job" has no backing: there is no job to resume.
 *   - Capital utilization: UNSUPPORTED. app/backtest/replay.py's own
 *     module docstring states this engine has "no shared-account capital
 *     modeling" -- every signal replays independently at its own
 *     quantity, with no shared capital ceiling across concurrent
 *     positions, so there is no real "% of capital deployed" figure this
 *     engine could honestly report.
 *   - Costs: real ONLY when a run actually requested slippage_bps/
 *     fee_per_trade (E07 bounded cost stress). A run that didn't request
 *     it assumed zero-cost fills -- app/backtest/replay.py's module
 *     docstring: "No slippage/fee modeling" -- and this view says so
 *     explicitly rather than inventing a cost breakdown.
 *   - MAE/MFE: NOT TRACKED. `ReplayedTrade` (app/backtest/replay.py)
 *     records only entry/exit price, not intra-trade price excursion --
 *     there is no real maximum-adverse/favorable-excursion figure to
 *     report.
 *   - Baseline comparison: UNSUPPORTED. This engine has no buy-and-hold
 *     computation and (per app/statistics.py's own documented scope) this
 *     codebase has no starting-balance/capital baseline to compute a real
 *     "current live policy" return series against -- a fabricated
 *     baseline would be worse than none.
 *   - Configuration comparison (Current policy vs candidate A vs
 *     candidate B) is built as: pick 2+ REAL persisted runs and compare
 *     their real metrics side by side -- never a UI that pretends to run
 *     something it isn't.
 */
(function () {
  "use strict";

  function unsupportedNote(reason) {
    const el = document.createElement("div");
    Components.renderCapabilityState(el, { status: "unsupported", reason });
    return el.outerHTML;
  }
  function notTrackedNote(reason) {
    const el = document.createElement("div");
    Components.renderCapabilityState(el, { status: "not_tracked", reason });
    return el.outerHTML;
  }

  function shell() {
    return `
      <section class="tr-panel" id="tr15-p01"><h2>History catalog</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr15-p02"><h2>Coverage check</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr15-p03"><h2>Run configuration</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr15-p04"><h2>Persisted runs</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr15-p05"><h2>Research report</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr15-p06"><h2>Configuration comparison</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr15-p07"><h2>Trade explorer</h2><div class="tr-panel-body"></div></section>
    `;
  }

  // Lightweight in-memory cache of full run details this tab has already
  // fetched from GET /backtest/runs/{id} -- purely to avoid refetching the
  // same real persisted run repeatedly while the operator clicks around;
  // the actual source of truth is always the server (app/db.py's
  // backtest_runs table), never this cache alone. A page reload starts
  // this cache empty and re-fetches from the real persisted list, which is
  // the whole point of TR-15's persistence work.
  const runDetailCache = new Map();
  let selectedRunId = null;
  const comparisonSelection = new Set();

  let equityChart = null;
  let distributionChart = null;
  let marketPathChart = null;

  function computeEquityCurveForChart(equityCurve) {
    return (equityCurve || []).map((p) => ({ x: p.captured_at, y: p.cumulative_pnl }));
  }

  function renderEquityChart(wrap, run) {
    if (equityChart) {
      equityChart.destroy();
      equityChart = null;
    }
    const curve = computeEquityCurveForChart(run.equity_curve);
    if (!curve.length) {
      wrap.innerHTML = `<div class="empty">No resolved trade has both an exit time and a P&amp;L in this run -- no real equity curve to chart yet (never shown as a fabricated flat line).</div>`;
      return;
    }
    wrap.innerHTML = `<div class="chart-container"><canvas id="tr15-equity-chart"></canvas></div>`;
    equityChart = new Chart(wrap.querySelector("#tr15-equity-chart").getContext("2d"), {
      type: "line",
      data: {
        labels: curve.map((p) => p.x),
        datasets: [
          {
            label: "Cumulative P&L (resolved trades, by real exit_time)",
            data: curve.map((p) => p.y),
            borderColor: "#3ddc84",
            backgroundColor: "rgba(61, 220, 132, 0.15)",
            fill: true,
            tension: 0,
            pointRadius: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: false } },
      },
    });
  }

  function renderDistributionChart(wrap, trades) {
    if (distributionChart) {
      distributionChart.destroy();
      distributionChart = null;
    }
    const pnls = trades
      .filter((t) => (t.outcome === "win" || t.outcome === "loss") && t.pnl !== null && t.pnl !== undefined)
      .map((t) => t.pnl);
    if (!pnls.length) {
      wrap.innerHTML = `<div class="empty">No resolved (win/loss) trade in this run has a real P&amp;L to distribute.</div>`;
      return;
    }
    const min = Math.min(...pnls);
    const max = Math.max(...pnls);
    const binCount = Math.min(10, Math.max(3, Math.ceil(Math.sqrt(pnls.length))));
    const span = max - min || 1;
    const binWidth = span / binCount;
    const bins = new Array(binCount).fill(0);
    for (const p of pnls) {
      let idx = Math.floor((p - min) / binWidth);
      if (idx >= binCount) idx = binCount - 1;
      if (idx < 0) idx = 0;
      bins[idx] += 1;
    }
    const labels = bins.map((_, i) => {
      const lo = min + i * binWidth;
      const hi = min + (i + 1) * binWidth;
      return `${fmtNum(lo)} to ${fmtNum(hi)}`;
    });
    wrap.innerHTML = `<div class="chart-container"><canvas id="tr15-distribution-chart"></canvas></div>`;
    distributionChart = new Chart(wrap.querySelector("#tr15-distribution-chart").getContext("2d"), {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: "Resolved trades (win/loss) by real P&L bucket",
            data: bins,
            backgroundColor: labels.map((_, i) => (min + i * binWidth < 0 ? "rgba(239, 91, 91, 0.55)" : "rgba(61, 220, 132, 0.55)")),
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
  }

  // --- Monthly returns heatmap: real month-bucketed cumulative P&L from
  // this run's own resolved trades' real exit_time. Built generically --
  // works correctly whenever the input data genuinely spans multiple
  // calendar months, and just as honestly renders a single sparse cell
  // for a short single-period run rather than fabricating a full grid.
  function computeMonthlyReturns(trades) {
    const byMonth = new Map(); // "YYYY-MM" -> {pnl, count}
    for (const t of trades) {
      if (t.pnl === null || t.pnl === undefined || !t.exit_time) continue;
      const key = String(t.exit_time).slice(0, 7);
      if (!byMonth.has(key)) byMonth.set(key, { pnl: 0, count: 0 });
      const e = byMonth.get(key);
      e.pnl += t.pnl;
      e.count += 1;
    }
    return byMonth;
  }

  function renderMonthlyHeatmap(byMonth) {
    if (!byMonth.size) {
      return `<div class="empty">No resolved trade in this run has both an exit time and a P&amp;L -- no real monthly buckets to show.</div>`;
    }
    const keys = Array.from(byMonth.keys()).sort();
    const years = Array.from(new Set(keys.map((k) => k.slice(0, 4)))).sort();
    const maxAbs = Math.max(...Array.from(byMonth.values()).map((e) => Math.abs(e.pnl)), 1);
    const monthLabels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    let rows = "";
    for (const year of years) {
      let cells = "";
      for (let m = 1; m <= 12; m++) {
        const key = `${year}-${String(m).padStart(2, "0")}`;
        const entry = byMonth.get(key);
        if (!entry) {
          cells += `<td class="tr15-heat-cell" style="background:transparent;">—</td>`;
          continue;
        }
        const intensity = Math.min(1, Math.abs(entry.pnl) / maxAbs);
        const color = entry.pnl >= 0
          ? `rgba(61, 220, 132, ${0.15 + 0.65 * intensity})`
          : `rgba(239, 91, 91, ${0.15 + 0.65 * intensity})`;
        cells += `<td class="tr15-heat-cell" style="background:${color};" title="${escapeHtml(key)}: ${escapeHtml(fmtNum(entry.pnl))} over ${entry.count} trade(s)">${fmtNum(entry.pnl)}</td>`;
      }
      rows += `<tr><th>${escapeHtml(year)}</th>${cells}</tr>`;
    }
    return `<table class="tr15-heatmap"><thead><tr><th></th>${monthLabels.map((m) => `<th>${m}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table>
      <p class="section-note">Real month-bucketed sum of resolved trades' own P&amp;L, by real exit_time -- a run whose replayed signals only span one or two real calendar months will honestly show a sparse grid rather than a fabricated full year.</p>`;
  }

  function groupBreakdown(trades, keyFn) {
    const byKey = new Map();
    for (const t of trades) {
      const key = keyFn(t) ?? "—";
      if (!byKey.has(key)) byKey.set(key, { total: 0, wins: 0, losses: 0, resolved: 0, pnl: 0 });
      const e = byKey.get(key);
      e.total += 1;
      if (t.outcome === "win" || t.outcome === "loss") {
        e.resolved += 1;
        if (t.outcome === "win") e.wins += 1;
        else e.losses += 1;
        if (t.pnl !== null && t.pnl !== undefined) e.pnl += t.pnl;
      }
    }
    return byKey;
  }

  function renderBreakdownTable(byKey, keyLabel) {
    const rows = Array.from(byKey.entries())
      .sort((a, b) => b[1].pnl - a[1].pnl)
      .map(([key, e]) => [
        `<span class="mono">${escapeHtml(key)}</span>`,
        fmtNum(e.total),
        fmtNum(e.resolved),
        e.resolved ? `${((e.wins / e.resolved) * 100).toFixed(1)}%` : "—",
        fmtNum(e.pnl),
      ]);
    return table([keyLabel, "Signals", "Resolved trades", "Win rate", "Total P&L"], rows, "No data.");
  }

  function renderRejectedUnfilledTable(trades) {
    const rejectedOutcomes = new Set(["no_price_data", "no_exit_levels", "not_replayed", "exit_unscorable", "still_open"]);
    const rows = trades
      .filter((t) => rejectedOutcomes.has(t.outcome))
      .map((t) => [
        `<span class="mono">${escapeHtml(String(t.signal_id))}</span>`,
        escapeHtml(t.symbol),
        pill(escapeHtml(t.outcome), "warn"),
        escapeHtml(t.note || "—"),
        escapeHtml(t.entry_time),
      ]);
    return table(["Signal ID", "Symbol", "Reason code", "Note", "Entry time"], rows, "No rejected/unfilled signals in this run -- every signal in scope either resolved to a real WIN/LOSS or is still counted as AMBIGUOUS below.");
  }

  function renderAmbiguousTable(trades) {
    const rows = trades
      .filter((t) => t.outcome === "ambiguous")
      .map((t) => [
        `<span class="mono">${escapeHtml(String(t.signal_id))}</span>`,
        escapeHtml(t.symbol),
        escapeHtml(t.entry_time),
        t.exit_time ? escapeHtml(t.exit_time) : "—",
      ]);
    return table(["Signal ID", "Symbol", "Entry time", "Bar where stop AND target both fell in range"], rows, "No ambiguous bars in this run (see app/backtest/simulator.py's module docstring for what AMBIGUOUS means and why it's never resolved by assumption).");
  }

  function renderCostsSection(run) {
    if (!run.stressed_summary) {
      return notTrackedNote(
        "This run did not request the E07 (bounded) linear cost stress (slippage_bps/fee_per_trade were both 0 or omitted) -- every fill in this run's summary above assumes a zero-cost, exact-stop/target-price fill (see app/backtest/replay.py's module docstring: \"No slippage/fee modeling\"). Re-run with a non-zero slippage_bps and/or fee_per_trade in Run configuration to see a real cost-stressed comparison here."
      );
    }
    const raw = run.summary;
    const stressed = run.stressed_summary;
    return `
      ${table(
        ["Metric", "Raw (zero-cost fills)", "Cost-stressed"],
        [
          ["Total P&L", fmtNum(raw.total_pnl), fmtNum(stressed.total_pnl)],
          ["Wins", fmtNum(raw.wins), fmtNum(stressed.wins)],
          ["Losses", fmtNum(raw.losses), fmtNum(stressed.losses)],
          ["Win rate", raw.win_rate !== null ? `${(raw.win_rate * 100).toFixed(1)}%` : "—", stressed.win_rate !== null ? `${(stressed.win_rate * 100).toFixed(1)}%` : "—"],
        ],
        "No data."
      )}
      <p class="section-note">${escapeHtml(run.cost_stress_note || "")}</p>
    `;
  }

  async function loadPersistedRuns(ctx, els) {
    const res = await ctx.fetchJSON("/backtest/runs?limit=50");
    if (res.status === 401 || res.status === 403) {
      StateMatrix.render(els.runs, { state: "denied", deniedCode: res.status });
      return [];
    }
    if (!res.ok) {
      StateMatrix.render(els.runs, { state: "error", message: "Could not load persisted backtest runs." });
      return [];
    }
    const runs = (res.data && res.data.runs) || [];
    renderPersistedRunsTable(els, runs);
    return runs;
  }

  function renderPersistedRunsTable(els, runs) {
    if (!runs.length) {
      StateMatrix.render(els.runs, {
        state: "empty",
        emptyMessage: "No backtest run has been persisted yet -- run one below (Run configuration) to see it appear here, and survive a reload of this page.",
      });
      return;
    }
    const rows = runs.map((r) => {
      const req = r.request || {};
      return [
        `<label><input type="checkbox" class="tr15-compare-checkbox" value="${r.id}" ${comparisonSelection.has(r.id) ? "checked" : ""}> <span class="mono">#${r.id}</span></label>`,
        escapeHtml(r.created_at),
        escapeHtml(req.source || "—") + (req.symbol ? ` / ${escapeHtml(req.symbol)}` : " (every symbol)"),
        `<span class="mono" title="${escapeHtml(r.config_hash)}">${escapeHtml(String(r.config_hash).slice(0, 12))}…</span>`,
        r.summary && r.summary.win_rate !== null && r.summary.win_rate !== undefined ? `${(r.summary.win_rate * 100).toFixed(1)}%` : "—",
        r.summary ? fmtNum(r.summary.total_pnl) : "—",
        `<button type="button" class="tr15-view-report" data-run-id="${r.id}">View report</button>`,
      ];
    });
    StateMatrix.render(els.runs, {
      state: "ready",
      html: `<p class="section-note">Every real, durably persisted run (app/db.py's <span class="mono">backtest_runs</span> table) -- most recent first. This list, and the reports below, survive a reload of this page: it is read fresh from <span class="mono">GET /backtest/runs</span> on every load, not kept only in this browser tab's memory.</p>
        <div class="tr-controls-row"><button type="button" id="tr15-compare-selected">Compare selected runs (2+)</button></div>
        ${table(["Compare", "Persisted at", "Source / symbol", "Config hash", "Win rate", "Total P&L", ""], rows, "No runs.")}`,
    });
    els.runs.querySelectorAll(".tr15-view-report").forEach((btn) => {
      btn.addEventListener("click", () => selectRun(parseInt(btn.dataset.runId, 10)));
    });
    els.runs.querySelectorAll(".tr15-compare-checkbox").forEach((cb) => {
      cb.addEventListener("change", () => {
        const id = parseInt(cb.value, 10);
        if (cb.checked) comparisonSelection.add(id);
        else comparisonSelection.delete(id);
      });
    });
    const compareBtn = els.runs.querySelector("#tr15-compare-selected");
    if (compareBtn) compareBtn.addEventListener("click", () => renderComparison(els));
  }

  async function fetchRunDetail(ctx, runId) {
    if (runDetailCache.has(runId)) return runDetailCache.get(runId);
    const res = await ctx.fetchJSON(`/backtest/runs/${runId}`);
    if (!res.ok) return null;
    runDetailCache.set(runId, res.data);
    return res.data;
  }

  let currentCtx = null;
  let currentEls = null;

  async function selectRun(runId) {
    selectedRunId = runId;
    const run = await fetchRunDetail(currentCtx, runId);
    if (!run) {
      StateMatrix.render(currentEls.reports, { state: "error", message: `Could not load backtest run #${runId}.` });
      return;
    }
    renderCoverage(currentEls, run);
    renderReports(currentEls, run);
    StateMatrix.render(currentEls.explorer, { state: "empty", emptyMessage: "Click \"Inspect\" on a trade row above to see its original signal, simulated fill, and (if the source CSV is still readable) its real historical price path." });
  }

  function renderCoverage(els, run) {
    const s = run.summary;
    const rows = [
      ["Total signals in scope", fmtNum(s.total_signals), "OK", "GET /backtest/runs/{id}"],
      ["Resolved trades (had usable price data + exit levels)", fmtNum(s.resolved_trades), s.resolved_trades > 0 ? "OK" : "NO_RESOLVED_TRADES", "GET /backtest/runs/{id}"],
      ["No price data for the CSV(s) provided", fmtNum(s.no_price_data), s.no_price_data > 0 ? "COVERAGE_GAP" : "OK", "GET /backtest/runs/{id}"],
      ["No stop_loss/take_profit on the signal", fmtNum(s.no_exit_levels), s.no_exit_levels > 0 ? "MISSING_EXIT_LEVELS" : "OK", "GET /backtest/runs/{id} (older signals predate these columns)"],
      ["Still open at period end", fmtNum(s.still_open), "OK", "GET /backtest/runs/{id}"],
      ["Ambiguous (stop and target both in one bar's range)", fmtNum(s.ambiguous), s.ambiguous > 0 ? "PATH_AMBIGUOUS" : "OK", "GET /backtest/runs/{id}"],
    ];
    StateMatrix.render(els.coverage, {
      state: "ready",
      html: `<p class="section-note">Real coverage/gap accounting for run #${run.id}, persisted ${escapeHtml(run.created_at)} -- candle path ambiguity and missing data are retained, never silently dropped or resolved favorably.</p>${table(
        ["Condition", "Count", "Reason code", "Evidence"],
        rows,
        "No conditions."
      )}`,
    });
  }

  function renderReports(els, run) {
    const s = run.summary;
    const kpiEl = document.createElement("div");
    Components.renderKPIBand(kpiEl, {
      items: [
        { label: "Win rate", value: s.win_rate !== null ? `${(s.win_rate * 100).toFixed(1)}%` : "—", tone: "neutral" },
        { label: "Total P&L", value: fmtNum(s.total_pnl), tone: s.total_pnl >= 0 ? "ok" : "crit" },
        { label: "Expectancy", value: s.expectancy !== null ? fmtNum(s.expectancy) : "—", tone: "neutral" },
        { label: "Profit factor", value: s.profit_factor !== null ? fmtNum(s.profit_factor) : "—", sublabel: s.profit_factor_note || "", tone: "neutral" },
        { label: "Max drawdown", value: run.max_drawdown !== null && run.max_drawdown !== undefined ? fmtNum(run.max_drawdown) : "—", sublabel: run.max_drawdown_duration_seconds ? `${Math.round(run.max_drawdown_duration_seconds / 3600)}h underwater` : "computed by app/statistics.py's compute_max_drawdown", tone: "warn" },
      ],
    });

    const trades = run.trades || [];
    const providerBreakdown = groupBreakdown(trades, (t) => t.source);
    const symbolBreakdown = groupBreakdown(trades, (t) => t.symbol);
    const monthly = computeMonthlyReturns(trades);

    const tradeRows = trades.slice(0, 100).map((t) => [
      `<span class="mono">${escapeHtml(String(t.signal_id))}</span>`,
      escapeHtml(t.symbol),
      escapeHtml(t.side),
      t.outcome === "win" ? pill("win", "ok") : t.outcome === "loss" ? pill("loss", "bad") : pill(escapeHtml(t.outcome), "warn"),
      t.pnl !== null && t.pnl !== undefined ? fmtNum(t.pnl) : "—",
      escapeHtml(t.entry_time),
      t.exit_time ? escapeHtml(t.exit_time) : "—",
      `<button type="button" class="tr15-inspect-trade" data-signal-id="${escapeAttr(String(t.signal_id))}">Inspect</button>`,
    ]);

    StateMatrix.render(els.reports, {
      state: "ready",
      html: `
        <p class="section-note">Research report for run #${run.id}, persisted ${escapeHtml(run.created_at)}, config hash <span class="mono">${escapeHtml(run.config_hash)}</span> -- built entirely from this exact persisted run's own real summary/trades (GET /backtest/runs/${run.id}), never re-simulated in the browser.</p>
        <div id="tr15-kpi-band"></div>

        <h3 class="section-note" style="margin-top:16px;">Equity curve (real, cumulative sum of resolved trades' own exit_time/pnl)</h3>
        <div id="tr15-equity-chart-wrap"></div>

        <h3 class="section-note" style="margin-top:16px;">Drawdown</h3>
        <p class="section-note">Max drawdown ${run.max_drawdown !== null && run.max_drawdown !== undefined ? fmtNum(run.max_drawdown) : "—"}${run.max_drawdown_duration_seconds ? `, over ${Math.round(run.max_drawdown_duration_seconds / 3600)} hour(s) from peak to trough` : ""} -- computed by walking this run's own real equity curve once with app/statistics.py's <span class="mono">compute_max_drawdown</span> (the same peak-to-trough walk this codebase already uses for live account equity, reused rather than re-implemented here). Needs at least 2 real equity-curve points; <span class="mono">null</span> above means this run doesn't have that many.</p>

        <h3 class="section-note" style="margin-top:16px;">Monthly returns heatmap</h3>
        ${renderMonthlyHeatmap(monthly)}

        <h3 class="section-note" style="margin-top:16px;">Trade distribution (real win/loss P&amp;L histogram)</h3>
        <div id="tr15-distribution-chart-wrap"></div>

        <h3 class="section-note" style="margin-top:16px;">Provider breakdown (grouped by each trade's real Signal.source)</h3>
        ${renderBreakdownTable(providerBreakdown, "Source")}

        <h3 class="section-note" style="margin-top:16px;">Symbol breakdown</h3>
        ${renderBreakdownTable(symbolBreakdown, "Symbol")}

        <h3 class="section-note" style="margin-top:16px;">Costs</h3>
        ${renderCostsSection(run)}

        <h3 class="section-note" style="margin-top:16px;">Capital utilization</h3>
        ${unsupportedNote("app/backtest/replay.py's own module docstring: this engine has \"no shared-account capital modeling\" -- every signal is replayed independently at its own quantity, with no shared capital ceiling across concurrent positions, so there is no real percentage-of-capital-deployed figure to report here (fabricating one would imply a portfolio-level simulation this engine doesn't do).")}

        <h3 class="section-note" style="margin-top:16px;">MAE / MFE (maximum adverse/favorable excursion)</h3>
        ${notTrackedNote("app/backtest/replay.py's ReplayedTrade records only entry_price/exit_price, not the intra-trade high/low price path a real MAE/MFE figure needs -- this engine walks bar-by-bar to find the FIRST resolving bar but never records the running excursion, so there is no real number to show rather than an estimated one.")}

        <h3 class="section-note" style="margin-top:16px;">Rejected / unfilled signals</h3>
        ${renderRejectedUnfilledTable(trades)}

        <h3 class="section-note" style="margin-top:16px;">Ambiguous executions</h3>
        ${renderAmbiguousTable(trades)}

        <h3 class="section-note" style="margin-top:16px;">Comparison to baseline</h3>
        ${unsupportedNote("No real baseline exists to compare against: this engine has no buy-and-hold computation (it only replays a signal's own resolved stop/target, never \"what if we just held the instrument\"), and app/statistics.py's own documented scope confirms this codebase has no starting-balance/starting-capital figure for any account either -- there is no real, honestly-labeled baseline series to plot here, so none is fabricated.")}

        <h3 class="section-note" style="margin-top:16px;">Resume job (TR-15-A02)</h3>
        ${unsupportedNote("No backing -- there is no job/shard concept to resume (POST /backtest either completes synchronously or the request failed outright).")}

        <h3 class="section-note" style="margin-top:16px;">Trades (click Inspect to open the trade explorer below)</h3>
        ${table(["Signal ID", "Symbol", "Side", "Outcome", "P&L", "Entry time", "Exit time", ""], tradeRows, "No trades.")}
      `,
    });
    els.reports.querySelector("#tr15-kpi-band").replaceWith(kpiEl.firstElementChild || kpiEl);
    renderEquityChart(els.reports.querySelector("#tr15-equity-chart-wrap"), run);
    renderDistributionChart(els.reports.querySelector("#tr15-distribution-chart-wrap"), trades);
    els.reports.querySelectorAll(".tr15-inspect-trade").forEach((btn) => {
      btn.addEventListener("click", () => openTradeExplorer(run, btn.dataset.signalId));
    });
  }

  async function openTradeExplorer(run, signalId) {
    const trade = (run.trades || []).find((t) => String(t.signal_id) === String(signalId));
    if (!trade) return;
    StateMatrix.render(currentEls.explorer, { state: "loading" });

    const pathRes = await currentCtx.fetchJSON(`/backtest/runs/${run.id}/trades/${encodeURIComponent(signalId)}/market-path`);
    const pathData = pathRes.ok ? pathRes.data : { bars: [], available: false, note: "Could not load the market path for this trade." };

    const signalRows = [
      ["Signal ID", `<span class="mono">${escapeHtml(String(trade.signal_id))}</span>`],
      ["Source (provider)", escapeHtml(trade.source)],
      ["Analyst", escapeHtml(trade.analyst || "—")],
      ["Symbol", escapeHtml(trade.symbol)],
      ["Side", escapeHtml(trade.side)],
      ["Entry time", escapeHtml(trade.entry_time)],
      ["Entry price", trade.entry_price !== null ? fmtNum(trade.entry_price) : "—"],
      ["Quantity", trade.quantity !== null ? fmtNum(trade.quantity) : "—"],
      ["Stop price", trade.stop_price !== null ? fmtNum(trade.stop_price) : "— (none set)"],
      ["Target price", trade.target_price !== null ? fmtNum(trade.target_price) : "— (none set)"],
    ];
    const fillRows = [
      ["Outcome", trade.outcome === "win" ? pill("win", "ok") : trade.outcome === "loss" ? pill("loss", "bad") : pill(escapeHtml(trade.outcome), "warn")],
      ["Exit time", trade.exit_time ? escapeHtml(trade.exit_time) : "— (not resolved)"],
      ["Exit (fill) price", trade.exit_price !== null && trade.exit_price !== undefined ? fmtNum(trade.exit_price) : "—"],
      ["P&L", trade.pnl !== null && trade.pnl !== undefined ? fmtNum(trade.pnl) : "—"],
      ["Note", escapeHtml(trade.note || "—")],
    ];

    StateMatrix.render(currentEls.explorer, {
      state: "ready",
      html: `
        <p class="section-note">Trade explorer for signal <span class="mono">${escapeHtml(String(trade.signal_id))}</span> in run #${run.id} -- every field below is this run's own persisted, real replay detail (GET /backtest/runs/${run.id}), never re-derived in the browser.</p>
        <div class="tr-controls-row" style="align-items:flex-start;">
          <div style="flex:1;min-width:220px;">
            <h3 class="section-note">Original signal</h3>
            ${table(["Field", "Value"], signalRows, "—")}
          </div>
          <div style="flex:1;min-width:220px;">
            <h3 class="section-note">Simulated fill / stop / target / P&amp;L</h3>
            ${table(["Field", "Value"], fillRows, "—")}
          </div>
        </div>
        <h3 class="section-note" style="margin-top:12px;">Historical market path</h3>
        <div id="tr15-market-path-wrap"></div>
      `,
    });
    renderMarketPathChart(currentEls.explorer.querySelector("#tr15-market-path-wrap"), trade, pathData);
  }

  function renderMarketPathChart(wrap, trade, pathData) {
    if (marketPathChart) {
      marketPathChart.destroy();
      marketPathChart = null;
    }
    if (!pathData.available || !pathData.bars || !pathData.bars.length) {
      wrap.innerHTML = unsupportedNote(pathData.note || "The historical market path for this trade is unavailable -- the original CSV this run replayed against is no longer readable from this server process, and this view won't fabricate a price path.");
      return;
    }
    const bars = pathData.bars;
    const labels = bars.map((b) => b.timestamp);
    const closes = bars.map((b) => b.close);
    const flat = (v) => (v === null || v === undefined ? null : labels.map(() => v));
    wrap.innerHTML = `<div class="chart-container"><canvas id="tr15-market-path-chart"></canvas></div>
      <p class="section-note">Real OHLC close price, re-read from this run's own persisted CSV path -- entry/stop/target/exit are this trade's own real levels, plotted as reference lines.</p>`;
    const datasets = [
      { label: "Close", data: closes, borderColor: "#5b8cff", backgroundColor: "rgba(91, 140, 255, 0.12)", fill: false, tension: 0, pointRadius: 1 },
    ];
    if (trade.entry_price !== null && trade.entry_price !== undefined) {
      datasets.push({ label: "Entry", data: flat(trade.entry_price), borderColor: "#8b93a7", borderDash: [4, 4], pointRadius: 0, fill: false });
    }
    if (trade.stop_price !== null && trade.stop_price !== undefined) {
      datasets.push({ label: "Stop", data: flat(trade.stop_price), borderColor: "#ef5b5b", borderDash: [4, 4], pointRadius: 0, fill: false });
    }
    if (trade.target_price !== null && trade.target_price !== undefined) {
      datasets.push({ label: "Target", data: flat(trade.target_price), borderColor: "#3ddc84", borderDash: [4, 4], pointRadius: 0, fill: false });
    }
    if (trade.exit_price !== null && trade.exit_price !== undefined) {
      datasets.push({ label: "Exit fill", data: flat(trade.exit_price), borderColor: "#f5b942", borderDash: [2, 2], pointRadius: 0, fill: false });
    }
    marketPathChart = new Chart(wrap.querySelector("#tr15-market-path-chart").getContext("2d"), {
      type: "line",
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: true, position: "bottom" } },
        scales: { y: { beginAtZero: false } },
      },
    });
  }

  async function renderComparison(els) {
    const ids = Array.from(comparisonSelection);
    if (ids.length < 2) {
      StateMatrix.render(els.comparison, { state: "empty", emptyMessage: "Select 2 or more persisted runs above (the checkboxes in the leftmost column) to compare their real metrics side by side." });
      return;
    }
    StateMatrix.render(els.comparison, { state: "loading" });
    const runs = [];
    for (const id of ids) {
      const run = await fetchRunDetail(currentCtx, id);
      if (run) runs.push(run);
    }
    if (!runs.length) {
      StateMatrix.render(els.comparison, { state: "error", message: "Could not load the selected runs for comparison." });
      return;
    }
    const headers = ["Metric", ...runs.map((r) => `Run #${r.id} (${escapeHtml(String(r.config_hash).slice(0, 8))}…)`)];
    const metricRow = (label, fn) => [label, ...runs.map((r) => fn(r))];
    const rows = [
      metricRow("Persisted at", (r) => escapeHtml(r.created_at)),
      metricRow("Source / symbol", (r) => escapeHtml(r.request.source) + (r.request.symbol ? ` / ${escapeHtml(r.request.symbol)}` : " (every symbol)")),
      metricRow("Period", (r) => `${escapeHtml(r.request.start)} → ${escapeHtml(r.request.end)}`),
      metricRow("Max hold (days)", (r) => fmtNum(r.request.max_hold_days)),
      metricRow("Slippage (bps) / Fee per trade", (r) => `${fmtNum(r.request.slippage_bps)} / ${fmtNum(r.request.fee_per_trade)}`),
      metricRow("Total signals", (r) => fmtNum(r.summary.total_signals)),
      metricRow("Resolved trades", (r) => fmtNum(r.summary.resolved_trades)),
      metricRow("Win rate", (r) => (r.summary.win_rate !== null ? `${(r.summary.win_rate * 100).toFixed(1)}%` : "—")),
      metricRow("Total P&L", (r) => fmtNum(r.summary.total_pnl)),
      metricRow("Expectancy", (r) => (r.summary.expectancy !== null ? fmtNum(r.summary.expectancy) : "—")),
      metricRow("Profit factor", (r) => (r.summary.profit_factor !== null ? fmtNum(r.summary.profit_factor) : "—")),
      metricRow("Max drawdown", (r) => (r.max_drawdown !== null && r.max_drawdown !== undefined ? fmtNum(r.max_drawdown) : "—")),
      metricRow("Ambiguous bars", (r) => fmtNum(r.summary.ambiguous)),
      metricRow("Cost-stressed total P&L", (r) => (r.stressed_summary ? fmtNum(r.stressed_summary.total_pnl) : "— (no cost stress requested)")),
    ];
    StateMatrix.render(els.comparison, {
      state: "ready",
      html: `<p class="section-note">Side-by-side comparison of ${runs.length} real, persisted runs the operator selected above -- "Current policy vs candidate A vs candidate B" realized as an actual comparison of real completed replays, never a UI that pretends to run something new here.</p>${table(headers, rows, "No runs selected.")}`,
    });
  }

  async function load(ctx) {
    const els = {
      history: ctx.container.querySelector("#tr15-p01 .tr-panel-body"),
      coverage: ctx.container.querySelector("#tr15-p02 .tr-panel-body"),
      config: ctx.container.querySelector("#tr15-p03 .tr-panel-body"),
      runs: ctx.container.querySelector("#tr15-p04 .tr-panel-body"),
      reports: ctx.container.querySelector("#tr15-p05 .tr-panel-body"),
      comparison: ctx.container.querySelector("#tr15-p06 .tr-panel-body"),
      explorer: ctx.container.querySelector("#tr15-p07 .tr-panel-body"),
    };
    currentCtx = ctx;
    currentEls = els;
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const signalsRes = await ctx.fetchJSON("/signals?limit=200");
    if (signalsRes.status === 401 || signalsRes.status === 403) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "denied", deniedCode: signalsRes.status });
      return;
    }
    if (!signalsRes.ok) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "error", message: "Could not load signal history." });
      return;
    }
    const signals = (signalsRes.data && signalsRes.data.signals) || [];

    // --- History catalog: real, already-received immutable signals ---
    if (!signals.length) {
      StateMatrix.render(els.history, {
        state: "empty",
        emptyMessage: "No historical replay has completed.",
        nextRoute: "/trade/sources/new",
        nextLabel: "Source onboarding and parser laboratory (TR-10)",
      });
    } else {
      const bySource = new Map();
      for (const s of signals) {
        const key = s.source;
        if (!bySource.has(key)) bySource.set(key, { count: 0, symbols: new Set(), earliest: s.received_at, latest: s.received_at });
        const entry = bySource.get(key);
        entry.count += 1;
        entry.symbols.add(s.symbol);
        if (s.received_at < entry.earliest) entry.earliest = s.received_at;
        if (s.received_at > entry.latest) entry.latest = s.received_at;
      }
      const rows = Array.from(bySource.entries()).map(([source, e]) => [
        `<span class="mono">${escapeHtml(source)}</span>`,
        `${e.count} signal(s)`,
        `${escapeHtml(e.earliest)} → ${escapeHtml(e.latest)}`,
        escapeHtml(Array.from(e.symbols).join(", ")),
      ]);
      StateMatrix.render(els.history, {
        state: "ready",
        html: `<p class="section-note">This build's real, durable source history is the already-received signal record itself (GET /signals) -- no separate dataset/version registry exists. A replay below uses this exact record, filtered by source/symbol/period.</p>${table(
          ["Source", "Signals received", "Received range", "Symbols"],
          rows,
          "No source history."
        )}`,
      });
    }

    // --- Run configuration (the real form) ---
    StateMatrix.render(els.config, {
      state: "ready",
      html: `
        <label>Source (Signal.source)<input type="text" id="tr15-source" maxlength="80" placeholder="tradingview" required></label>
        <label>Symbol filter (optional -- blank replays every symbol from this source)<input type="text" id="tr15-symbol" maxlength="40"></label>
        <label>Period start (UTC)<input type="datetime-local" id="tr15-start" required></label>
        <label>Period end (UTC)<input type="datetime-local" id="tr15-end" required></label>
        <label>Max hold (days)<input type="number" id="tr15-max-hold" value="30" min="0.01" step="any"></label>
        <div id="tr15-csv-rows"></div>
        <div class="tr-controls-row"><button type="button" id="tr15-add-csv-row">Add symbol → CSV path</button></div>
        <p class="section-note">Local OHLC CSV path per symbol (columns: timestamp,open,high,low,close[,volume]) -- no market-data vendor is wired in (app/backtest/models.py). The path must be readable by this server process.</p>
        <h3 class="section-note" style="margin-top:12px;">E07 (bounded): optional linear cost stress</h3>
        <label>Slippage (bps)<input type="number" id="tr15-slippage-bps" value="0" min="0" step="any"></label>
        <label>Fee per trade<input type="number" id="tr15-fee-per-trade" value="0" min="0" step="any"></label>
        <div class="form-error" id="tr15-form-error"></div>
        <div class="tr-controls-row">
          <button type="button" id="tr15-preview">Preview</button>
          <button type="button" id="tr15-confirm">Confirm: run replay</button>
        </div>
        <p class="section-note">Preview validates the form and shows exactly what will be replayed -- no request is sent. Confirm actually runs POST /backtest: a real, synchronous, paper-only replay (no live broker adapter is ever called -- see app/backtest/replay.py's module docstring) whose result is now durably persisted server-side (app/db.py's <span class="mono">backtest_runs</span> table) before the response returns -- see Persisted runs below.</p>
        <div id="tr15-preview-result"></div>
      `,
    });
    let csvRowCount = 0;
    function addCsvRow(symbol, path) {
      csvRowCount += 1;
      const id = csvRowCount;
      const row = document.createElement("div");
      row.className = "tr-controls-row";
      row.dataset.csvRow = String(id);
      row.innerHTML = `
        <input type="text" class="tr15-csv-symbol" placeholder="AAPL" maxlength="40" value="${escapeAttr(symbol || "")}">
        <input type="text" class="tr15-csv-path" placeholder="/path/to/AAPL.csv" style="min-width:260px;" value="${escapeAttr(path || "")}">
        <button type="button" class="ghost" data-remove-csv-row="${id}">Remove</button>
      `;
      els.config.querySelector("#tr15-csv-rows").appendChild(row);
      row.querySelector(`[data-remove-csv-row="${id}"]`).addEventListener("click", () => row.remove());
    }
    addCsvRow();
    els.config.querySelector("#tr15-add-csv-row").addEventListener("click", () => addCsvRow());

    function readForm() {
      const source = els.config.querySelector("#tr15-source").value.trim();
      const symbol = els.config.querySelector("#tr15-symbol").value.trim();
      const startRaw = els.config.querySelector("#tr15-start").value;
      const endRaw = els.config.querySelector("#tr15-end").value;
      const maxHold = parseFloat(els.config.querySelector("#tr15-max-hold").value) || 30;
      const slippageBps = parseFloat(els.config.querySelector("#tr15-slippage-bps").value) || 0;
      const feePerTrade = parseFloat(els.config.querySelector("#tr15-fee-per-trade").value) || 0;
      const csvPaths = {};
      els.config.querySelectorAll("[data-csv-row]").forEach((row) => {
        const sym = row.querySelector(".tr15-csv-symbol").value.trim();
        const path = row.querySelector(".tr15-csv-path").value.trim();
        if (sym && path) csvPaths[sym] = path;
      });
      const errors = [];
      if (!source) errors.push("Source is required.");
      if (!startRaw) errors.push("Period start is required.");
      if (!endRaw) errors.push("Period end is required.");
      if (startRaw && endRaw && new Date(startRaw) >= new Date(endRaw)) errors.push("Period end must be after period start.");
      if (!Object.keys(csvPaths).length) errors.push("At least one symbol → CSV path is required.");
      return {
        errors,
        request: {
          source,
          symbol: symbol || null,
          start: startRaw ? new Date(startRaw).toISOString() : null,
          end: endRaw ? new Date(endRaw).toISOString() : null,
          csv_paths: csvPaths,
          max_hold_days: maxHold,
          slippage_bps: slippageBps,
          fee_per_trade: feePerTrade,
        },
      };
    }

    els.config.querySelector("#tr15-preview").addEventListener("click", () => {
      const errorEl = els.config.querySelector("#tr15-form-error");
      const resultEl = els.config.querySelector("#tr15-preview-result");
      errorEl.textContent = "";
      const { errors, request } = readForm();
      if (errors.length) {
        errorEl.textContent = errors.join(" ");
        resultEl.innerHTML = "";
        return;
      }
      resultEl.innerHTML = `<p class="section-note">Would replay source "${escapeHtml(request.source)}"${request.symbol ? ` / ${escapeHtml(request.symbol)}` : " (every symbol)"} from ${escapeHtml(request.start)} to ${escapeHtml(request.end)}, max hold ${request.max_hold_days}d, against ${Object.keys(request.csv_paths).length} CSV source(s): ${escapeHtml(Object.keys(request.csv_paths).join(", "))}. No request has been sent -- Confirm to actually run it.</p>`;
    });

    els.config.querySelector("#tr15-confirm").addEventListener("click", async () => {
      const errorEl = els.config.querySelector("#tr15-form-error");
      const resultEl = els.config.querySelector("#tr15-preview-result");
      errorEl.textContent = "";
      const { errors, request } = readForm();
      if (errors.length) {
        errorEl.textContent = errors.join(" ");
        return;
      }
      resultEl.textContent = "Running replay…";
      try {
        const body = await postJSON("/backtest", request);
        runDetailCache.set(body.run_id, { ...body, id: body.run_id, request: { ...request } });
        // Render Persisted runs / Coverage check / Research report BEFORE
        // announcing completion in the form -- an operator (or a test)
        // reacting to "Replay complete" below must already find the real
        // report rendered, not a still-loading placeholder.
        await loadPersistedRuns(ctx, els);
        await selectRun(body.run_id);
        resultEl.innerHTML = `<p class="section-note">Replay complete and persisted as run #${body.run_id} (config hash <span class="mono">${escapeHtml(body.config_hash)}</span>). See Persisted runs, Coverage check, and Research report below.</p>`;
      } catch (err) {
        errorEl.textContent = err.message;
        resultEl.innerHTML = "";
      }
    });

    // --- Persisted runs (real, durable -- survives a reload) ---
    const persisted = await loadPersistedRuns(ctx, els);

    // --- Coverage / Research report / Trade explorer / Comparison: start
    // from the most recently persisted run (if any), same as before, but
    // now real server-side history rather than this tab's own memory.
    if (persisted.length) {
      await selectRun(persisted[0].id);
    } else {
      StateMatrix.render(els.coverage, { state: "empty", emptyMessage: "Run a replay below to see its real coverage/gap accounting here." });
      StateMatrix.render(els.reports, { state: "empty", emptyMessage: "No historical replay has completed.", nextRoute: "/trade/sources/new", nextLabel: "Source onboarding and parser laboratory (TR-10)" });
      StateMatrix.render(els.explorer, { state: "empty", emptyMessage: "Run a replay and open its research report to inspect a trade here." });
    }
    StateMatrix.render(els.comparison, { state: "empty", emptyMessage: "Select 2 or more persisted runs above (Persisted runs' checkboxes) to compare their real metrics side by side." });

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr15 = {
    title: "Historical signal backtests",
    breadcrumb: "Trade / Backtests",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = shell();
      await load(ctx);
    },
  };
  Router.register("/trade/backtests", "tr15");
})();
