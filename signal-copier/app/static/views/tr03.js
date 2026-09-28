/* TR-03: Position and protection detail (`#/trade/positions/:account_id/:symbol`).
 *
 * Real backing data: GET /positions (filtered client-side to this
 * account_id/symbol -- both the plain `positions` row and, if managed,
 * the matching `managed_lifecycles` entry) and GET /orders?account_id=...
 * (filtered client-side to this symbol) for order/fill history.
 *
 * Rule-6 boundary for this batch: TR-03-A01 (preview partial reduction),
 * TR-03-A02 (preview stop change) and TR-03-A03 (request reconciliation)
 * have NO backing endpoint anywhere in this codebase --
 * app/lifecycle/manager.py's request_exit/replace-stop machinery is only
 * ever driven internally (by targets/trailing/time-exits/provider
 * signals), never through an owner-facing HTTP action, and there is no
 * "trigger a reconciliation pass now" route either (app/reconciliation.py
 * only runs on its own schedule). This batch is explicitly told not to
 * add any new financial-command capability -- so the Controls panel below
 * renders those three as honest "unsupported" states, not fake forms.
 * The ONE real, already-wired action surfaced here is the existing
 * `POST /positions/{account_id}/{symbol}/close` full exit (same one the
 * legacy dashboard's "Exit now" button already calls).
 *
 * --- PU-B2: MAE/MFE chart (this batch) ---
 *
 * Real backing data: PU-A1's own `PositionLifecycle.entry_price`/
 * `highest_price_since_entry`(`_at`)/`lowest_price_since_entry`(`_at`)/
 * `mae`/`mfe`/`has_price_data`, already projected onto every
 * `managed_lifecycles` row by GET /positions (app/main.py's
 * `_managed_lifecycle_snapshot`) -- no backend change needed for this
 * batch. Rendered as a floating-bar (range) Chart.js chart: one bar
 * spans entry price -> highest price reached, the other spans lowest
 * price reached -> entry price, so both visually meet at the entry
 * price. The displayed MAE/MFE numbers are `lifecycle.mae`/`lifecycle.mfe`
 * verbatim -- NEVER recomputed client-side from the two extremes (that
 * would risk silently diverging from PositionLifecycle's own side-aware
 * computation the moment this file and app/lifecycle/models.py disagree
 * about which extreme is "adverse" vs "favorable" for a given side).
 *
 * Honest gaps:
 *   - Plain (unmanaged) positions have no `PositionLifecycle` at all, so
 *     there is nothing to chart -- rendered as "unsupported", not a fake
 *     empty chart.
 *   - `has_price_data` false (no entry_price yet -- the pending-entry
 *     gap this build documents -- or a broker with no live-price feed at
 *     all) renders an honest "no price/excursion data available" empty
 *     state, never a fabricated flat line at 0.
 *   - No OHLC candlestick chart: this build has no historical intraday
 *     price-bar source for a LIVE/managed position (app/pricing.py's
 *     PriceMonitor only ever gets a single current last-price tick per
 *     poll, never a bar; app/backtest/models.py's CsvPriceHistoryProvider
 *     is the only OHLC source anywhere in this codebase, and it only
 *     replays caller-supplied local CSV files through the separate
 *     backtest path, never a live position). Faking OHLC bars out of one
 *     last-price series would be exactly the kind of fabrication this
 *     build refuses to do.
 *   - No cost waterfall: app/economics.py's own module docstring says
 *     plainly that `orders` has no fee/commission/slippage/spread column
 *     at all yet ("Gross of fees (not yet tracked)") -- there isn't even
 *     a single blended figure to show, let alone components to break out.
 *   - No MAE-before-eventual-winner scatter: that compares MANY positions
 *     across providers/symbols, which belongs on a cross-position
 *     analytics screen, not this single-position detail view.
 */
