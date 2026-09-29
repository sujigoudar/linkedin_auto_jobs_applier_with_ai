/* TR-03: Position and protection detail (`#/trade/positions/:account_id/:symbol`).
 *
 * 2026-09 redesign: the design review flagged this as "one of the most
 * important screens and currently one of the thinnest" and asked for a
 * complete trade-lifecycle page -- a prominent header, a centerpiece
 * price chart (entries/average entry/mark/stops/targets/exits), a
 * visualized quantity ledger, order-family grouping, and Level-3 caveats
 * rendered as Components.renderCapabilityState instead of bare italic
 * prose. Every number below traces to a real endpoint already used
 * elsewhere in this codebase -- nothing here is a new backend capability,
 * and every gap this schema genuinely has is stated as such rather than
 * approximated.
 *
 * Real backing data (all read-only GETs, all already used by sibling
 * TR-0x views):
 *   - GET /positions -- `positions` (account_id/symbol/net_quantity,
 *     signed -- record_fill (app/db.py) updates this row for BOTH plain
 *     and managed-lifecycle accounts, so its sign is this page's one
 *     real source for "long" vs "short", even for a managed position) and
 *     `managed_lifecycles` (app/main.py's `_managed_lifecycle_snapshot` --
 *     owned/covered/uncovered quantity, stop status/price, pending_exit/
 *     pending_entry, entry_price/highest/lowest-since-entry/mae/mfe/
 *     has_price_data, halted/halt_reason).
 *   - GET /orders?account_id=... -- this account's orders (filtered
 *     client-side to this symbol), each real row carrying `signal_id` and
 *     `broker` -- see app/static/views/tr06.js's own docstring: grouping
 *     by `signal_id` is "the closest real family concept this schema
 *     has", so the Orders/fills panel below genuinely groups by it
 *     (not a flat table with a wished-for field).
 *   - GET /positions/{account_id}/{symbol}/stop-events -- PU-A4's real,
 *     append-only stop/target event log (STOP_PLACED/STOP_TIGHTENED/
 *     PROTECTION_FAILED/TARGET_HIT -- see app/lifecycle/models.py's
 *     `StopTargetEventType` for exactly which event types exist and which
 *     do not, e.g. no breakeven/trailing-activation event on this
 *     branch).
 *   - GET /signals?limit=500 -- matched client-side by the `signal_id`(s)
 *     this position's own orders carry, for the real provider (`source`)/
 *     analyst attribution `positions` itself doesn't carry per-position
 *     (see app/static/views/tr02.js's own docstring on why).
 *   - GET /accounts -- this account's real config row (broker,
 *     managed_lifecycle, max_notional_exposure, multiplier/
 *     fixed_quantity) -- the closest real "management policy" concept
 *     this schema has; there is no separate policy object.
 *   - GET /accounts/{account_id}/economics -- E06's real average-cost/
 *     realized-P&L replay (app/economics.py). `per_symbol[symbol].
 *     average_cost` is a genuine, complete (no LIMIT), volume-weighted
 *     average cost basis of the CURRENTLY OPEN quantity -- used here as
 *     this header's "average entry" (a materially better real figure
 *     than `PositionLifecycle.entry_price`, which app/lifecycle/models.py
 *     only ever sets from the FIRST entry fill and never updates again).
 *     `realized_pnl` is closed-fills-only, per that module's own
 *     documented scope ("unrealized P&L is not reported here").
 *
 * Honest, deliberately-not-fabricated gaps (each rendered via
 * Components.renderCapabilityState, never bare italic prose, never a
 * fabricated chart series/line/value):
 *   - Current mark / live unrealized P&L: NO endpoint in this codebase
 *     projects a live/current price for a position. `PositionLifecycle.
 *     last_observed_price` (PU-A3) exists in app/lifecycle/models.py but
 *     is never serialized by `_managed_lifecycle_snapshot` or any other
 *     GET route (confirmed by reading app/main.py in full); `highest_/
 *     lowest_price_since_entry` are EXTREMES, not "now" -- charting or
 *     headlining either as "current mark" would misrepresent it. E06's
 *     own `AccountEconomics.to_dict()` says as much in its own `note`
 *     field: "last_fill_price is the last price this account actually
 *     traded at, not a live market quote -- unrealized P&L is not
 *     reported here." So mark and unrealized P&L/% are both rendered as
 *     an honest capability-state, not a stale/fabricated number.
 *   - Strategy, portfolio: no such field/table exists anywhere in this
 *     schema (checked app/models.py, app/lifecycle/models.py, app/db.py's
 *     full schema) -- rendered as not_tracked.
 *   - Planned/pending target PRICE as a distinct field: `Target.
 *     trigger_price` (app/lifecycle/models.py) is never serialized by any
 *     GET endpoint. The one real trace of it is `PendingExit.reason`,
 *     which app/lifecycle/manager.py formats deterministically as
 *     `f"target @ {target.trigger_price}"` ONLY when `source == "target"`
 *     (verified by reading every `reason=` call site in that file) --
 *     parsed here defensively (a regex that must match the full expected
 *     shape) and always labeled as "parsed from the order reason text",
 *     never presented as if it came from a dedicated numeric field. If it
 *     doesn't match, the reason is shown as plain text and nothing is
 *     plotted for it.
 *   - Signal revisions: signals are immutable once received in this
 *     schema (no edit/amend endpoint or column) -- not_tracked.
 *   - Deadline (time-exit): `PositionPlan.time_exit` is never serialized
 *     by any GET endpoint -- not_tracked (the one real trace, a
 *     `pending_exit.source == "time_exit"` reason string, is surfaced as
 *     plain text the same defensive way as the target-price parse above,
 *     never charted as a date).
 *   - OHLC candlestick / cost waterfall / cross-position MAE scatter:
 *     same documented gaps as the previous build of this file (see git
 *     history) -- still true, still not fabricated.
 *
 * Rule-6 boundary (unchanged from the previous build): TR-03-A01/A02/A03
 * (preview partial reduction / preview stop change / request
 * reconciliation) have NO backing endpoint anywhere in this codebase --
 * the Controls panel renders those as an honest Components.
 * renderCapabilityState, not fake forms. The one real, already-wired
 * action is the existing `POST /positions/{account_id}/{symbol}/close`
 * full exit.
 */
