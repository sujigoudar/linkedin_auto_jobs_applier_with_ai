/* TR-15: Historical signal backtests (`#/trade/backtests`).
 *
 * Real backing capability: POST /backtest (app/main.py's run_backtest,
 * app/backtest/replay.py's BacktestEngine) -- replays this service's own
 * already-received, immutable signal history (SignalStore) against
 * caller-supplied local OHLC CSV files (app/backtest/models.py's
 * CsvPriceHistoryProvider). Synchronous, in-process, never touches a live
 * broker adapter (see app/backtest/replay.py's module docstring) -- no
 * live order is ever placed from this screen. Optional linear cost-stress
 * (E07 bounded, app/backtest/cost_stress.py) is a flat slippage_bps/
 * fee_per_trade stress test, never a real broker fee schedule.
 *
 * Honest gaps, disclosed rather than invented:
 *   - No dataset/policy/cost-scenario/resource-profile REGISTRY exists
 *     anywhere (dataset_version_ids, policy_version_ids, cost_scenario_ids,
 *     resource_profile_id) -- this build's real inputs are exactly what
 *     POST /backtest's own BacktestRequest takes: source/symbol filter,
 *     a UTC period, a local CSV path per symbol, max_hold_days, and the
 *     two E07 cost-stress numbers. The form below collects exactly that,
 *     not the fuller spec'd field set this codebase has no backing for.
 *   - No async job QUEUE exists -- POST /backtest runs synchronously and
 *     returns the full result in one response (see its own docstring: "no
 *     results are persisted"). "Queue/progress" below is honestly a
 *     record of this session's own synchronous run(s), not a real queue.
 *   - No persisted backtest-run history: reloading this screen loses any
 *     prior run's results (nothing survives past this response) -- stated
 *     plainly rather than faking a "Reports" catalog from nothing.
 *   - "Resume job" (TR-15-A02) has no backing: there is no job to resume
 *     (a run either completed synchronously or the request failed).
 *   - "Compare results" (TR-15-A03) is offered as a REAL compare of two
 *     runs actually completed in this session (kept in memory), not a
 *     server-side saved-result comparison (no persistence exists).
 */
