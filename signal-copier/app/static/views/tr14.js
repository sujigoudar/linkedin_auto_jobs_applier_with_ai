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
 *
 * Honest gaps, disclosed rather than invented:
 *   - Fees/commissions: `orders` has no fee column (see app/economics.py's
 *     module docstring) -- every "Fees"/"Net" cell renders "not tracked"
 *     rather than a fabricated 0. M-TR-14-04 (Unresolved fees) is
 *     `unsupported` for the same reason: zero would falsely claim a
 *     verified count over a dimension this schema doesn't track at all.
 *   - Fill slippage: no reference/expected execution price is stored
 *     anywhere this build could diff a fill against -- `unsupported`.
 *   - Maximum drawdown (M-TR-14-03): needs a full-resolution chronological
 *     equity curve; this build only replays a realized-P&L total and
 *     per-symbol running numbers, never a persisted equity series --
 *     `unsupported` rather than approximated from a metric that isn't one.
 *   - "Episode" here is one (account, symbol) pair's aggregated economics,
 *     not a per-trade row -- this schema doesn't expose individual
 *     completed-episode rows (entry/exit fill pairs) through any GET
 *     endpoint, only the aggregated counts app/economics.py computes.
 *
 * "Export report" (F-REPORT) has no server-side async export-job queue
 * anywhere in this codebase (no job table, no download-token issuance) --
 * rather than fabricate one, TR-14-A01 generates a REAL client-side CSV
 * of exactly the already-loaded, already-computed read-model snapshot
 * (CSV-injection-safe: any cell starting with =+-@ is prefixed with a
 * leading apostrophe before download, matching the form contract's own
 * "No formula injection in CSV" requirement) -- disclosed as exactly that,
 * not a scoped/period-filtered server job.
 */
