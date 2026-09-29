/* TR-04: Incoming signal stream (`#/trade/signals`).
 *
 * Real backing data: GET /signals (every accepted, persisted Signal --
 * `app/db.py`'s `list_recent_signals`, extended in this batch to also
 * project the already-stored `analyst` column it wasn't previously
 * selecting -- see that function's comment). No numeric headline metric
 * per spec ("Do not add a decorative performance KPI").
 *
 * Honest gap, disclosed rather than worked around: the spec's purpose
 * line is "classify every authorized incoming event including rejected
 * or ignored instructions," and its Disposition column implies
 * accepted/rejected/ignored outcomes. This schema has NO disposition
 * ledger -- a signal that fails `SignalValidationError` during parsing
 * (see app/main.py's webhook/SMS/WhatsApp/NinjaTrader routes) is
 * rejected with an HTTP 4xx to the sender and never becomes a `signals`
 * row at all, so there is no persisted record of it to show here.
 * `GET /sources/{source}/classify-messages` (E02) can dry-run arbitrary
 * text against the parser, but that is a separate, deliberately
 * non-persisting analysis tool (see its own docstring: "never ingests a
 * signal"), not a log of real rejected events -- using it here would
 * misrepresent hypothetical classification as historical disposition.
 * Every row below is therefore an accepted signal; Disposition reads
 * "accepted (recorded)" for all of them, with an explicit note that
 * rejected/ignored instructions are not persisted in this build.
 *
 * --- Signal analytics charts (this batch, PU-B3) ---
 * Five additive Chart.js charts, computed client-side from data this
 * screen already fetches (GET /signals, plus a client-side join against
 * GET /orders for the disposition chart only) -- reusing the exact same
 * one-persistent-instance, destroy-and-recreate pattern established by
 * dashboard.html's "economics-chart" (C12) and TR-14/TR-15 (see
 * app/static/views/tr14.js, tr15.js):
 *   1. Signal volume over time -- real `received_at` timestamps bucketed
 *      by hour if the fetched page's own timestamp range spans <=48h, by
 *      day otherwise (never padded: a bucket only appears if a real
 *      signal landed in it).
 *   2. Signals by source, 3. by side, 4. by asset class -- real counts
 *      over the exact same (filtered) signals list the table below
 *      renders.
 *   5. Disposition breakdown -- this schema still has no rejected/ignored
 *      ledger (see the module docstring above), but GET /orders' own
 *      `signal_id` + `status` columns are a REAL, already-persisted join:
 *      for each signal in the current (filtered) list, every order that
 *      references it contributes one real order status (filled/rejected/
 *      pending/...), and a signal with no matching order at all
 *      contributes to a separate, honestly-labeled "accepted, no order
 *      yet" bucket. Nothing here is fabricated: a bucket with zero real
 *      orders behind it simply does not appear.
 *
 * Deliberately NOT built (per this batch's brief):
 *   - A signal-to-order conversion FUNNEL with a "rejected/skipped" stage:
 *     app/rate_limit.py and app/sources/*.py were checked for a real,
 *     queryable parse-failure/skip count and none exists -- a failed
 *     parse never becomes a row anywhere in this schema, so a funnel
 *     chart could only fake that stage. The disposition chart above
 *     already covers the one real join (order status) without inventing
 *     a stage that has no data behind it.
 *   - A signal-arrival heatmap by hour/day: this build's real seed/demo
 *     data is far too sparse (often a handful of signals in one test run)
 *     for an hour x day-of-week grid to be anything but mostly-empty
 *     cells -- that is itself an honest result, but not a meaningful
 *     chart, so it is left out rather than padded to look fuller.
 *   - Provider latency distribution: that is Phase A2/B9's job once new
 *     latency-stage timestamps land (a different, concurrent batch on
 *     this same branch touches app/engine.py/app/execution_quality.py
 *     for exactly that) -- duplicating or preempting it here would fork
 *     that work.
 */