(function () {
  "use strict";

  // One persistent Chart.js instance, destroy-and-recreate on reload --
  // the same convention TR-14/TR-15 already established.
  let maeMfeChart = null;

  function renderMaeMfeChart(container, lifecycle) {
    if (maeMfeChart) {
      maeMfeChart.destroy();
      maeMfeChart = null;
    }
    const wrap = container.querySelector("#tr03-maemfe-chart-wrap");
    if (!wrap) return;
    wrap.innerHTML = `<div class="chart-container"><canvas id="tr03-maemfe-chart"></canvas></div>`;
    const entry = lifecycle.entry_price;
    const high = lifecycle.highest_price_since_entry;
    const low = lifecycle.lowest_price_since_entry;
    maeMfeChart = new Chart(wrap.querySelector("#tr03-maemfe-chart").getContext("2d"), {
      type: "bar",
      data: {
        labels: ["Highest reached", "Lowest reached"],
        datasets: [
          {
            label: "Price range since entry (both bars meet at entry price)",
            // Floating-bar segments: [start, end] per category. Both meet
            // at `entry` on the x-axis regardless of side -- which
            // extreme is "adverse" vs "favorable" is a fact about
            // lifecycle.mae/lifecycle.mfe below, not about this chart's
            // axis geometry.
            data: [
              [entry, high],
              [low, entry],
            ],
            backgroundColor: ["rgba(61, 220, 132, 0.55)", "rgba(220, 61, 61, 0.55)"],
            borderColor: ["#3ddc84", "#dc3d3d"],
            borderWidth: 1,
          },
        ],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const [a, b] = ctx.raw;
                return `${fmtNum(Math.min(a, b))} .. ${fmtNum(Math.max(a, b))}`;
              },
            },
          },
        },
        scales: { x: { title: { display: true, text: "Price" } } },
      },
    });
  }

  function renderMaeMfePanel(el, lifecycle) {
    if (!lifecycle) {
      StateMatrix.render(el, {
        state: "unsupported",
        reason: "This allocation is a plain (unmanaged) account position -- MAE/MFE excursion tracking (PositionLifecycle.mae/mfe) only exists for managed-lifecycle positions in this build.",
      });
      return;
    }
    if (lifecycle.entry_price === null || lifecycle.entry_price === undefined || !lifecycle.has_price_data) {
      StateMatrix.render(el, {
        state: "empty",
        emptyMessage: "No price/excursion data available for this position yet -- either its entry fill price is still unresolved (pending-entry path) or its broker has no live-price feed capability, so app/lifecycle/models.py's PositionLifecycle has recorded no real price observation to chart. Never shown as a fabricated flat line.",
      });
      return;
    }
    StateMatrix.render(el, {
      state: "ready",
      html: `<div id="tr03-maemfe-chart-wrap"></div>
             <div class="econ-stats">
               <div><span class="muted">Entry price</span><br><span class="num">${fmtNum(lifecycle.entry_price)}</span></div>
               <div><span class="muted">Highest reached</span><br><span class="num">${fmtNum(lifecycle.highest_price_since_entry)}</span>${lifecycle.highest_price_at ? `<br><span class="muted mono">${escapeHtml(lifecycle.highest_price_at)}</span>` : ""}</div>
               <div><span class="muted">Lowest reached</span><br><span class="num">${fmtNum(lifecycle.lowest_price_since_entry)}</span>${lifecycle.lowest_price_at ? `<br><span class="muted mono">${escapeHtml(lifecycle.lowest_price_at)}</span>` : ""}</div>
               <div><span class="muted">MAE (adverse excursion)</span><br><span class="num" id="tr03-mae-value">${fmtNum(lifecycle.mae)}</span></div>
               <div><span class="muted">MFE (favorable excursion)</span><br><span class="num" id="tr03-mfe-value">${fmtNum(lifecycle.mfe)}</span></div>
             </div>`,
    });
    renderMaeMfeChart(el, lifecycle);
  }

  function shell() {
    return `
      <section class="tr-panel" id="tr03-p01"><h2>Identity and plan</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr03-p02"><h2>Quantity ledger</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr03-p07"><h2>Price excursion (MAE/MFE)</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr03-p03"><h2>Price/stop timeline</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr03-p04"><h2>Orders and fills</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr03-p05"><h2>Protection transfer</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr03-p06"><h2>Controls</h2><div class="tr-panel-body"></div></section>
    `;
  }

  async function load(ctx) {
    const { account_id: accountId, symbol } = ctx.params;
    const els = {
      identity: ctx.container.querySelector("#tr03-p01 .tr-panel-body"),
      ledger: ctx.container.querySelector("#tr03-p02 .tr-panel-body"),
      maeMfe: ctx.container.querySelector("#tr03-p07 .tr-panel-body"),
      timeline: ctx.container.querySelector("#tr03-p03 .tr-panel-body"),
      orders: ctx.container.querySelector("#tr03-p04 .tr-panel-body"),
      transfer: ctx.container.querySelector("#tr03-p05 .tr-panel-body"),
      controls: ctx.container.querySelector("#tr03-p06 .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const positionsRes = await ctx.fetchJSON("/positions");
    if (positionsRes.status === 401 || positionsRes.status === 403) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "denied", deniedCode: positionsRes.status });
      return;
    }
    if (!positionsRes.ok) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "error", message: "Could not load this allocation." });
      return;
    }

    const positions = (positionsRes.data && positionsRes.data.positions) || [];
    const lifecycles = (positionsRes.data && positionsRes.data.managed_lifecycles) || [];
    const position = positions.find((p) => p.account_id === accountId && p.symbol === symbol);
    const lifecycle = lifecycles.find((l) => l.account_id === accountId && l.symbol === symbol);

    if (!position && !lifecycle) {
      for (const el of Object.values(els)) {
        StateMatrix.render(el, {
          state: "empty",
          emptyMessage: "No verified allocation is available for this reference.",
          nextRoute: "/trade/positions",
          nextLabel: "Go to Positions and allocations",
        });
      }
      return;
    }

    StateMatrix.render(els.identity, {
      state: "ready",
      html: `<p><span class="mono">${escapeHtml(accountId)}</span> / <span class="mono">${escapeHtml(symbol)}</span></p>
             <p>${lifecycle ? boolPill(true, "managed lifecycle", "") : pill("plain (unmanaged)", "muted")}</p>`,
    });

    const owned = lifecycle ? lifecycle.owned_quantity : position ? position.net_quantity : null;
    const covered = lifecycle ? lifecycle.covered_quantity : null;
    const uncovered = lifecycle ? lifecycle.uncovered_quantity : null;
    const closeable = lifecycle && lifecycle.pending_exit && !lifecycle.pending_exit.remainder_resolved
      ? 0
      : owned;
    StateMatrix.render(els.ledger, {
      state: "ready",
      html: `<div class="econ-stats">
               <div><span class="muted">M-TR-03-01 Owned</span><br><span class="num">${owned === null || owned === undefined ? "—" : fmtNum(owned)}</span></div>
               <div><span class="muted">M-TR-03-02 Native covered</span><br><span class="num">${covered === null || covered === undefined ? "not tracked (plain account)" : fmtNum(covered)}</span></div>
               <div><span class="muted">M-TR-03-03 Uncovered</span><br><span class="num">${uncovered === null || uncovered === undefined ? "not tracked (plain account)" : fmtNum(uncovered)}</span></div>
               <div><span class="muted">M-TR-03-04 Still executable closes</span><br><span class="num">${closeable === null || closeable === undefined ? "—" : fmtNum(closeable)}</span></div>
             </div>`,
    });

    renderMaeMfePanel(els.maeMfe, lifecycle);

    const ordersRes = await ctx.fetchJSON(`/orders?limit=100&account_id=${encodeURIComponent(accountId)}`);
    let symbolOrders = [];
    if (ordersRes.status === 401 || ordersRes.status === 403) {
      StateMatrix.render(els.timeline, { state: "denied", deniedCode: ordersRes.status });
      StateMatrix.render(els.orders, { state: "denied", deniedCode: ordersRes.status });
    } else if (!ordersRes.ok) {
      StateMatrix.render(els.timeline, { state: "error", message: "Could not load order history." });
      StateMatrix.render(els.orders, { state: "error", message: "Could not load order history." });
    } else {
      symbolOrders = ((ordersRes.data && ordersRes.data.orders) || []).filter((o) => o.symbol === symbol);
      if (!symbolOrders.length) {
        StateMatrix.render(els.timeline, { state: "empty", emptyMessage: "No order events recorded for this allocation." });
        StateMatrix.render(els.orders, { state: "empty", emptyMessage: "No order events recorded for this allocation." });
      } else {
        const timelineRows = symbolOrders.map((o) => [
          o.executed_at || "—",
          escapeHtml(o.side || "—"),
          o.status === "filled" ? pill("filled", "ok") : o.status === "pending" ? pill("pending", "warn") : pill(o.status || "—", "bad"),
          escapeHtml(o.message || ""),
        ]);
        // PU-B2: additive real-data enrichment -- when this is a managed
        // lifecycle with real price observations, surface PU-A1's own
        // highest/lowest-price-reached markers (with their real
        // timestamps) as extra annotation rows alongside the order
        // journal, rather than building a second, separate chart for the
        // same two data points the MAE/MFE panel above already charts.
        const markerRows = [];
        if (lifecycle && lifecycle.has_price_data) {
          markerRows.push(
            ["Highest price reached", lifecycle.highest_price_at || "—", pill(fmtNum(lifecycle.highest_price_since_entry), "ok"), "PU-A1 real price observation (PositionLifecycle.highest_price_since_entry)"],
            ["Lowest price reached", lifecycle.lowest_price_at || "—", pill(fmtNum(lifecycle.lowest_price_since_entry), "bad"), "PU-A1 real price observation (PositionLifecycle.lowest_price_since_entry)"]
          );
        }
        const markerNote = markerRows.length
          ? `<p class="section-note">Real highest/lowest-price-reached markers (PU-A1), annotated below the order journal:</p>${table(
              ["Marker", "At", "Price", "Source"],
              markerRows,
              ""
            )}`
          : "";
        StateMatrix.render(els.timeline, {
          state: "ready",
          html: `<p class="section-note">Order execution history (newest first) -- this build has no separately tracked stop-revision event log, so this timeline is the order journal, not a full price/stop revision history.</p>${table(
            ["Executed at", "Side", "Status", "Message"],
            timelineRows,
            "No order events."
          )}${markerNote}`,
        });

        const orderRows = symbolOrders.map((o) => [
          `<span class="mono">${escapeHtml(o.broker_order_id || String(o.id))}</span>`,
          `<span class="tr-not-tracked">not distinctly tracked (see message)</span>`,
          fmtNum(o.requested_quantity),
          fmtNum(o.filled_quantity),
          `<span class="tr-not-exposed">not exposed</span>`,
          o.status === "filled" ? pill("filled", "ok") : o.status === "pending" ? pill("pending", "warn") : pill(o.status || "—", "bad"),
          escapeHtml(o.message || ""),
        ]);
        StateMatrix.render(els.orders, {
          state: "ready",
          html: table(
            ["Family/order", "Purpose", "Requested", "Cumulative filled", "Remaining possible", "Status", "Coverage evidence"],
            orderRows,
            "No orders."
          ),
        });
      }
    }

    if (!lifecycle) {
      StateMatrix.render(els.transfer, { state: "unsupported", reason: "This allocation is a plain (unmanaged) account position -- protection transfer only applies to managed-lifecycle positions in this build." });
    } else {
      const pe = lifecycle.pending_exit;
      const pen = lifecycle.pending_entry;
      const rows = [
        ["Stop status", escapeHtml(lifecycle.stop_status || "—")],
        ["Stop price", lifecycle.stop_price === null || lifecycle.stop_price === undefined ? "—" : fmtNum(lifecycle.stop_price)],
        ["Pending exit", pe ? `${fmtNum(pe.unresolved_remainder)} unresolved (${escapeHtml(pe.phase)}, order ${escapeHtml(pe.broker_order_id || "—")})` : pill("none", "ok")],
        ["Pending entry", pen ? `${fmtNum(pen.requested_quantity)} requested, ${fmtNum(pen.confirmed_filled_quantity)} confirmed` : pill("none", "ok")],
        ["Halted", lifecycle.halted ? pill(lifecycle.halt_reason || "halted", "bad") : pill("no", "ok")],
      ];
      StateMatrix.render(els.transfer, { state: "ready", html: table(["Field", "Value"], rows, "No protection detail.") });
    }

    const canClose = closeable !== null && closeable !== undefined && closeable > 0;
    StateMatrix.render(els.controls, {
      state: "ready",
      html: `
        <div class="tr-controls-row">
          <button type="button" class="danger" id="tr03-exit-now" ${canClose ? "" : "disabled"}>Exit now (full close)</button>
          <span class="section-note">Bypasses routing; targets exactly this account/symbol. Reuses the existing engine close path -- no new financial capability.</span>
        </div>
      `,
    });
    const exitBtn = els.controls.querySelector("#tr03-exit-now");
    if (exitBtn) {
      exitBtn.addEventListener("click", async () => {
        if (!confirm(`Exit the ${symbol} position on account "${accountId}" immediately at market?`)) return;
        try {
          const result = await postJSON(`/positions/${encodeURIComponent(accountId)}/${encodeURIComponent(symbol)}/close`, {});
          alert(`${symbol} on ${accountId}: ${result.status}${result.message ? " — " + result.message : ""}`);
        } catch (err) {
          alert(`Exit failed: ${err.message}`);
        }
        await load(ctx);
      });
    }
    const unsupportedControls = document.createElement("div");
    unsupportedControls.className = "tr-controls-unsupported";
    StateMatrix.render(unsupportedControls, {
      state: "unsupported",
      reason: "Preview partial reduction (TR-03-A01), preview stop change (TR-03-A02) and request reconciliation (TR-03-A03) have no backing capability in this build -- app/lifecycle/manager.py's exit/stop-resize/reconciliation machinery is internal-only, with no owner-facing HTTP action to preview or trigger any of the three. Not implemented here rather than faked.",
    });
    els.controls.appendChild(unsupportedControls);

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr03 = {
    title: "Position and protection detail",
    breadcrumb: "Trade / Positions / Detail",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = shell();
      await load(ctx);
      ctx.registerPoll(`tr03:${ctx.params.account_id}:${ctx.params.symbol}`, 10000, () => load(ctx));
    },
  };
  Router.register("/trade/positions/:account_id/:symbol", "tr03");
})();
