/* TR-04: Incoming signal stream (`#/trade/signals`).
 *
 * Real backing data: GET /signals (every accepted, persisted Signal --
 * `app/db.py`'s `list_recent_signals`, extended in an earlier batch to
 * also project the already-stored `analyst` column, and `stop_loss`/
 * `take_profit`) joined client-side against GET /orders (`list_recent_orders`
 * -- account_id, symbol, status, message, purpose, family_id, all real,
 * already-persisted columns). No numeric headline metric per spec ("Do not
 * add a decorative performance KPI").
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
 * Every row below is therefore an accepted, persisted signal; its
 * Disposition cell reports the real furthest pipeline stage that
 * signal's own linked order(s) reached (see FUNNEL_STAGES below), with
 * an explicit note that pre-persistence rejected/ignored instructions
 * are not represented at all in this build.
 *
 * --- Operational inbox (this batch) ---
 * The disposition table is now a genuine per-signal operational inbox:
 * every row shows received time, provider, analyst, raw/normalized
 * instrument, side, entry instruction, stop, target, parser status, age,
 * disposition, destinations, order result and rejection reason -- see
 * `buildInboxRow` below for exactly which real column backs each cell,
 * and which one honestly renders `Components.renderCapabilityState`
 * instead of a fabricated value.
 *
 * --- Signal funnel (this batch) ---
 * A REAL signal funnel, per `computeFunnelForSignal` below: this
 * schema's actual, distinctly-observable transitions a signal's own
 * linked order rows go through, not the review's idealized 10-stage
 * list verbatim (several of those stages collapse in this codebase --
 * see the stage-by-stage rationale on FUNNEL_STAGES). Broken down by
 * provider (source) and by analyst, both real grouping keys already on
 * every signal row. Chart.js grouped bars, same one-persistent-instance
 * destroy-and-recreate idiom as dashboard.html's "economics-chart" (C12)
 * and TR-14/TR-15 (app/static/views/tr14.js, tr15.js).
 *
 * --- Remaining signal analytics charts (from an earlier batch, PU-B3) ---
 * Three additive Chart.js charts, computed client-side from data this
 * screen already fetches (GET /signals) -- same destroy-and-recreate
 * pattern:
 *   1. Signal volume over time -- real `received_at` timestamps bucketed
 *      by hour if the fetched page's own timestamp range spans <=48h, by
 *      day otherwise (never padded: a bucket only appears if a real
 *      signal landed in it).
 *   2. Signals by side, 3. by asset class -- real counts over the exact
 *      same (filtered) signals list the table above renders.
 * "Signals by source" and the old standalone "Disposition breakdown"
 * chart from that batch are superseded by the funnel's by-provider
 * breakdown and the inbox's own Disposition column respectively, so they
 * are not duplicated here.
 *
 * Deliberately NOT built:
 *   - A signal-arrival heatmap by hour/day: this build's real seed/demo
 *     data is far too sparse (often a handful of signals in one test run)
 *     for an hour x day-of-week grid to be anything but mostly-empty
 *     cells -- that is itself an honest result, but not a meaningful
 *     chart, so it is left out rather than padded to look fuller.
 *   - Provider latency distribution: that is Phase A2/B9's job (see
 *     app/execution_quality.py, rendered on TR-14) -- duplicating it here
 *     would fork that work.
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
    side: null,
    assetClass: null,
    funnelOverall: null,
    funnelByProvider: null,
    funnelByAnalyst: null,
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

  // ---------------------------------------------------------------------
  // Signal funnel (real, per-signal stage reached).
  //
  // This codebase's actual sequence of distinctly-observable states a
  // signal's processing goes through (verified against app/engine.py's
  // real control flow, not the review brief's 10-stage list verbatim --
  // several of those stages collapse here because nothing this codebase
  // persists can tell them apart):
  //
  //   - "Received" / "parsed" COLLAPSE: app/engine.py's own module
  //     docstring and app/sources/*.py's parsers build a `Signal` (which
  //     stamps `received_at`) as the very last step of parsing, in the
  //     same call frame -- a signal that fails to parse is rejected with
  //     an HTTP 4xx and never becomes a `signals` row at all (see this
  //     file's own module docstring), so every real signal row already
  //     represents "received AND parsed" with no persisted gap between
  //     the two. There is no separate "parsed" moment to report.
  //   - "Instrument resolved" does not become a separate observable
  //     stage: `symbol_for_account` (app/risk.py) is a deterministic,
  //     always-succeeding remap with no rejection path in app/engine.py
  //     -- there is no real "instrument could not be resolved" state
  //     this schema ever records, so this step is folded into "Routed"
  //     below rather than invented as its own bar.
  //   - "Routed": routing.destinations_for(signal.source, signal.symbol)
  //     found at least one destination account -- observable as "this
  //     signal has >=1 real row in `orders`" (GET /orders' signal_id),
  //     since app/engine.py only ever calls save_order_result once
  //     destinations exist. A signal with zero destinations has zero
  //     order rows and never advances past "Received".
  //   - "Policy valid" and "risk admitted" COLLAPSE into the same
  //     observable boundary as "Submitted": app/engine.py runs the
  //     broker/asset-class check, the stop/target-bracket-support check,
  //     the capital-admission check and (for managed_lifecycle accounts)
  //     `validate_plan` all BEFORE ever calling `broker.place_order` --
  //     none of those individual gates gets its own persisted
  //     status/timestamp in `orders`, only the final order `status`
  //     (rejected/error/pending/filled). A REJECTED order with a real
  //     `message` (shown in the inbox's Rejection reason column) may
  //     have failed any one of those gates; this schema cannot
  //     distinguish which without a persisted per-gate outcome, so they
  //     honestly collapse into one "did this signal's order reach the
  //     broker at all" boundary rather than three invented bars with the
  //     same underlying (real) rejected/not-rejected signal.
  //   - "Submitted": >=1 linked order has status 'pending' or 'filled'
  //     (i.e. `broker.place_order` was actually called and returned a
  //     non-rejected result). A 'error' status is NOT counted as
  //     reaching this stage -- ERROR can happen either before the broker
  //     call (no broker adapter registered) or during it (an exception
  //     while calling place_order), and `orders` has no column that
  //     distinguishes the two (see app/execution_quality.py's own stage
  //     4 discussion of this exact ambiguity for `submitted_at`, which
  //     GET /orders does not project at all) -- treating every ERROR as
  //     "reached the broker" would overstate this stage on an ambiguous
  //     signal, so it conservatively does not count.
  //   - "Filled": >=1 linked order has status 'filled'.
  //   - "Protected": NOT built. `orders.protection_confirmed_at` is a
  //     real column (app/db.py's SCHEMA, PU-A2) but `GET /orders`
  //     (list_recent_orders) does not project it, and this batch's scope
  //     is this file only -- there is no real data reaching this screen
  //     to honestly compute a Protected stage from. The funnel stops
  //     before it rather than fabricating a count; see the by-provider/
  //     by-analyst chart's own caption for this exact disclosure.
  //   - "Exited": only observable for a managed_lifecycle destination --
  //     a CLOSE signal's own linked order (real, via `orders.signal_id`)
  //     carries a real `family_id` equal to the ENTRY signal's id when
  //     that account is managed_lifecycle (see app/engine.py's DB-0X
  //     comments); a plain (non-managed_lifecycle) account's close
  //     leaves `family_id` NULL, so an entry closed on a plain account
  //     cannot be traced to "Exited" through this real field at all --
  //     it honestly stops at "Filled" for that signal instead.
  const FUNNEL_STAGES = ["Received", "Routed", "Submitted", "Filled", "Exited"];

  function indexOrders(orders) {
    const bySignalId = new Map();
    const byFamilyId = new Map();
    for (const o of orders) {
      if (!bySignalId.has(o.signal_id)) bySignalId.set(o.signal_id, []);
      bySignalId.get(o.signal_id).push(o);
      if (o.family_id) {
        if (!byFamilyId.has(o.family_id)) byFamilyId.set(o.family_id, []);
        byFamilyId.get(o.family_id).push(o);
      }
    }
    return { bySignalId, byFamilyId };
  }

  // Returns the real furthest FUNNEL_STAGES index this one signal's own
  // linked orders reached -- see FUNNEL_STAGES' own comment above for
  // exactly what each index means and why the boundaries fall where they
  // do.
  function stageIndexForSignal(signal, ordersForSignal, byFamilyId) {
    if (!ordersForSignal.length) return 0; // Received only -- no destination found.
    const reachedBroker = ordersForSignal.some((o) => o.status === "pending" || o.status === "filled");
    if (!reachedBroker) return 1; // Routed, but every linked order was rejected/errored before/at the broker call.
    const filled = ordersForSignal.some((o) => o.status === "filled");
    if (!filled) return 2; // Submitted, still pending (or an ambiguous error) -- not yet a confirmed fill.
    if (signal.side === "close") return 3; // A close signal's own fill IS an exit of something else, not itself something to "exit" again.
    const linkedCloses = byFamilyId.get(signal.id) || [];
    const exited = linkedCloses.some((o) => o.purpose === "close" && o.status === "filled");
    return exited ? 4 : 3;
  }

  function computePerSignalStages(signals, orders) {
    const { bySignalId, byFamilyId } = indexOrders(orders);
    return signals.map((s) => ({
      signal: s,
      ordersForSignal: bySignalId.get(s.id) || [],
      stageIndex: stageIndexForSignal(s, bySignalId.get(s.id) || [], byFamilyId),
    }));
  }

  // Cumulative real counts: "reached stage i" always implies "reached
  // every stage before it" by construction of stageIndexForSignal above,
  // so this renders as a real, honestly non-increasing funnel shape --
  // never independently-counted bars that could show a later stage with
  // MORE signals than an earlier one.
  function stageCounts(perSignalStages) {
    return FUNNEL_STAGES.map((_, i) => perSignalStages.filter((p) => p.stageIndex >= i).length);
  }

  function renderGroupedFunnelChart(canvas, groups) {
    // `groups`: [{ label, perSignalStages }] -- one dataset per group
    // (provider or analyst), each real counts over FUNNEL_STAGES.
    return new Chart(canvas.getContext("2d"), {
      type: "bar",
      data: {
        labels: FUNNEL_STAGES,
        datasets: groups.map((g, i) => ({
          label: g.label,
          data: stageCounts(g.perSignalStages),
          backgroundColor: CHART_PALETTE[i % CHART_PALETTE.length],
          maxBarThickness: 40,
        })),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: true } },
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
  }

  function renderFunnelSection(wrap, perSignalStages) {
    destroyChart("funnelOverall");
    destroyChart("funnelByProvider");
    destroyChart("funnelByAnalyst");
    if (!perSignalStages.length) {
      wrap.innerHTML = `<div class="empty">No signals to build a funnel from yet.</div>`;
      return;
    }
    wrap.innerHTML = `
      <p class="section-note" style="margin-top:0;">Real, cumulative per-signal stage counts (a signal counted at a stage always reached every stage before it) -- see this module's own FUNNEL_STAGES comment for exactly which real app/engine.py transition backs each bar, and where this codebase's real observable chain currently ends ("Protected" is a real column this batch's endpoint doesn't project; "Exited" is only traceable for managed_lifecycle destinations).</p>
      <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); gap:16px;">
        <div style="grid-column:1 / -1;"><h3 class="section-note">Overall</h3><div class="chart-container"><canvas id="tr04-funnel-overall"></canvas></div></div>
        <div><h3 class="section-note">By provider (source)</h3><div class="chart-container"><canvas id="tr04-funnel-provider"></canvas></div></div>
        <div><h3 class="section-note">By analyst</h3><div class="chart-container"><canvas id="tr04-funnel-analyst"></canvas></div></div>
      </div>
    `;
    charts.funnelOverall = renderBarChart(
      wrap.querySelector("#tr04-funnel-overall"),
      FUNNEL_STAGES,
      stageCounts(perSignalStages),
      "Signals"
    );

    const byProvider = [...countBy(perSignalStages, (p) => p.signal.source).keys()].map((source) => ({
      label: source,
      perSignalStages: perSignalStages.filter((p) => p.signal.source === source),
    }));
    charts.funnelByProvider = renderGroupedFunnelChart(wrap.querySelector("#tr04-funnel-provider"), byProvider);

    const byAnalyst = [...countBy(perSignalStages, (p) => p.signal.analyst || "(none)").keys()].map((analyst) => ({
      label: analyst,
      perSignalStages: perSignalStages.filter((p) => (p.signal.analyst || "(none)") === analyst),
    }));
    charts.funnelByAnalyst = renderGroupedFunnelChart(wrap.querySelector("#tr04-funnel-analyst"), byAnalyst);
  }

  // ---------------------------------------------------------------------
  // Operational inbox row builders.

  function formatAge(receivedAt) {
    if (!receivedAt) return "—";
    const t = Date.parse(receivedAt);
    if (Number.isNaN(t)) return "—";
    const ms = Date.now() - t;
    if (ms < 0) return "just now";
    const mins = Math.floor(ms / 60000);
    if (mins < 1) return "<1m";
    if (mins < 60) return `${mins}m`;
    const hours = Math.floor(mins / 60);
    if (hours < 48) return `${hours}h ${mins % 60}m`;
    const days = Math.floor(hours / 24);
    return `${days}d ${hours % 24}h`;
  }

  function entryInstructionCell(s) {
    if (s.side === "close") return "Flatten existing position (CLOSE)";
    const qty = s.quantity != null ? fmtNum(s.quantity) : "—";
    const px = s.price != null ? `@ ${fmtNum(s.price)}` : "@ market";
    return `${qty} ${px}`;
  }

  function instrumentCell(s, ordersForSignal) {
    const rawSym = `<span class="mono">${escapeHtml(s.symbol)}</span>`;
    const mapped = [...new Set(ordersForSignal.map((o) => o.symbol).filter(Boolean))];
    if (!mapped.length) return rawSym;
    if (mapped.length === 1 && mapped[0] === s.symbol) return rawSym;
    return `${rawSym} → <span class="mono">${mapped.map(escapeHtml).join(", ")}</span>`;
  }

  const STATUS_PILL_KIND = { filled: "ok", pending: "warn", rejected: "bad", error: "bad" };

  function dispositionCell(stageIndex, ordersForSignal) {
    if (!ordersForSignal.length) return pill("no destination configured", "muted");
    if (stageIndex === 1) {
      const anyRejected = ordersForSignal.some((o) => o.status === "rejected");
      return pill(anyRejected ? "routed, rejected before submission" : "routed, errored before/without reaching broker", "bad");
    }
    if (stageIndex === 2) return pill("submitted, awaiting fill", "warn");
    if (stageIndex === 3) return pill("filled", "ok");
    return pill("filled and exited", "ok");
  }

  function destinationsCell(ordersForSignal) {
    if (!ordersForSignal.length) return pill("no destination configured", "muted");
    return [...new Set(ordersForSignal.map((o) => o.account_id))]
      .map((id) => `<span class="mono">${escapeHtml(id)}</span>`)
      .join(", ");
  }

  function orderResultCell(ordersForSignal) {
    if (!ordersForSignal.length) return pill("no order yet", "muted");
    return ordersForSignal
      .map((o) => `<span class="mono">${escapeHtml(o.account_id)}</span> ${pill(o.status, STATUS_PILL_KIND[o.status] || "muted")}`)
      .join("<br>");
  }

  function interpretSignal(s) {
    // WP-44: Plain-language signal interpretation for TR-04.
    // Returns a human-readable description of what the signal means.
    if (!s.intent) return "—";

    // Helper to format contract specs from raw["contract_spec"]
    const formatContractSpec = () => {
      const spec = s.raw?.contract_spec;
      if (!spec) return "";

      if (spec.option) {
        const opt = spec.option;
        const right = opt.right?.toUpperCase() === "CALL" ? "C" : "P";
        return `Option: ${s.symbol} ${opt.strike}${right} ${opt.expiry} ×${opt.multiplier}`;
      }
      if (spec.future) {
        const fut = spec.future;
        return `Future: ${s.symbol} ${fut.root} ${fut.expiry} ×${fut.multiplier}`;
      }
      if (spec.fx) {
        const fx = spec.fx;
        return `FX: ${fx.base_currency}/${fx.quote_currency} (${fx.unit})`;
      }
      if (spec.crypto_derivative) {
        const cd = spec.crypto_derivative;
        return `Crypto derivative: ${s.symbol} ${cd.instrument_kind}`;
      }
      return "";
    };

    const contractSpec = formatContractSpec();
    const qty = s.quantity ? `${s.quantity}` : "";

    switch (s.intent) {
      case "ENTRY_LONG":
        return `Enter long${qty ? " " + qty : ""} ${s.symbol}${contractSpec ? " (" + contractSpec + ")" : ""}`;
      case "ENTRY_SHORT":
        return `Enter short${qty ? " " + qty : ""} ${s.symbol}${contractSpec ? " (" + contractSpec + ")" : ""}`;
      case "SELL":
        return `Sell${qty ? " " + qty : ""} ${s.symbol}${contractSpec ? " (" + contractSpec + ")" : ""}`;
      case "EXIT":
        return `Exit${qty ? " " + qty : ""} ${s.symbol}${contractSpec ? " (" + contractSpec + ")" : ""}`;
      case "REDUCE":
        const fraction = s.reduce_fraction ? Math.round(s.reduce_fraction * 100) : "";
        return `Exit ${fraction}% ${s.symbol}${contractSpec ? " (" + contractSpec + ")" : ""}`;
      case "STOP_UPDATE":
        return `Update stop → ${s.stop_loss || "?"} on ${s.symbol}${contractSpec ? " (" + contractSpec + ")" : ""}`;
      case "TARGET_UPDATE":
        return `Update targets on ${s.symbol}${contractSpec ? " (" + contractSpec + ")" : ""}`;
      case "CANCEL":
        return `Cancel order on ${s.symbol}${contractSpec ? " (" + contractSpec + ")" : ""}`;
      case "ADD":
        return `Add to position on ${s.symbol}${contractSpec ? " (" + contractSpec + ")" : ""}`;
      default:
        return s.intent;
    }
  }

  function fixRouteForReason(message) {
    // WP-44: Route rejection messages to appropriate fix screens.
    // Returns the URL path or null if no match.
    if (!message) return null;
    const lowerMessage = message.toLowerCase();

    // TR-08 (Account Editor UI): sizing/quantity/allow_short/leverage/loss-limit/buying power/notional/margin issues
    if (/sizing|quantity|allow_short|leverage|loss.?limit|buying.?power|notional|margin/i.test(lowerMessage)) {
      return "#/accounts";
    }

    // TR-11 (Routing): routing/destination issues
    if (/no.?destination|routing/i.test(lowerMessage)) {
      return "#/trade/routing";
    }

    // TR-20 (Operations): halt/unresolved/alert issues
    if (/halt|unresolved|alert/i.test(lowerMessage)) {
      return "#/operations";
    }

    return null;
  }

  function rejectionReasonCell(ordersForSignal) {
    const reasons = ordersForSignal.filter((o) => (o.status === "rejected" || o.status === "error") && o.message);
    if (!reasons.length) return "—";
    return reasons.map((o) => {
      const fixRoute = fixRouteForReason(o.message);
      const fixLink = fixRoute ? ` <a href="${fixRoute}" class="inline-link">Fix</a>` : "";
      return `<span class="mono">${escapeHtml(o.account_id)}</span>: ${escapeHtml(o.message)}${fixLink}`;
    }).join("<br>");
  }

  function buildInboxRow(entry) {
    const { signal: s, ordersForSignal, stageIndex } = entry;
    return [
      `<a class="mono" href="#/trade/signals/${encodeURIComponent(s.id)}">${escapeHtml(s.received_at || "—")}</a>`,
      `<span class="mono">${escapeHtml(s.source)}</span>`,
      escapeHtml(s.analyst || "(none)"),
      instrumentCell(s, ordersForSignal),
      escapeHtml(s.side || "—"),
      entryInstructionCell(s),
      s.stop_loss != null ? fmtNum(s.stop_loss) : "—",
      s.take_profit != null ? fmtNum(s.take_profit) : "—",
      capSlot(`tr04-cap-parser-${escapeAttr(s.id)}`),
      formatAge(s.received_at),
      dispositionCell(stageIndex, ordersForSignal),
      destinationsCell(ordersForSignal),
      orderResultCell(ordersForSignal),
      interpretSignal(s),
      rejectionReasonCell(ordersForSignal),
    ];
  }

  function shell() {
    return `
      <section class="tr-panel" id="tr04-p01"><h2>Source filters</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr04-p02"><h2>Stream status</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr04-p03"><h2>Operational inbox</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr04-p04"><h2>Backlog</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr04-p05"><h2>Signal funnel</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr04-p06"><h2>Signal analytics</h2><div class="tr-panel-body"></div></section>
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
      funnel: ctx.container.querySelector("#tr04-p05 .tr-panel-body"),
      analytics: ctx.container.querySelector("#tr04-p06 .tr-panel-body"),
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
      StateMatrix.render(els.funnel, { state: "denied", deniedCode: signalsRes.status });
      StateMatrix.render(els.analytics, { state: "denied", deniedCode: signalsRes.status });
      return;
    }
    if (!signalsRes.ok) {
      StateMatrix.render(els.filters, { state: "error", message: "Could not load signals." });
      StateMatrix.render(els.disposition, { state: "error", message: "Could not load signals." });
      StateMatrix.render(els.backlog, { state: "error", message: "Could not load signals." });
      StateMatrix.render(els.funnel, { state: "error", message: "Could not load signals." });
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

    // GET /orders is the real, already-persisted join key for the inbox's
    // destinations/order-result/rejection-reason/disposition columns AND
    // the signal funnel below -- fetched once here (not just inside the
    // old analytics-only branch) since the inbox table now needs it too.
    const ordersRes = await ctx.fetchJSON("/orders?limit=500");
    const orders = ordersRes.ok && ordersRes.data && Array.isArray(ordersRes.data.orders) ? ordersRes.data.orders : [];
    const perSignalStages = computePerSignalStages(signals, orders);

    if (!signals.length) {
      StateMatrix.render(els.disposition, {
        state: "empty",
        emptyMessage: "No authorized signals have been received.",
        nextRoute: "/trade/signals",
        nextLabel: "Signal providers and collectors (TR-09, not yet available)",
      });
    } else {
      const rows = perSignalStages.map(buildInboxRow);
      StateMatrix.render(els.disposition, {
        state: "ready",
        html: `
          <p class="section-note" style="margin-top:0;">Every row is a real, persisted signal -- see this module's docstring for what an authorized-but-rejected-at-parse-time instruction looks like (never persisted, so never a row here). Click an event id below for the full evidence/plan-preview detail (TR-05).</p>
          ${table(
            [
              "Received",
              "Provider",
              "Analyst",
              "Raw / normalized instrument",
              "Side",
              "Entry instruction",
              "Stop",
              "Target",
              "Parser status",
              "Age",
              "Disposition",
              "Destinations",
              "Order result",
              "Interpreted as",
              "Rejection reason",
            ],
            rows,
            "No authorized signals have been received."
          )}
        `,
      });
      mountCapStates(
        els.disposition,
        signals.map((s) => [
          `tr04-cap-parser-${escapeAttr(s.id)}`,
          {
            status: "not_tracked",
            reason: "parser is deterministic; no confidence score is computed",
          },
        ])
      );
    }

    els.backlog.removeAttribute("aria-busy");
    Components.renderCapabilityState(els.backlog, {
      status: "unsupported",
      reason: "No ingestion queue-depth/backlog metric is exposed by any endpoint in this build -- signals are processed synchronously per request (see app/engine.py), so there is no queue to report depth for.",
    });

    if (!signals.length) {
      StateMatrix.render(els.funnel, {
        state: "empty",
        emptyMessage: "No authorized signals have been received -- nothing real to build a funnel from yet.",
      });
    } else {
      StateMatrix.render(els.funnel, { state: "ready", html: `<div id="tr04-funnel-wrap"></div>` });
      renderFunnelSection(els.funnel.querySelector("#tr04-funnel-wrap"), perSignalStages);
    }

    // --- Remaining signal analytics charts (real, over the same filtered
    // `signals` the inbox above renders) ---
    if (!signals.length) {
      StateMatrix.render(els.analytics, {
        state: "empty",
        emptyMessage: "No authorized signals have been received -- nothing real to chart yet.",
      });
    } else {
      StateMatrix.render(els.analytics, {
        state: "ready",
        html: `
          <p class="section-note" style="margin-top:0;">Computed client-side from the exact same (filtered) signals list the inbox above renders -- see this module's own header comment for what is charted and what is deliberately left out (signals-by-source and the old order-status disposition chart are superseded by the funnel above).</p>
          <div class="tr-chart-grid" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:16px;">
            <div style="grid-column:1 / -1;"><h3 class="section-note">Signal volume over time</h3><div id="tr04-volume-wrap"></div></div>
            <div><h3 class="section-note">Signals by side</h3><div id="tr04-side-wrap"></div></div>
            <div><h3 class="section-note">Signals by asset class</h3><div id="tr04-assetclass-wrap"></div></div>
          </div>
        `,
      });

      renderVolumeChart(els.analytics.querySelector("#tr04-volume-wrap"), signals);
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