(function () {
  "use strict";

  function unsupportedNote(reason) {
    const el = document.createElement("div");
    StateMatrix.render(el, { state: "unsupported", reason });
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

  // Additive Chart.js visualization of the same real per-symbol latency
  // data the table below already shows (E05, app/execution_quality.py's
  // mean_seconds per account/symbol) -- reuses the exact same one-
  // persistent-instance, destroy-and-recreate Chart.js pattern
  // dashboard.html's own "economics-chart" (C12) already established.
  let latencyChart = null;

  function renderLatencyChart(container, rows) {
    if (latencyChart) {
      latencyChart.destroy();
      latencyChart = null;
    }
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

  function shell() {
    return `
      <section class="tr-panel" id="tr14-p01"><h2>Account/analyst book</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr14-metrics"><h2>Metrics</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr14-p03"><h2>Latency/slippage</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr14-p04"><h2>Costs</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr14-p05"><h2>Incomplete records</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr14-actions"><h2>Actions</h2><div class="tr-panel-body"></div></section>
    `;
  }

  async function load(ctx) {
    const els = {
      book: ctx.container.querySelector("#tr14-p01 .tr-panel-body"),
      metrics: ctx.container.querySelector("#tr14-metrics .tr-panel-body"),
      latency: ctx.container.querySelector("#tr14-p03 .tr-panel-body"),
      costs: ctx.container.querySelector("#tr14-p04 .tr-panel-body"),
      incomplete: ctx.container.querySelector("#tr14-p05 .tr-panel-body"),
      actions: ctx.container.querySelector("#tr14-actions .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const accountsRes = await ctx.fetchJSON("/accounts");
    if (accountsRes.status === 401 || accountsRes.status === 403) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "denied", deniedCode: accountsRes.status });
      return;
    }
    if (!accountsRes.ok) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "error", message: "Could not load accounts." });
      return;
    }
    const accounts = (accountsRes.data && accountsRes.data.accounts) || [];
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

    const [economicsResults, qualityResults] = await Promise.all([
      Promise.all(accounts.map((a) => ctx.fetchJSON(`/accounts/${encodeURIComponent(a.account_id)}/economics`))),
      Promise.all(accounts.map((a) => ctx.fetchJSON(`/accounts/${encodeURIComponent(a.account_id)}/execution-quality`))),
    ]);

    const economics = accounts.map((a, i) => ({ account: a, data: economicsResults[i].ok ? economicsResults[i].data : null }));
    const quality = accounts.map((a, i) => ({ account: a, data: qualityResults[i].ok ? qualityResults[i].data : null }));
    const anyEconomicsFailed = economicsResults.some((r) => !r.ok);

    // --- Account/analyst book: per (account, symbol) aggregated economics ---
    const bookRows = [];
    for (const { account, data } of economics) {
      if (!data) continue;
      const symbols = Object.keys(data.per_symbol || {});
      if (!symbols.length) {
        bookRows.push([
          `<a href="#/trade/positions">${escapeHtml(account.account_id)}</a>`,
          pill("no completed activity", "muted"), "—", "—", "—", "—",
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
        ]);
      }
    }
    StateMatrix.render(els.book, {
      state: bookRows.length ? "ready" : "empty",
      emptyMessage: "No fully qualified economic report is available.",
      html: `<p class="section-note">Real, authoritative account economics (E06, app/economics.py) replayed from this account's own confirmed fills -- gross of fees, no fee column exists in this schema. "Episode" = one (account, symbol) pair's independently completed flat→non-flat→flat position cycle count, aggregated -- not a per-trade row (no per-episode row is exposed by any GET endpoint).</p>${table(
        ["Account / symbol", "Completed episodes", "Lifecycle win rate", "Realized P&L (gross of fees)", "Closing fills", "Last fill price"],
        bookRows,
        "No completed economic activity."
      )}`,
    });

    // --- Metrics: M-TR-14-01..04 ---
    let netPnl = 0;
    let completedEpisodes = 0;
    let sawAnyEconomics = false;
    for (const { data } of economics) {
      if (!data) continue;
      sawAnyEconomics = true;
      netPnl += data.realized_pnl;
      for (const s of Object.values(data.per_symbol || {})) completedEpisodes += s.completed_episodes;
    }
    const metricsHtml = `
      <div class="tr-controls-row">
        <div><strong>Net P&amp;L</strong><br>${sawAnyEconomics && !anyEconomicsFailed ? fmtNum(netPnl) : pill("partial -- some accounts unavailable", "warn")} <span class="section-note">(sum of realized P&amp;L; excludes unrealized/live marks and untracked fees -- not net-of-costs verification)</span></div>
        <div><strong>Completed episodes</strong><br>${sawAnyEconomics ? fmtNum(completedEpisodes) : "—"} <span class="section-note">(exact count over this authorized snapshot)</span></div>
      </div>
      ${unsupportedNote("M-TR-14-03 (Maximum drawdown): requires a full-resolution chronological equity curve -- this build persists only aggregated realized-P&L totals per (account, symbol), never a running equity series. No approximation is substituted for a metric that needs data this schema doesn't keep.")}
      ${unsupportedNote("M-TR-14-04 (Unresolved fees): this schema has no fee column at all (see app/economics.py's module docstring) -- reporting 0 would falsely claim a verified zero over a dimension that was never tracked, so this stays unsupported rather than a fabricated count.")}
    `;
    StateMatrix.render(els.metrics, { state: "ready", html: metricsHtml });

    // --- Latency/slippage ---
    const latencySamples = [];
    const latencyRows = [];
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
        ]);
      }
    }
    StateMatrix.render(els.latency, {
      state: latencyRows.length ? "ready" : "empty",
      emptyMessage: "No filled order has a matching signal timestamp to measure latency from yet.",
      html: `<p class="section-note">Real signal-received-to-fill latency (E05, app/execution_quality.py) -- mixes this process's own handling time with real network/broker latency; there is no separately tracked decision/submission/acknowledgement timestamp to split it further.</p>
        <h3 class="section-note" style="margin-top:12px;">Mean latency by account/symbol (additive to the table below, not a replacement)</h3>
        <div id="tr14-latency-chart-wrap"></div>
        ${table(
        ["Account", "Symbol", "Samples", "Mean", "Median", "Max"],
        latencyRows,
        "No latency samples."
      )}${unsupportedNote('"Protection delay" (signal→confirmed protective stop) and "Fill slippage" (fill price vs. a reference/expected price) are not tracked anywhere in this build -- no reference price is stored to diff a fill against, and no protection-confirmation timestamp is kept separately from the stop\'s own current status.')}`,
    });
    renderLatencyChart(els.latency, latencySamples);

    // --- Costs: fees not tracked at all ---
    StateMatrix.render(els.costs, {
      state: "unsupported",
      reason: "This schema has no fee/commission column anywhere (app/economics.py's own module docstring: every reported P&L number is gross of costs). There is nothing real to show here -- a cost-stress ESTIMATE exists only inside the separate backtest replay (see Historical signal backtests, TR-15's slippage_bps/fee_per_trade), never as a claim about real executed costs.",
    });

    // --- Incomplete records ---
    const incompleteRows = [];
    for (const { account, data } of economics) {
      if (data && data.incomplete_symbols && data.incomplete_symbols.length) {
        for (const symbol of data.incomplete_symbols) {
          incompleteRows.push([`<span class="mono">${escapeHtml(account.account_id)}</span>`, escapeHtml(symbol), "excluded from realized P&L totals (missing/invalid fill data)", "economics replay"]);
        }
      }
    }
    for (const { account, data } of quality) {
      if (data && data.unmatched_order_count) {
        incompleteRows.push([`<span class="mono">${escapeHtml(account.account_id)}</span>`, "(all symbols)", `${fmtNum(data.unmatched_order_count)} filled order(s) with no matching signal timestamp -- excluded from latency figures`, "execution-quality replay"]);
      }
    }
    StateMatrix.render(els.incomplete, {
      state: incompleteRows.length ? "ready" : "empty",
      emptyMessage: "No incomplete records in the current authorized snapshot.",
      html: `<p class="section-note">Real, server-scoped exclusions -- never silently folded into a total as zero (see app/economics.py/app/execution_quality.py's own "incomplete stays incomplete" rule).</p>${table(
        ["Account", "Symbol", "Why excluded", "Source"],
        incompleteRows,
        "None."
      )}`,
    });

    // --- Actions ---
    const accountOptions = accounts.map((a) => `<option value="${escapeAttr(a.account_id)}">${escapeHtml(a.account_id)}</option>`).join("");
    StateMatrix.render(els.actions, {
      state: "ready",
      html: `
        <p class="section-note">TR-14-A02 (Inspect episode): use the account/symbol links in "Account/analyst book" above -- each opens the real <a href="#/trade/positions">Position and protection detail (TR-03)</a> for that exact object.</p>
        <h3 class="section-note" style="margin-top:12px;">TR-14-A01: Export report</h3>
        <label>Scoped accounts (multi-select)<select id="tr14-export-accounts" multiple size="${Math.min(6, accounts.length)}">${accountOptions}</select></label>
        <div class="tr-controls-row"><button type="button" id="tr14-export-run">Download CSV</button></div>
        ${unsupportedNote("This build has no server-side async export-job queue (no job table, no expiring download token) -- 'Export report' downloads a real client-side CSV of exactly this page's already-loaded, already-computed snapshot for the selected accounts instead of enqueuing a server job. Period/currency/format scoping beyond CSV is not offered since no server job exists to scope.")}
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
        for (const [symbol, s] of Object.entries(data.per_symbol || {})) {
          rows.push([account.account_id, symbol, s.completed_episodes, s.winning_episodes, s.realized_pnl, "not tracked", s.realized_pnl, s.closing_fills, s.last_fill_price]);
        }
      }
      downloadCsv(
        `tr14-performance-export-${new Date().toISOString().slice(0, 10)}.csv`,
        ["account_id", "symbol", "completed_episodes", "winning_episodes", "gross_realized_pnl", "fees", "net_realized_pnl_gross_of_fees", "closing_fills", "last_fill_price"],
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
      await load(ctx);
      ctx.registerPoll("tr14", 10000, () => load(ctx));
    },
  };
  Router.register("/trade/performance", "tr14");
})();