(function () {
  "use strict";

  function unsupportedNote(reason) {
    const el = document.createElement("div");
    StateMatrix.render(el, { state: "unsupported", reason });
    return el.outerHTML;
  }

  function shell() {
    return `
      <section class="tr-panel" id="tr15-p01"><h2>History catalog</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr15-p02"><h2>Coverage check</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr15-p03"><h2>Run configuration</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr15-p04"><h2>Queue/progress</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr15-p05"><h2>Reports</h2><div class="tr-panel-body"></div></section>
    `;
  }

  // This session's own completed run history -- never persisted server
  // side (see this file's module docstring); lost on reload, same as
  // the server itself never storing it.
  const runHistory = [];

  // Additive Chart.js visualization of the same real data the trades table
  // already shows -- a client-side running sum of each RESOLVED trade's own
  // real exit_time/pnl (POST /backtest's own response, unmodified), sorted
  // chronologically. Never a fabricated series: a run with zero resolved
  // trades renders a real empty state instead of a flat line. Reuses the
  // exact same one-persistent-instance, destroy-and-recreate Chart.js
  // pattern dashboard.html's own "economics-chart" (C12) already
  // established, rather than inventing a second charting convention.
  let equityChart = null;

  function computeEquityCurve(trades) {
    return trades
      .filter((t) => t.exit_time && t.pnl !== null && t.pnl !== undefined)
      .slice()
      .sort((a, b) => new Date(a.exit_time) - new Date(b.exit_time))
      .reduce((acc, t) => {
        const prev = acc.length ? acc[acc.length - 1].y : 0;
        acc.push({ x: t.exit_time, y: prev + t.pnl });
        return acc;
      }, []);
  }

  function renderEquityChart(container, trades) {
    if (equityChart) {
      equityChart.destroy();
      equityChart = null;
    }
    const wrap = container.querySelector("#tr15-equity-chart-wrap");
    if (!wrap) return;
    const curve = computeEquityCurve(trades);
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

  async function load(ctx) {
    const els = {
      history: ctx.container.querySelector("#tr15-p01 .tr-panel-body"),
      coverage: ctx.container.querySelector("#tr15-p02 .tr-panel-body"),
      config: ctx.container.querySelector("#tr15-p03 .tr-panel-body"),
      queue: ctx.container.querySelector("#tr15-p04 .tr-panel-body"),
      reports: ctx.container.querySelector("#tr15-p05 .tr-panel-body"),
    };
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
        <p class="section-note">Preview validates the form and shows exactly what will be replayed -- no request is sent. Confirm actually runs POST /backtest: a real, synchronous, paper-only replay (no live broker adapter is ever called) -- see app/backtest/replay.py's module docstring.</p>
        <div id="tr15-preview-result"></div>
        ${unsupportedNote("Save (persist a reproducible replay draft) has no backing: this build has no backtest-draft persistence table -- the form above is only ever kept in this browser tab's own memory, same as every field on this page.")}
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
      renderQueue({ running: true, request });
      try {
        const report = await postJSON("/backtest", request);
        const run = { at: new Date().toISOString(), request, report };
        runHistory.unshift(run);
        resultEl.innerHTML = `<p class="section-note">Replay complete. See Coverage check and Reports below.</p>`;
        renderQueue({ running: false });
        renderCoverage(run);
        renderReports();
      } catch (err) {
        errorEl.textContent = err.message;
        resultEl.innerHTML = "";
        renderQueue({ running: false, failed: true });
      }
    });

    function renderQueue(state) {
      if (state.running) {
        StateMatrix.render(els.queue, { state: "loading" });
        return;
      }
      if (!runHistory.length) {
        StateMatrix.render(els.queue, {
          state: "empty",
          emptyMessage: state.failed
            ? "The last replay request failed -- see the form error above. No real queue/job exists in this build: POST /backtest runs synchronously in one request."
            : "No historical replay has completed.",
        });
        return;
      }
      const rows = runHistory.slice(0, 25).map((run, i) => [
        `<span class="mono">#${runHistory.length - i}</span>`,
        escapeHtml(run.at),
        escapeHtml(run.request.source) + (run.request.symbol ? ` / ${escapeHtml(run.request.symbol)}` : ""),
        `${escapeHtml(run.request.start)} → ${escapeHtml(run.request.end)}`,
        pill("completed synchronously", "ok"),
      ]);
      StateMatrix.render(els.queue, {
        state: "ready",
        html: `<p class="section-note">No async job queue exists in this build (see this file's module docstring) -- this is this browser tab's own record of runs completed synchronously via POST /backtest, most recent first. Nothing here is persisted server-side; reloading this page clears it.</p>${table(
          ["Run", "Completed at", "Source / symbol", "Period", "State"],
          rows,
          "No runs yet."
        )}`,
      });
    }
    renderQueue({ running: false });

    function renderCoverage(run) {
      const s = run.report.summary;
      const rows = [
        ["Total signals in scope", fmtNum(s.total_signals), "OK", "POST /backtest summary"],
        ["Resolved trades (had usable price data + exit levels)", fmtNum(s.resolved_trades), s.resolved_trades > 0 ? "OK" : "NO_RESOLVED_TRADES", "POST /backtest summary"],
        ["No price data for the CSV(s) provided", fmtNum(s.no_price_data), s.no_price_data > 0 ? "COVERAGE_GAP" : "OK", "POST /backtest summary"],
        ["No stop_loss/take_profit on the signal", fmtNum(s.no_exit_levels), s.no_exit_levels > 0 ? "MISSING_EXIT_LEVELS" : "OK", "POST /backtest summary (older signals predate these columns)"],
        ["Still open at period end", fmtNum(s.still_open), "OK", "POST /backtest summary"],
      ];
      StateMatrix.render(els.coverage, {
        state: "ready",
        html: `<p class="section-note">Real coverage/gap accounting from the just-completed replay -- candle path ambiguity and missing data are retained, never silently dropped or resolved favorably.</p>${table(
          ["Condition", "Count", "Reason code", "Evidence"],
          rows,
          "No conditions."
        )}`,
      });
    }
    if (runHistory.length) renderCoverage(runHistory[0]);
    else StateMatrix.render(els.coverage, { state: "empty", emptyMessage: "Run a replay below to see its real coverage/gap accounting here." });

    function renderReports() {
      if (!runHistory.length) {
        StateMatrix.render(els.reports, { state: "empty", emptyMessage: "No historical replay has completed.", nextRoute: "/trade/sources/new", nextLabel: "Source onboarding and parser laboratory (TR-10)" });
        return;
      }
      const run = runHistory[0];
      const s = run.report.summary;
      const tradeRows = run.report.trades.slice(0, 25).map((t) => [
        `<span class="mono">${escapeHtml(String(t.signal_id))}</span>`,
        escapeHtml(t.symbol),
        escapeHtml(t.side),
        t.outcome === "win" ? pill("win", "ok") : t.outcome === "loss" ? pill("loss", "bad") : pill(escapeHtml(t.outcome), "warn"),
        t.pnl !== null && t.pnl !== undefined ? fmtNum(t.pnl) : "—",
        escapeHtml(t.entry_time),
        t.exit_time ? escapeHtml(t.exit_time) : "—",
      ]);
      const stressedNote = run.report.stressed_summary
        ? `<p class="section-note">Cost-stress applied (E07 bounded, linear only): stressed total P&amp;L ${fmtNum(run.report.stressed_summary.total_pnl)} vs. raw ${fmtNum(s.total_pnl)}. ${escapeHtml(run.report.cost_stress_note || "")}</p>`
        : "";
      StateMatrix.render(els.reports, {
        state: "ready",
        html: `
          <p class="section-note">Report for run completed ${escapeHtml(run.at)} -- not persisted server-side (see this file's module docstring); this is the exact POST /backtest response, unmodified.</p>
          <div class="tr-controls-row">
            <div><strong>Win rate</strong><br>${s.win_rate !== null ? `${(s.win_rate * 100).toFixed(1)}%` : "—"}</div>
            <div><strong>Total P&amp;L</strong><br>${fmtNum(s.total_pnl)}</div>
            <div><strong>Expectancy</strong><br>${s.expectancy !== null ? fmtNum(s.expectancy) : "—"}</div>
            <div><strong>Profit factor</strong><br>${s.profit_factor !== null ? fmtNum(s.profit_factor) : "—"} <span class="section-note">${escapeHtml(s.profit_factor_note || "")}</span></div>
          </div>
          ${stressedNote}
          <h3 class="section-note" style="margin-top:12px;">Cumulative P&amp;L over time (real, client-side running sum of each resolved trade's own exit_time/pnl -- additive to the table below, not a replacement)</h3>
          <div id="tr15-equity-chart-wrap"></div>
          ${table(["Signal ID", "Symbol", "Side", "Outcome", "P&L", "Entry time", "Exit time"], tradeRows, "No trades.")}
          ${unsupportedNote("TR-15-A02 (Resume job): no backing -- there is no job/shard concept to resume (POST /backtest either completes synchronously or the request failed outright).")}
          ${runHistory.length > 1 ? compareControlHtml() : ""}
        `,
      });
      renderEquityChart(els.reports, run.report.trades);
      if (runHistory.length > 1) wireCompareControl();
    }
    function compareControlHtml() {
      const options = runHistory.map((r, i) => `<option value="${i}">#${runHistory.length - i} (${escapeHtml(r.at)})</option>`).join("");
      return `
        <h3 class="section-note" style="margin-top:12px;">TR-15-A03: Compare results (this session's own completed runs)</h3>
        <label>Compare against<select id="tr15-compare-select">${options}</select></label>
        <div id="tr15-compare-result"></div>
      `;
    }
    function wireCompareControl() {
      const sel = els.reports.querySelector("#tr15-compare-select");
      if (!sel) return;
      const renderCompare = () => {
        const other = runHistory[parseInt(sel.value, 10)];
        const current = runHistory[0];
        const resultEl = els.reports.querySelector("#tr15-compare-result");
        resultEl.innerHTML = table(
          ["Metric", "Current (#" + runHistory.length + ")", `Compared (#${runHistory.length - parseInt(sel.value, 10)})`],
          [
            ["Total P&L", fmtNum(current.report.summary.total_pnl), fmtNum(other.report.summary.total_pnl)],
            ["Win rate", current.report.summary.win_rate !== null ? `${(current.report.summary.win_rate * 100).toFixed(1)}%` : "—", other.report.summary.win_rate !== null ? `${(other.report.summary.win_rate * 100).toFixed(1)}%` : "—"],
            ["Resolved trades", fmtNum(current.report.summary.resolved_trades), fmtNum(other.report.summary.resolved_trades)],
          ],
          "No runs to compare."
        );
      };
      sel.addEventListener("change", renderCompare);
      renderCompare();
    }
    renderReports();

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