(function () {
  "use strict";

  // Placeholder-slot helper: capability-state badges (Components.
  // renderCapabilityState) render into a real DOM element, but this view
  // builds most panels as one big HTML string (table rows, checklists)
  // before it is inserted. `capSlot` reserves an id inside that string;
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

  // One persistent Chart.js instance per canvas -- destroyed and
  // recreated on every load() the same way tr14.js/tr15.js/dashboard.html
  // already do, so repeated polls (ctx.registerPoll below) never leak
  // chart instances or double-render onto a stale canvas.
  const charts = {
    volume: null,
    source: null,
    side: null,
    assetClass: null,
    disposition: null,
  };

  function destroyChart(key) {
    if (charts[key]) {
      charts[key].destroy();
      charts[key] = null;
    }
  }

  function countBy(items, keyFn) {
    const counts = new Map();
    for (const item of items) {
      const key = keyFn(item);
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    return counts;
  }

  const CHART_PALETTE = ["#3ddc84", "#4f8cff", "#ffb347", "#ff6b6b", "#b47dff", "#3ec6c6", "#e0e0e0"];

  function renderBarChart(canvas, labels, values, label) {
    return new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label,
            data: values,
            backgroundColor: labels.map((_, i) => CHART_PALETTE[i % CHART_PALETTE.length]),
            maxBarThickness: 60,
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

  // 1. Signal volume over time -- real received_at timestamps, bucketed
  // by hour when the fetched page's own span is <=48h, by day otherwise.
  // Never padded: a bucket exists only if at least one real signal fell
  // into it (Map insertion order is chronological because `signals` is
  // iterated in the same order the table below renders, which is
  // ORDER BY received_at DESC from GET /signals -- sorted ascending here
  // for a left-to-right timeline).
  function bucketSignalsByTime(signals) {
    const withTime = signals
      .map((s) => ({ s, t: s.received_at ? Date.parse(s.received_at) : NaN }))
      .filter((x) => !Number.isNaN(x.t))
      .sort((a, b) => a.t - b.t);
    if (!withTime.length) return { labels: [], values: [], granularity: "none" };

    const minT = withTime[0].t;
    const maxT = withTime[withTime.length - 1].t;
    const spanHours = (maxT - minT) / 3_600_000;
    const granularity = spanHours <= 48 ? "hour" : "day";

    function bucketKey(t) {
      const d = new Date(t);
      const iso = d.toISOString();
      return granularity === "hour" ? iso.slice(0, 13) + ":00" : iso.slice(0, 10);
    }

    const counts = new Map();
    for (const { t } of withTime) {
      const key = bucketKey(t);
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    const labels = [...counts.keys()]; // already chronological: withTime was sorted ascending
    const values = labels.map((k) => counts.get(k));
    return { labels, values, granularity };
  }

  function renderVolumeChart(wrap, signals) {
    destroyChart("volume");
    const { labels, values, granularity } = bucketSignalsByTime(signals);
    if (!labels.length) {
      wrap.innerHTML = `<div class="empty">No signals with a real received_at timestamp to chart yet.</div>`;
      return;
    }
    wrap.innerHTML = `<p class="section-note" style="margin-top:0;">Bucketed by ${granularity === "hour" ? "hour" : "day"} (chosen from the real span of received_at timestamps in the current page).</p><div class="chart-container"><canvas id="tr04-volume-chart"></canvas></div>`;
    charts.volume = renderBarChart(wrap.querySelector("#tr04-volume-chart"), labels, values, "Signals received");
  }

  function renderBreakdownChart(wrap, chartKey, canvasId, signals, keyFn, emptyLabel, label) {
    destroyChart(chartKey);
    if (!signals.length) {
      wrap.innerHTML = `<div class="empty">${emptyLabel}</div>`;
      return;
    }
    const counts = countBy(signals, keyFn);
    const labels = [...counts.keys()];
    const values = labels.map((k) => counts.get(k));
    wrap.innerHTML = `<div class="chart-container"><canvas id="${canvasId}"></canvas></div>`;
    charts[chartKey] = renderBarChart(wrap.querySelector(`#${canvasId}`), labels, values, label);
  }

  // 5. Disposition breakdown -- real client-side join of the current
  // (filtered) signals list against GET /orders' own signal_id/status
  // columns. A signal with zero matching orders contributes to an
  // honestly-labeled "accepted, no order yet" bucket rather than being
  // dropped or guessed at.
  function renderDispositionChart(wrap, signals, orders) {
    destroyChart("disposition");
    if (!signals.length) {
      wrap.innerHTML = `<div class="empty">No signals to break down yet.</div>`;
      return;
    }
    const ordersBySignal = new Map();
    for (const o of orders) {
      if (!ordersBySignal.has(o.signal_id)) ordersBySignal.set(o.signal_id, []);
      ordersBySignal.get(o.signal_id).push(o);
    }
    const counts = new Map();
    for (const s of signals) {
      const matching = ordersBySignal.get(s.id) || [];
      if (!matching.length) {
        counts.set("accepted, no order yet", (counts.get("accepted, no order yet") || 0) + 1);
      } else {
        for (const o of matching) {
          const key = `order: ${o.status || "unknown"}`;
          counts.set(key, (counts.get(key) || 0) + 1);
        }
      }
    }
    const labels = [...counts.keys()];
    const values = labels.map((k) => counts.get(k));
    wrap.innerHTML = `<p class="section-note" style="margin-top:0;">Real join of the signals above against GET /orders' own signal_id/status -- "accepted, no order yet" is not a fabricated rejection, it is a signal with no matching order row at all. This build has no separate rejected/ignored ledger (see this screen's module docstring).</p><div class="chart-container"><canvas id="tr04-disposition-chart"></canvas></div>`;
    charts.disposition = renderBarChart(wrap.querySelector("#tr04-disposition-chart"), labels, values, "Signals");
  }

  function shell() {
    return `
      <section class="tr-panel" id="tr04-p01"><h2>Source filters</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr04-p02"><h2>Stream status</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr04-p03"><h2>Disposition table</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr04-p04"><h2>Backlog</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr04-p05"><h2>Signal analytics</h2><div class="tr-panel-body"></div></section>
    `;
  }

  function applyFilters(signals, filters) {
    return signals.filter((s) => {
      if (filters.source && s.source !== filters.source) return false;
      if (filters.analyst && (s.analyst || "") !== filters.analyst) return false;
      return true;
    });
  }

  async function load(ctx, filters) {
    const els = {
      filters: ctx.container.querySelector("#tr04-p01 .tr-panel-body"),
      status: ctx.container.querySelector("#tr04-p02 .tr-panel-body"),
      disposition: ctx.container.querySelector("#tr04-p03 .tr-panel-body"),
      backlog: ctx.container.querySelector("#tr04-p04 .tr-panel-body"),
      analytics: ctx.container.querySelector("#tr04-p05 .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const health = await ctx.fetchJSON("/health");
    if (health.status === 401 || health.status === 403) {
      StateMatrix.render(els.status, { state: "denied", deniedCode: health.status });
    } else if (!health.ok) {
      StateMatrix.render(els.status, { state: "error", message: "Could not check stream health." });
    } else {
      const h = health.data || {};
      const checklist = [
        ["Signal store reachable", boolPill(h.database_ok)],
        ["Ingestion-adjacent workers (reconciler/price monitor) making progress", boolPill(h.price_monitor_ok && h.reconciler_ok)],
        ["Per-source connection status", capSlot("tr04-cap-conn-status")],
        ["Trading authority (per source)", capSlot("tr04-cap-trading-authority")],
      ];
      StateMatrix.render(els.status, {
        state: "ready",
        html: `<p class="section-note">Independently evaluated conditions -- not collapsed into one badge.</p>${table(
          ["Condition", "Status"],
          checklist,
          "No conditions."
        )}`,
      });
      mountCapStates(els.status, [
        [
          "tr04-cap-conn-status",
          {
            status: "not_tracked",
            reason: "Per-source connection status is not exposed by any endpoint in this build.",
            remediation: "See TR-09 Signal providers and collectors (a later batch) for that surface.",
          },
        ],
        [
          "tr04-cap-trading-authority",
          {
            status: "not_tracked",
            reason: "Trading authority (per source) is not exposed by any endpoint in this build.",
            remediation: "See TR-09 Signal providers and collectors (a later batch) for that surface.",
          },
        ],
      ]);
    }

    const signalsRes = await ctx.fetchJSON("/signals?limit=100");
    if (signalsRes.status === 401 || signalsRes.status === 403) {
      StateMatrix.render(els.filters, { state: "denied", deniedCode: signalsRes.status });
      StateMatrix.render(els.disposition, { state: "denied", deniedCode: signalsRes.status });
      StateMatrix.render(els.backlog, { state: "denied", deniedCode: signalsRes.status });
      StateMatrix.render(els.analytics, { state: "denied", deniedCode: signalsRes.status });
      return;
    }
    if (!signalsRes.ok) {
      StateMatrix.render(els.filters, { state: "error", message: "Could not load signals." });
      StateMatrix.render(els.disposition, { state: "error", message: "Could not load signals." });
      StateMatrix.render(els.backlog, { state: "error", message: "Could not load signals." });
      StateMatrix.render(els.analytics, { state: "error", message: "Could not load signals." });
      return;
    }

    const allSignals = (signalsRes.data && signalsRes.data.signals) || [];
    const sources = [...new Set(allSignals.map((s) => s.source))];
    StateMatrix.render(els.filters, {
      state: "ready",
      html: `
        <form id="tr04-filter-form" class="inline-form" style="margin:0;">
          <label>Source
            <select name="source">
              <option value="">(any)</option>
              ${sources.map((s) => `<option value="${escapeAttr(s)}" ${filters.source === s ? "selected" : ""}>${escapeHtml(s)}</option>`).join("")}
            </select>
          </label>
          <div class="actions"><button type="submit">Apply</button> <button type="button" class="ghost" id="tr04-clear-filters">Clear</button></div>
        </form>
        <p class="section-note">Filters: Account, Analyst, Instrument, Product, Protection scoping is server-side in the spec; this batch filters client-side over the fetched page (Source shown; Analyst/Disposition/Period are not yet independently queryable server-side in this build).</p>
      `,
    });
    const form = els.filters.querySelector("#tr04-filter-form");
    if (form) {
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        const f = new FormData(form);
        load(ctx, { source: f.get("source") || "" });
      });
      els.filters.querySelector("#tr04-clear-filters").addEventListener("click", () => load(ctx, {}));
    }

    const signals = applyFilters(allSignals, filters || {});
    if (!signals.length) {
      StateMatrix.render(els.disposition, {
        state: "empty",
        emptyMessage: "No authorized signals have been received.",
        nextRoute: "/trade/signals",
        nextLabel: "Signal providers and collectors (TR-09, not yet available)",
      });
    } else {
      const rows = signals.map((s) => [
        `<a class="mono" href="#/trade/signals/${encodeURIComponent(s.id)}">${escapeHtml(s.id)}</a>`,
        `<span class="mono">${escapeHtml(s.source)}</span>`,
        escapeHtml(s.analyst || "(none)"),
        s.received_at || "—",
        escapeHtml(s.side || "—"),
        `<span class="mono">${escapeHtml(s.symbol)}</span>`,
        pill("accepted (recorded)", "ok"),
        capSlot(`tr04-cap-disposition-${escapeAttr(s.id)}`),
      ]);
      StateMatrix.render(els.disposition, {
        state: "ready",
        html: table(
          ["Event/revision", "Provider", "Analyst", "Observed", "Interpreted action", "Instrument", "Disposition", "Reason"],
          rows,
          "No authorized signals have been received."
        ),
      });
      mountCapStates(
        els.disposition,
        signals.map((s) => [
          `tr04-cap-disposition-${escapeAttr(s.id)}`,
          {
            status: "not_tracked",
            reason: "Rejected/ignored instructions are not persisted in this build.",
          },
        ])
      );
    }

    els.backlog.removeAttribute("aria-busy");
    Components.renderCapabilityState(els.backlog, {
      status: "unsupported",
      reason: "No ingestion queue-depth/backlog metric is exposed by any endpoint in this build -- signals are processed synchronously per request (see app/engine.py), so there is no queue to report depth for.",
    });

    // --- Signal analytics charts (real, over the same filtered `signals`
    // the disposition table above renders) ---
    if (!signals.length) {
      StateMatrix.render(els.analytics, {
        state: "empty",
        emptyMessage: "No authorized signals have been received -- nothing real to chart yet.",
      });
    } else {
      const ordersRes = await ctx.fetchJSON("/orders?limit=500");
      const orders =
        ordersRes.ok && ordersRes.data && Array.isArray(ordersRes.data.orders) ? ordersRes.data.orders : [];

      StateMatrix.render(els.analytics, {
        state: "ready",
        html: `
          <p class="section-note" style="margin-top:0;">All charts below are computed client-side from the exact same (filtered) signals list the Disposition table above renders -- see this module's own header comment for what is charted and what is deliberately left out.</p>
          <div class="tr-chart-grid" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:16px;">
            <div><h3 class="section-note">Signal volume over time</h3><div id="tr04-volume-wrap"></div></div>
            <div><h3 class="section-note">Signals by source</h3><div id="tr04-source-wrap"></div></div>
            <div><h3 class="section-note">Signals by side</h3><div id="tr04-side-wrap"></div></div>
            <div><h3 class="section-note">Signals by asset class</h3><div id="tr04-assetclass-wrap"></div></div>
            <div style="grid-column:1 / -1;"><h3 class="section-note">Disposition breakdown (real order-status join)</h3><div id="tr04-disposition-wrap"></div></div>
          </div>
        `,
      });

      renderVolumeChart(els.analytics.querySelector("#tr04-volume-wrap"), signals);
      renderBreakdownChart(
        els.analytics.querySelector("#tr04-source-wrap"),
        "source",
        "tr04-source-chart",
        signals,
        (s) => s.source,
        "No signals to break down by source yet.",
        "Signals"
      );
      renderBreakdownChart(
        els.analytics.querySelector("#tr04-side-wrap"),
        "side",
        "tr04-side-chart",
        signals,
        (s) => s.side || "(none)",
        "No signals to break down by side yet.",
        "Signals"
      );
      renderBreakdownChart(
        els.analytics.querySelector("#tr04-assetclass-wrap"),
        "assetClass",
        "tr04-assetclass-chart",
        signals,
        (s) => s.asset_class || "(none)",
        "No signals to break down by asset class yet.",
        "Signals"
      );
      renderDispositionChart(els.analytics.querySelector("#tr04-disposition-wrap"), signals, orders);
    }

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr04 = {
    title: "Incoming signal stream",
    breadcrumb: "Trade / Signals",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = shell();
      await load(ctx, {});
      ctx.registerPoll("tr04", 10000, () => load(ctx, {}));
    },
  };
  Router.register("/trade/signals", "tr04");
})();