(function () {
  "use strict";

  // Chart.js instances, destroy-and-recreate on reload -- same convention
  // TR-12/TR-14/TR-15/this file's own previous MAE/MFE chart established.
  let maeMfeChart = null;
  let priceChart = null;

  // ---------------------------------------------------------------------
  // Pure helpers (no DOM) -- kept separate and simple enough to hand-check
  // by eye, since a bug here would visually UNDERSTATE uncommitted/
  // unprotected quantity, the exact failure mode this whole page exists
  // to expose.
  // ---------------------------------------------------------------------

  // The one real trace of a pending target's trigger price (see this
  // file's module docstring) -- must match the FULL string
  // app/lifecycle/manager.py always formats, or it is not used.
  const TARGET_REASON_RE = /^target @ (-?[0-9]+(?:\.[0-9]+)?)$/;
  const TIME_EXIT_REASON_RE = /^time exit reached \((.+)\)$/;

  function parsePendingExitReason(pendingExit) {
    if (!pendingExit || !pendingExit.reason) return null;
    if (pendingExit.source === "target") {
      const m = TARGET_REASON_RE.exec(pendingExit.reason);
      if (m) return { kind: "target", price: Number(m[1]) };
    }
    if (pendingExit.source === "time_exit") {
      const m = TIME_EXIT_REASON_RE.exec(pendingExit.reason);
      if (m) return { kind: "time_exit", at: m[1] };
    }
    return null;
  }

  /**
   * The safety-critical computation this batch was told to load-bearing
   * verify: how much of what's owned sits behind a confirmed working
   * stop, how much is allocated to a pending target order, and how much
   * is genuinely uncommitted. Every input is a real field already
   * returned by GET /positions -- nothing here is recomputed from a
   * lower-level source that could drift from it (owned/covered/uncovered
   * are used exactly as the backend reports them, same rule the existing
   * MAE/MFE panel already follows for mae/mfe).
   *
   * Returns { supported, owned, segments } -- `segments` is `[]` (never a
   * fabricated single "owned" segment) when `supported` is false, so
   * callers render a capability-state instead of a misleading one-color
   * bar.
   */
  function computeQuantityLedgerSegments(lifecycle, position) {
    if (!lifecycle) {
      return {
        supported: false,
        owned: position ? Math.abs(position.net_quantity) : null,
        segments: [],
      };
    }
    const owned = lifecycle.owned_quantity;
    const workingStopCovered = lifecycle.covered_quantity || 0;
    const pendingExit = lifecycle.pending_exit;
    const pendingTargetQty =
      pendingExit && pendingExit.source === "target" ? pendingExit.unresolved_remainder || 0 : 0;
    // Reuse the backend's own uncovered_quantity (owned - covered) rather
    // than recomputing owned - workingStopCovered by hand here -- same
    // "never recompute what the backend already computed" rule this
    // file's MAE/MFE panel documents. Clamped at 0: a pending-target
    // allocation can never make "uncommitted" negative in this bar.
    const uncommitted = Math.max(0, (lifecycle.uncovered_quantity || 0) - pendingTargetQty);
    return {
      supported: true,
      owned,
      segments: [
        { label: "Working stop", value: workingStopCovered, tone: "ok" },
        { label: "Pending target", value: pendingTargetQty, tone: "warn" },
        { label: "Uncommitted", value: uncommitted, tone: "crit" },
      ],
    };
  }

  // ---------------------------------------------------------------------
  // MAE/MFE chart (unchanged from the previous build of this file).
  // ---------------------------------------------------------------------

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
      Components.renderCapabilityState(el, {
        status: "unsupported",
        reason: "This allocation is a plain (unmanaged) account position -- MAE/MFE excursion tracking (PositionLifecycle.mae/mfe) only exists for managed-lifecycle positions in this build.",
      });
      return;
    }
    if (lifecycle.entry_price === null || lifecycle.entry_price === undefined || !lifecycle.has_price_data) {
      Components.renderCapabilityState(el, {
        status: "not_tracked",
        reason: "No price/excursion data available for this position yet -- either its entry fill price is still unresolved (pending-entry path) or its broker has no live-price feed capability, so app/lifecycle/models.py's PositionLifecycle has recorded no real price observation to chart.",
      });
      return;
    }
    const wrap = document.createElement("div");
    wrap.innerHTML = `<div id="tr03-maemfe-chart-wrap"></div>
             <div class="econ-stats">
               <div><span class="muted">Entry price</span><br><span class="num">${fmtNum(lifecycle.entry_price)}</span></div>
               <div><span class="muted">Highest reached</span><br><span class="num">${fmtNum(lifecycle.highest_price_since_entry)}</span>${lifecycle.highest_price_at ? `<br><span class="muted mono">${escapeHtml(lifecycle.highest_price_at)}</span>` : ""}</div>
               <div><span class="muted">Lowest reached</span><br><span class="num">${fmtNum(lifecycle.lowest_price_since_entry)}</span>${lifecycle.lowest_price_at ? `<br><span class="muted mono">${escapeHtml(lifecycle.lowest_price_at)}</span>` : ""}</div>
               <div><span class="muted">MAE (adverse excursion)</span><br><span class="num" id="tr03-mae-value">${fmtNum(lifecycle.mae)}</span></div>
               <div><span class="muted">MFE (favorable excursion)</span><br><span class="num" id="tr03-mfe-value">${fmtNum(lifecycle.mfe)}</span></div>
             </div>`;
    el.innerHTML = "";
    el.appendChild(wrap);
    renderMaeMfeChart(el, lifecycle);
  }

  // ---------------------------------------------------------------------
  // Centerpiece price chart.
  // ---------------------------------------------------------------------

  function isEntryFill(order, entrySide) {
    return order.side === entrySide && order.status === "filled" && order.filled_price !== null && order.filled_price !== undefined;
  }

  function isExitFill(order, entrySide) {
    return order.side && order.side !== entrySide && order.status === "filled" && order.filled_price !== null && order.filled_price !== undefined;
  }

  /** Build the chart's series + a list of genuinely-missing categories, all
   * from real data only -- see this file's module docstring for exactly
   * which series are real for which account/position shape. */
  function buildPriceChartPlan(position, lifecycle, symbolOrders, stopEvents) {
    const entrySide = position && position.net_quantity < 0 ? "sell" : "buy";
    const entryFills = symbolOrders.filter((o) => isEntryFill(o, entrySide));
    const exitFills = symbolOrders.filter((o) => isExitFill(o, entrySide));

    const stopPlaced = stopEvents.filter((e) => e.event_type === "stop_placed");
    const stopTightened = stopEvents.filter((e) => e.event_type === "stop_tightened");
    const targetHits = stopEvents.filter((e) => e.event_type === "target_hit");

    const points = []; // {at, price, kind, label}
    entryFills.forEach((o) => points.push({ at: o.executed_at, price: o.filled_price, kind: "entry", label: `Entry fill (${fmtNum(o.filled_quantity)})` }));
    exitFills.forEach((o) => points.push({ at: o.executed_at, price: o.filled_price, kind: "exit", label: `Exit/partial-exit fill (${fmtNum(o.filled_quantity)})` }));
    stopPlaced.forEach((e) => points.push({ at: e.at, price: e.price, kind: "stop_placed", label: "Stop placed" }));
    stopTightened.forEach((e) => points.push({ at: e.at, price: e.price, kind: "stop_tightened", label: `Stop tightened (was ${fmtNum(e.previous_price)})` }));
    targetHits.forEach((e) => points.push({ at: e.at, price: e.price, kind: "target_hit", label: "Target hit" }));

    const pendingParsed = lifecycle ? parsePendingExitReason(lifecycle.pending_exit) : null;
    if (pendingParsed && pendingParsed.kind === "target" && Number.isFinite(pendingParsed.price)) {
      points.push({ at: null, price: pendingParsed.price, kind: "pending_target", label: "Pending target order (parsed from order reason text)" });
    }

    const gaps = [];
    if (!lifecycle) {
      gaps.push({
        status: "unsupported",
        reason: "This is a plain (unmanaged) account position -- initial stop, stop revisions, working stop and target-hit history only exist for managed-lifecycle positions in this build (app/lifecycle/manager.py never runs for a plain account).",
      });
    } else {
      if (!stopPlaced.length) {
        gaps.push({
          status: "not_tracked",
          reason: "No real STOP_PLACED event exists yet for this position (app/lifecycle/manager.py's stop_target_events log) -- no initial stop to plot.",
        });
      }
      if (!stopTightened.length) {
        gaps.push({
          status: "not_tracked",
          reason: "No real STOP_TIGHTENED event exists yet for this position -- either the stop has never been revised, or this position hasn't yet had a logical TIGHTEN_STOP target/trailing ratchet fire.",
        });
      }
      if (!targetHits.length) {
        gaps.push({
          status: "not_tracked",
          reason: "No real TARGET_HIT event exists yet for this position -- either it has no take_profit target, or that target hasn't fired.",
        });
      }
      if (lifecycle.stop_status !== "stop_confirmed" || lifecycle.stop_price === null || lifecycle.stop_price === undefined) {
        gaps.push({
          status: "not_tracked",
          reason: `No confirmed working stop right now (stop_status: ${lifecycle.stop_status || "unknown"}) -- nothing real to plot as the current working-stop line.`,
        });
      }
      if (!pendingParsed) {
        gaps.push({
          status: "not_tracked",
          reason: "No dedicated target-price field is exposed by this API -- the only real trace of a planned target's trigger price is the pending-exit reason text (app/lifecycle/manager.py), which is either absent or didn't match the expected 'target @ <price>' shape here.",
        });
      }
    }
    if (!entryFills.length) {
      gaps.push({ status: "not_tracked", reason: "No filled entry order is recorded for this position yet -- no entry execution point to plot." });
    }

    return {
      entryFills,
      exitFills,
      stopPlaced,
      stopTightened,
      targetHits,
      pendingParsed,
      points,
      gaps,
      hasAnyPoints: points.length > 0,
      averageEntry:
        lifecycle && lifecycle.entry_price !== null && lifecycle.entry_price !== undefined ? lifecycle.entry_price : null,
      workingStop:
        lifecycle && lifecycle.stop_status === "stop_confirmed" && lifecycle.stop_price !== null && lifecycle.stop_price !== undefined
          ? lifecycle.stop_price
          : null,
    };
  }

  function renderPriceChart(container, plan, averageEntryOverride) {
    if (priceChart) {
      priceChart.destroy();
      priceChart = null;
    }
    const wrap = container.querySelector("#tr03-price-chart-wrap");
    if (!wrap) return;
    if (!plan.hasAnyPoints) {
      wrap.innerHTML = "";
      return;
    }
    wrap.innerHTML = `<div class="chart-container" style="height:340px"><canvas id="tr03-price-chart"></canvas></div>`;

    // Category x-axis of every distinct real timestamp this chart has a
    // point for (chronological) -- no vendored date adapter in this
    // codebase (see app/static/vendor/), so a category axis (same
    // technique this file's own MAE/MFE chart and TR-12's charts already
    // use) is the honest choice over silently assuming one is available.
    const timedPoints = plan.points.filter((p) => p.at);
    const labels = Array.from(new Set(timedPoints.map((p) => p.at))).sort();
    const indexOf = (at) => labels.indexOf(at);

    function scatterDataset(label, kind, color) {
      const pts = plan.points.filter((p) => p.kind === kind && p.at);
      if (!pts.length) return null;
      return {
        label,
        data: pts.map((p) => ({ x: indexOf(p.at), y: p.price })),
        showLine: false,
        pointBackgroundColor: color,
        pointBorderColor: color,
        pointRadius: 6,
        pointHoverRadius: 8,
        parsing: false,
      };
    }

    function flatLine(label, value, color) {
      if (value === null || value === undefined || !labels.length) return null;
      return {
        label,
        data: labels.map((_, i) => ({ x: i, y: value })),
        borderColor: color,
        borderDash: [6, 4],
        borderWidth: 2,
        pointRadius: 0,
        fill: false,
      };
    }

    const avgEntry = averageEntryOverride !== null && averageEntryOverride !== undefined ? averageEntryOverride : plan.averageEntry;

    const datasets = [
      flatLine("Average entry", avgEntry, "#5b8cff"),
      flatLine("Current working stop", plan.workingStop, "#ef5b5b"),
      scatterDataset("Entry execution", "entry", "#3ddc84"),
      scatterDataset("Partial exit / exit fill", "exit", "#f5b942"),
      scatterDataset("Stop placed (initial)", "stop_placed", "#ff5d3b"),
      scatterDataset("Stop revision (tightened)", "stop_tightened", "#dc3d3d"),
      scatterDataset("Target hit", "target_hit", "#3ddc84"),
      scatterDataset("Pending target (parsed)", "pending_target", "#8b93a7"),
    ].filter(Boolean);

    priceChart = new Chart(wrap.querySelector("#tr03-price-chart").getContext("2d"), {
      type: "scatter",
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        parsing: false,
        scales: {
          x: {
            type: "linear",
            ticks: {
              callback: (value) => labels[value] || "",
              autoSkip: true,
            },
            title: { display: true, text: "Time (chronological, real events only)" },
          },
          y: { title: { display: true, text: "Price" } },
        },
        plugins: {
          legend: { position: "bottom" },
          tooltip: {
            callbacks: {
              title: (items) => (items.length ? labels[items[0].raw.x] || "" : ""),
              label: (ctx) => `${ctx.dataset.label}: ${fmtNum(ctx.raw.y)}`,
            },
          },
        },
      },
    });
  }

  function renderPriceChartPanel(els, plan, averageEntryOverride) {
    const html = plan.hasAnyPoints
      ? `<div id="tr03-price-chart-wrap"></div>`
      : `<p class="sm-empty-message">No real, timestamped price events (entry fills, stop events, target hits) exist yet for this position -- nothing to plot.</p>`;
    els.chart.innerHTML = html;
    renderPriceChart(els.chart, plan, averageEntryOverride);

    els.chartGaps.innerHTML = "";
    if (!plan.gaps.length) {
      els.chartGaps.innerHTML = `<p class="section-note">Every chartable series this build can track is real and plotted above -- no gaps for this position.</p>`;
      return;
    }
    plan.gaps.forEach((gap) => {
      const gapEl = document.createElement("div");
      gapEl.className = "tr03-chart-gap";
      Components.renderCapabilityState(gapEl, gap);
      els.chartGaps.appendChild(gapEl);
    });
  }

  // ---------------------------------------------------------------------
  // Orders/fills grouped by real order family (signal_id).
  // ---------------------------------------------------------------------

  function renderOrdersPanel(el, symbolOrders, signalsById) {
    if (!symbolOrders.length) {
      StateMatrix.render(el, { state: "empty", emptyMessage: "No order events recorded for this allocation." });
      return;
    }
    const families = new Map(); // signal_id (string, incl. "null") -> orders[]
    symbolOrders.forEach((o) => {
      const key = o.signal_id === null || o.signal_id === undefined ? "" : String(o.signal_id);
      if (!families.has(key)) families.set(key, []);
      families.get(key).push(o);
    });

    const familyBlocks = Array.from(families.entries())
      .map(([signalId, orders]) => {
        const signal = signalId && signalsById.has(signalId) ? signalsById.get(signalId) : null;
        const heading = signalId
          ? `Order family -- signal <span class="mono">${escapeHtml(signalId)}</span>${
              signal
                ? ` (${escapeHtml(signal.source || "unknown source")}${signal.analyst ? ` / ${escapeHtml(signal.analyst)}` : ""}, received ${escapeHtml(signal.received_at || "—")})`
                : ` (originating signal outside the most recent 500 signals fetched -- provider/analyst not resolved here)`
            }`
          : `Orders with no recorded signal_id (not attributable to any one originating signal)`;
        const rows = orders.map((o) => [
          `<span class="mono">${escapeHtml(o.broker_order_id || String(o.id))}</span>`,
          escapeHtml(o.broker || "—"),
          escapeHtml(o.side || "—"),
          fmtNum(o.requested_quantity),
          fmtNum(o.filled_quantity),
          o.filled_price === null || o.filled_price === undefined ? "—" : fmtNum(o.filled_price),
          o.status === "filled" ? pill("filled", "ok") : o.status === "pending" ? pill("pending", "warn") : pill(o.status || "—", "bad"),
          escapeHtml(o.executed_at || "—"),
          escapeHtml(o.message || ""),
        ]);
        return `<div class="tr03-order-family">
          <p class="section-note">${heading}</p>
          ${table(["Broker order", "Broker", "Side", "Requested", "Filled", "Fill price", "Status", "Executed at", "Message"], rows, "No orders.")}
        </div>`;
      })
      .join("");

    StateMatrix.render(el, {
      state: "ready",
      html: `<p class="section-note">Grouped by <code>signal_id</code> -- the closest real order-family concept this schema has today (every order sharing one signal_id came from the same originating signal; see app/static/views/tr06.js's own "Open family" feature for the same real grouping). A backend-added dedicated family field would slot in here without changing this grouping's shape.</p>${familyBlocks}`,
    });
  }

  // ---------------------------------------------------------------------
  // Header (KPI band).
  // ---------------------------------------------------------------------

  function renderHeader(el, ctxData) {
    const { symbol, position, lifecycle, symbolEconomics, entrySide } = ctxData;
    const owned = lifecycle ? lifecycle.owned_quantity : position ? Math.abs(position.net_quantity) : null;
    const avgEntry = symbolEconomics && symbolEconomics.average_cost !== null && symbolEconomics.average_cost !== undefined
      ? symbolEconomics.average_cost
      : null;
    const realizedPnl = symbolEconomics ? symbolEconomics.realized_pnl : 0;

    const items = [
      { label: "Symbol", value: symbol, tone: "neutral" },
      { label: "Side", value: entrySide === "sell" ? "Short" : "Long", tone: "neutral" },
      { label: "Quantity owned", value: owned === null || owned === undefined ? "—" : fmtNum(owned), tone: "neutral" },
      {
        label: "Average entry",
        value: avgEntry === null ? "—" : fmtNum(avgEntry),
        sublabel: avgEntry === null ? "No real average-cost basis yet (accounts/economics)" : "GET /accounts/{id}/economics average_cost",
        tone: "neutral",
      },
      {
        label: "Current mark",
        value: "Not tracked",
        sublabel: "No endpoint in this build projects a live/current price",
        tone: "warn",
      },
      {
        label: "Realized P&L (closed fills)",
        value: `${realizedPnl >= 0 ? "+" : ""}${fmtNum(realizedPnl)}`,
        sublabel: "Not a live unrealized basis -- see accounts/economics",
        tone: realizedPnl > 0 ? "ok" : realizedPnl < 0 ? "crit" : "neutral",
      },
      {
        label: "Unrealized P&L / %",
        value: "Not tracked",
        sublabel: "Needs a live mark, which this build doesn't have",
        tone: "warn",
      },
    ];
    Components.renderKPIBand(el, { items });
  }

  // ---------------------------------------------------------------------

  function shell() {
    return `
      <section class="tr-panel" id="tr03-header-panel"><div id="tr03-header"></div></section>
      <section class="tr-panel" id="tr03-p01"><h2>Identity and plan</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr03-p-chart">
        <h2>Price chart -- entries, stops, targets, exits</h2>
        <div class="tr-panel-body" id="tr03-chart-body"></div>
        <div id="tr03-chart-gaps"></div>
      </section>
      <section class="tr-panel" id="tr03-p02"><h2>Quantity ledger</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr03-p04"><h2>Orders and fills</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr03-p07"><h2>Price excursion (MAE/MFE)</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr03-p05"><h2>Protection transfer and reconciliation</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr03-p06"><h2>Controls</h2><div class="tr-panel-body"></div></section>
    `;
  }

  async function load(ctx) {
    const { account_id: accountId, symbol } = ctx.params;
    const els = {
      header: ctx.container.querySelector("#tr03-header"),
      identity: ctx.container.querySelector("#tr03-p01 .tr-panel-body"),
      chart: ctx.container.querySelector("#tr03-chart-body"),
      chartGaps: ctx.container.querySelector("#tr03-chart-gaps"),
      ledger: ctx.container.querySelector("#tr03-p02 .tr-panel-body"),
      maeMfe: ctx.container.querySelector("#tr03-p07 .tr-panel-body"),
      orders: ctx.container.querySelector("#tr03-p04 .tr-panel-body"),
      transfer: ctx.container.querySelector("#tr03-p05 .tr-panel-body"),
      controls: ctx.container.querySelector("#tr03-p06 .tr-panel-body"),
    };
    for (const el of [els.identity, els.chart, els.ledger, els.maeMfe, els.orders, els.transfer, els.controls]) {
      StateMatrix.render(el, { state: "loading" });
    }

    const positionsRes = await ctx.fetchJSON("/positions");
    if (positionsRes.status === 401 || positionsRes.status === 403) {
      for (const el of [els.identity, els.chart, els.ledger, els.maeMfe, els.orders, els.transfer, els.controls]) {
        StateMatrix.render(el, { state: "denied", deniedCode: positionsRes.status });
      }
      return;
    }
    if (!positionsRes.ok) {
      for (const el of [els.identity, els.chart, els.ledger, els.maeMfe, els.orders, els.transfer, els.controls]) {
        StateMatrix.render(el, { state: "error", message: "Could not load this allocation." });
      }
      return;
    }

    const positions = (positionsRes.data && positionsRes.data.positions) || [];
    const lifecycles = (positionsRes.data && positionsRes.data.managed_lifecycles) || [];
    const position = positions.find((p) => p.account_id === accountId && p.symbol === symbol);
    const lifecycle = lifecycles.find((l) => l.account_id === accountId && l.symbol === symbol);

    if (!position && !lifecycle) {
      for (const el of [els.identity, els.chart, els.ledger, els.maeMfe, els.orders, els.transfer, els.controls]) {
        StateMatrix.render(el, {
          state: "empty",
          emptyMessage: "No verified allocation is available for this reference.",
          nextRoute: "/trade/positions",
          nextLabel: "Go to Positions and allocations",
        });
      }
      els.header.innerHTML = "";
      return;
    }

    const entrySide = position && position.net_quantity < 0 ? "sell" : "buy";

    // --- Real attribution: account config + economics + orders + signals ---
    const [accountsRes, economicsRes, ordersRes] = await Promise.all([
      ctx.fetchJSON("/accounts"),
      ctx.fetchJSON(`/accounts/${encodeURIComponent(accountId)}/economics`),
      ctx.fetchJSON(`/orders?limit=200&account_id=${encodeURIComponent(accountId)}`),
    ]);

    const accountConfig = accountsRes.ok
      ? ((accountsRes.data && accountsRes.data.accounts) || []).find((a) => a.account_id === accountId)
      : null;
    const symbolEconomics =
      economicsRes.ok && economicsRes.data && economicsRes.data.per_symbol ? economicsRes.data.per_symbol[symbol] || null : null;

    let symbolOrders = [];
    let ordersLoadFailed = false;
    if (ordersRes.status === 401 || ordersRes.status === 403 || !ordersRes.ok) {
      ordersLoadFailed = true;
    } else {
      symbolOrders = ((ordersRes.data && ordersRes.data.orders) || []).filter((o) => o.symbol === symbol);
    }

    const signalIds = Array.from(
      new Set(symbolOrders.map((o) => o.signal_id).filter((id) => id !== null && id !== undefined).map(String))
    );
    let signalsById = new Map();
    if (signalIds.length) {
      const signalsRes = await ctx.fetchJSON("/signals?limit=500");
      if (signalsRes.ok) {
        const allSignals = (signalsRes.data && signalsRes.data.signals) || [];
        allSignals.forEach((s) => {
          if (signalIds.includes(String(s.id))) signalsById.set(String(s.id), s);
        });
      }
    }

    // --- Header ---
    renderHeader(els.header, { symbol, position, lifecycle, symbolEconomics, entrySide });

    // --- Identity and plan ---
    const uniqueSignals = signalIds.map((id) => signalsById.get(id)).filter(Boolean);
    const providerAnalystHtml = uniqueSignals.length
      ? uniqueSignals
          .map((s) => `${escapeHtml(s.source || "unknown")}${s.analyst ? ` / ${escapeHtml(s.analyst)}` : " (no analyst on this signal)"}`)
          .join("; ")
      : null;
    const identityRows = [
      ["Account", `<span class="mono">${escapeHtml(accountId)}</span>`],
      ["Symbol", `<span class="mono">${escapeHtml(symbol)}</span>`],
      ["Position type", lifecycle ? boolPill(true, "managed lifecycle", "") : pill("plain (unmanaged)", "muted")],
      ["Broker", accountConfig ? escapeHtml(accountConfig.broker) : symbolOrders.length ? escapeHtml(symbolOrders[0].broker || "—") : "—"],
      [
        "Provider / analyst",
        providerAnalystHtml ||
          (signalIds.length
            ? "Originating signal(s) found, but outside the most recent 500 signals fetched -- not resolved here"
            : "No order for this position carries a signal_id -- nothing to attribute to a provider/analyst"),
      ],
      [
        "Originating signal(s)",
        signalIds.length
          ? signalIds.map((id) => `<span class="mono">${escapeHtml(id)}</span>`).join(", ")
          : "None recorded on any order for this position",
      ],
      [
        "Management policy",
        accountConfig
          ? `${accountConfig.managed_lifecycle ? "Managed lifecycle" : "Plain (unmanaged)"} · notional cap: ${
              accountConfig.max_notional_exposure === null || accountConfig.max_notional_exposure === undefined
                ? "none"
                : fmtNum(accountConfig.max_notional_exposure)
            } · sizing: ${
              accountConfig.fixed_quantity !== null && accountConfig.fixed_quantity !== undefined
                ? `fixed ${fmtNum(accountConfig.fixed_quantity)}`
                : `× ${fmtNum(accountConfig.multiplier)}`
            }`
          : "Account config not found in GET /accounts",
      ],
    ];
    let identityHtml = table(["Field", "Value"], identityRows, "No identity detail.");
    identityHtml += `<div id="tr03-strategy-gap"></div><div id="tr03-portfolio-gap"></div>`;
    StateMatrix.render(els.identity, { state: "ready", html: identityHtml });
    Components.renderCapabilityState(els.identity.querySelector("#tr03-strategy-gap"), {
      status: "not_tracked",
      reason: "No 'strategy' field or table exists anywhere in this schema (checked app/models.py, app/lifecycle/models.py, app/db.py's full schema).",
    });
    Components.renderCapabilityState(els.identity.querySelector("#tr03-portfolio-gap"), {
      status: "not_tracked",
      reason: "No 'portfolio' concept exists in this codebase -- an account is the only grouping unit this schema tracks.",
    });

    // --- Centerpiece price chart ---
    let stopEvents = [];
    let stopEventsFailed = false;
    if (accountId) {
      const stopEventsRes = await ctx.fetchJSON(`/positions/${encodeURIComponent(accountId)}/${encodeURIComponent(symbol)}/stop-events`);
      if (stopEventsRes.ok) {
        stopEvents = (stopEventsRes.data && stopEventsRes.data.events) || [];
      } else {
        stopEventsFailed = true;
      }
    }
    if (ordersLoadFailed || stopEventsFailed) {
      StateMatrix.render(els.chart, { state: "error", message: "Could not load real order/event history needed for the price chart." });
      els.chartGaps.innerHTML = "";
    } else {
      const chartPlan = buildPriceChartPlan(position, lifecycle, symbolOrders, stopEvents);
      renderPriceChartPanel(els, chartPlan, symbolEconomics ? symbolEconomics.average_cost : null);
    }

    // --- Quantity ledger (visualized) ---
    const ledgerPlan = computeQuantityLedgerSegments(lifecycle, position);
    if (!ledgerPlan.supported) {
      const wrap = document.createElement("div");
      wrap.innerHTML = `<div class="econ-stats"><div><span class="muted">M-TR-03-01 Owned</span><br><span class="num">${
        ledgerPlan.owned === null || ledgerPlan.owned === undefined ? "—" : fmtNum(ledgerPlan.owned)
      }</span></div></div><div id="tr03-ledger-gap"></div>`;
      els.ledger.innerHTML = "";
      els.ledger.appendChild(wrap);
      Components.renderCapabilityState(els.ledger.querySelector("#tr03-ledger-gap"), {
        status: "unsupported",
        reason: "This allocation is a plain (unmanaged) account position -- working-stop coverage and pending-target allocation are only tracked for managed-lifecycle positions in this build. The owned quantity above is real (GET /positions); the rest genuinely isn't tracked, not silently zero.",
      });
    } else {
      const barWrap = document.createElement("div");
      Components.renderQuantityLedgerBar(barWrap, { total: ledgerPlan.owned, segments: ledgerPlan.segments });
      const closeable = lifecycle && lifecycle.pending_exit && !lifecycle.pending_exit.remainder_resolved ? 0 : ledgerPlan.owned;
      const numbersHtml = `<div class="econ-stats">
               <div><span class="muted">M-TR-03-01 Owned</span><br><span class="num">${fmtNum(ledgerPlan.owned)}</span></div>
               <div><span class="muted">M-TR-03-02 Native covered</span><br><span class="num">${fmtNum(lifecycle.covered_quantity)}</span></div>
               <div><span class="muted">M-TR-03-03 Uncovered</span><br><span class="num">${fmtNum(lifecycle.uncovered_quantity)}</span></div>
               <div><span class="muted">M-TR-03-04 Still executable closes</span><br><span class="num">${closeable === null || closeable === undefined ? "—" : fmtNum(closeable)}</span></div>
             </div>`;
      els.ledger.innerHTML = "";
      els.ledger.appendChild(barWrap);
      const numbersEl = document.createElement("div");
      numbersEl.innerHTML = numbersHtml;
      els.ledger.appendChild(numbersEl);
    }

    // --- MAE/MFE ---
    renderMaeMfePanel(els.maeMfe, lifecycle);

    // --- Orders and fills (grouped by real order family) ---
    if (ordersLoadFailed) {
      StateMatrix.render(els.orders, { state: "error", message: "Could not load order history." });
    } else {
      renderOrdersPanel(els.orders, symbolOrders, signalsById);
    }

    // --- Protection transfer / reconciliation ---
    if (!lifecycle) {
      Components.renderCapabilityState(els.transfer, {
        status: "unsupported",
        reason: "This allocation is a plain (unmanaged) account position -- protection transfer only applies to managed-lifecycle positions in this build.",
      });
    } else {
      const pe = lifecycle.pending_exit;
      const pen = lifecycle.pending_entry;
      const parsedReason = parsePendingExitReason(pe);
      const rows = [
        ["Stop status", escapeHtml(lifecycle.stop_status || "—")],
        ["Stop price", lifecycle.stop_price === null || lifecycle.stop_price === undefined ? "—" : fmtNum(lifecycle.stop_price)],
        [
          "Pending exit",
          pe
            ? `${fmtNum(pe.unresolved_remainder)} unresolved (${escapeHtml(pe.phase)}, order ${escapeHtml(pe.broker_order_id || "—")})${
                parsedReason && parsedReason.kind === "target"
                  ? ` -- target @ ${fmtNum(parsedReason.price)} (parsed from reason text)`
                  : pe.reason
                  ? ` -- ${escapeHtml(pe.reason)}`
                  : ""
              }`
            : pill("none", "ok"),
        ],
        ["Pending entry", pen ? `${fmtNum(pen.requested_quantity)} requested, ${fmtNum(pen.confirmed_filled_quantity)} confirmed` : pill("none", "ok")],
        ["Halted", lifecycle.halted ? pill(lifecycle.halt_reason || "halted", "bad") : pill("no", "ok")],
      ];
      const wrap = document.createElement("div");
      wrap.innerHTML = table(["Field", "Value"], rows, "No protection detail.") + `<div id="tr03-reconciliation-gap"></div>`;
      els.transfer.innerHTML = "";
      els.transfer.appendChild(wrap);
      Components.renderCapabilityState(els.transfer.querySelector("#tr03-reconciliation-gap"), {
        status: "unsupported",
        reason: "There is no owner-facing HTTP action to request a reconciliation pass on demand -- app/reconciliation.py only runs on its own internal schedule; this codebase has no 'request reconciliation' endpoint.",
      });
    }

    // --- Controls ---
    const ledgerCloseable = lifecycle
      ? lifecycle.pending_exit && !lifecycle.pending_exit.remainder_resolved
        ? 0
        : lifecycle.owned_quantity
      : position
      ? Math.abs(position.net_quantity)
      : null;
    const canClose = ledgerCloseable !== null && ledgerCloseable !== undefined && ledgerCloseable > 0;
    const controlsWrap = document.createElement("div");
    controlsWrap.innerHTML = `
        <div class="tr-controls-row">
          <button type="button" class="danger" id="tr03-exit-now" ${canClose ? "" : "disabled"}>Exit now (full close)</button>
          <span class="section-note">Bypasses routing; targets exactly this account/symbol. Reuses the existing engine close path -- no new financial capability.</span>
        </div>
        <div id="tr03-controls-gap"></div>
      `;
    els.controls.innerHTML = "";
    els.controls.appendChild(controlsWrap);
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
    Components.renderCapabilityState(els.controls.querySelector("#tr03-controls-gap"), {
      status: "unsupported",
      reason: "Preview partial reduction (TR-03-A01), preview stop change (TR-03-A02) and request reconciliation (TR-03-A03) have no backing capability in this build -- app/lifecycle/manager.py's exit/stop-resize/reconciliation machinery is internal-only, with no owner-facing HTTP action to preview or trigger any of the three.",
    });

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

  // Exposed for tests only -- not part of the runtime page behavior.
  window.Views.tr03._internal = { computeQuantityLedgerSegments, buildPriceChartPlan, parsePendingExitReason };
})();
