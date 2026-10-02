/* TR-05: Signal evidence and plan preview (`#/trade/signals/:event_id`).
 *
 * Real backing data: GET /signals (client-side find by id, same pattern
 * TR-03 uses over GET /positions -- there is no GET /signals/{id} route
 * in this build) -- extended in this batch (see app/db.py's
 * `list_recent_signals`) to also project `stop_loss`, `take_profit` and
 * `raw`, which were always persisted per-signal but never previously
 * selected out. GET /orders (client-side filtered to signal_id === the
 * selected event_id) for the Execution links panel. GET /routing-rules,
 * GET /accounts and GET /brokers, cross-referenced against the signal's
 * own source/symbol/asset_class, for the Routing preview checklist and
 * Instrument resolution panel -- every condition there is computed from
 * already-exposed config, not invented.
 *
 * Honest gaps, disclosed rather than worked around:
 *   - "Original/revisions": this schema has NO revision history for a
 *     signal (`signals` is INSERT OR REPLACE by id, one row, no version
 *     log) -- the panel shows the one stored version and says so.
 *   - "Parsed fields/evidence" columns per spec are Field, Source span,
 *     Parser version, Normalized value, Validation, Fallback provenance.
 *     This build tracks none of Source span/Parser version/Validation
 *     status/Fallback provenance per field -- those cells read "not
 *     tracked in this build" rather than a fabricated value; only Field
 *     and Normalized value are real.
 *   - TR-05-A01 "Reclassify in sandbox" is only offered when this signal
 *     actually came through the free-text parser (app/sources/text_parser.py
 *     stores `raw: {"text": ...}`) -- POST /sources/{source}/classify-messages
 *     is a real, already-existing, non-persisting dry-run of that exact
 *     grammar (see its own docstring), but structured-payload sources
 *     (webhook/NinjaTrader/MT4-MT5/Rithmic) have no free text to reclassify
 *     against it, so the action is unsupported for those signals rather
 *     than faked. Its result is shown, not claimed as "persisted" -- the
 *     endpoint itself never touches storage.
 *   - TR-05-A02 "Compare parser versions" has no backing capability: this
 *     build's parser has no versioned registry to compare against --
 *     unsupported, not faked.
 */
(function () {
  "use strict";

  // See tr04.js for why this placeholder-slot pattern exists: capability-
  // state badges render into a real DOM element, but most panels here are
  // built as one HTML string before insertion.
  function capSlot(id) {
    return `<span class="cap-state-slot" id="${id}"></span>`;
  }
  function mountCapStates(root, specs) {
    for (const [id, opts] of specs) {
      const el = root.querySelector(`#${id}`);
      if (el) Components.renderCapabilityState(el, opts);
    }
  }

  function shell() {
    return `
      <section class="tr-panel" id="tr05-p01"><h2>Original/revisions</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr05-p02"><h2>Parsed fields/evidence</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr05-p03"><h2>Instrument resolution</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr05-p04"><h2>Risk/stop/horizon plan</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr05-p05"><h2>Routing preview</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr05-p06"><h2>Execution links</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr05-p07"><h2>Actions</h2><div class="tr-panel-body"></div></section>
    `;
  }

  async function load(ctx) {
    // Handle both :event_id (evidence view) and :id (decision traces view)
    const eventId = ctx.params.event_id || ctx.params.id;
    const isDecisionView = !!ctx.params.id && !ctx.params.event_id;

    const els = {
      original: ctx.container.querySelector("#tr05-p01 .tr-panel-body"),
      parsed: ctx.container.querySelector("#tr05-p02 .tr-panel-body"),
      instrument: ctx.container.querySelector("#tr05-p03 .tr-panel-body"),
      plan: ctx.container.querySelector("#tr05-p04 .tr-panel-body"),
      routing: ctx.container.querySelector("#tr05-p05 .tr-panel-body"),
      execution: ctx.container.querySelector("#tr05-p06 .tr-panel-body"),
      actions: ctx.container.querySelector("#tr05-p07 .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const signalsRes = await ctx.fetchJSON("/signals?limit=500");
    if (signalsRes.status === 401 || signalsRes.status === 403) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "denied", deniedCode: signalsRes.status });
      return;
    }
    if (!signalsRes.ok) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "error", message: "Could not load this signal." });
      return;
    }

    const signals = (signalsRes.data && signalsRes.data.signals) || [];
    const signal = signals.find((s) => s.id === eventId);
    if (!signal) {
      for (const el of Object.values(els)) {
        StateMatrix.render(el, {
          state: "empty",
          emptyMessage: "No retained source revision is available for this event.",
          nextRoute: "/trade/signals",
          nextLabel: "Incoming signal stream (TR-04)",
        });
      }
      return;
    }

    // --- For decision view, fetch decision data and render decision traces ---
    if (isDecisionView) {
      const decisionRes = await ctx.fetchJSON(`/signals/${encodeURIComponent(eventId)}/decision`);
      if (decisionRes.ok && decisionRes.data) {
        const decision = decisionRes.data;

        // Decision traces table
        const traceRows = (decision.traces || []).map(t => [
          t.candidate_rank.toString(),
          escapeHtml(t.physical_account_id),
          t.feasible ? "✓" : "✗",
          escapeHtml(t.reason),
          t.selected ? '<span class="badge" style="background:#4caf50;">selected</span>' : "—",
        ]);
        StateMatrix.render(els.original, {
          state: "ready",
          html: table(
            ["Rank", "Account", "Feasible", "Reason", "Selected"],
            traceRows,
            "No candidates evaluated for this signal."
          ),
        });

        // Reservation card
        const reservationHtml = decision.reservation
          ? `<div class="econ-stats">
               <div><span class="muted">State</span><br><span class="mono">${escapeHtml(decision.reservation.state)}</span></div>
               <div><span class="muted">Cash needed</span><br><span class="num">${fmtCents(decision.reservation.needed_cash_cents)}</span></div>
               <div><span class="muted">Margin needed</span><br><span class="num">${fmtCents(decision.reservation.needed_margin_cents)}</span></div>
               <div><span class="muted">Notional</span><br><span class="num">${fmtCents(decision.reservation.needed_notional_cents)}</span></div>
               <div><span class="muted">Planned risk</span><br><span class="num">${fmtCents(decision.reservation.needed_planned_risk_cents)}</span></div>
             </div>`
          : '<p class="section-note">No reservation recorded for this signal.</p>';
        StateMatrix.render(els.parsed, {
          state: "ready",
          html: reservationHtml,
        });

        // Intent/outbox card
        const intentHtml = decision.intent
          ? `<div class="econ-stats">
               <div><span class="muted">Intent ID</span><br><span class="mono" style="font-size:0.85em;">${escapeHtml(decision.intent.intent_id.substring(0, 8))}</span></div>
               ${decision.outbox ? `<div><span class="muted">Outbox state</span><br><span class="mono">${escapeHtml(decision.outbox.state)}</span></div>
               <div><span class="muted">Claimed by</span><br><span class="mono">${escapeHtml(decision.outbox.claimed_by || "(none)")}</span></div>
               <div><span class="muted">Response at</span><br><span class="mono" style="font-size:0.85em;">${escapeHtml(decision.outbox.response_recorded_at || "(none)")}</span></div>` : ''}
             </div>`
          : '<p class="section-note">No intent recorded for this signal.</p>';
        StateMatrix.render(els.instrument, {
          state: "ready",
          html: intentHtml,
        });

        // Protection card
        const protectionHtml = decision.protection
          ? `<div class="econ-stats">
               <div><span class="muted">Protection state</span><br><span class="mono">${escapeHtml(decision.protection.state)}</span></div>
               ${decision.protection.reason ? `<div><span class="muted">Reason</span><br><span>${escapeHtml(decision.protection.reason)}</span></div>` : ''}
             </div>`
          : '<p class="section-note">No protection tracked.</p>';
        StateMatrix.render(els.plan, {
          state: "ready",
          html: protectionHtml,
        });

        // Clear remaining panels
        StateMatrix.render(els.routing, { state: "empty", emptyMessage: "Not shown in decision view." });
        StateMatrix.render(els.execution, { state: "empty", emptyMessage: "Not shown in decision view." });
        StateMatrix.render(els.actions, { state: "empty", emptyMessage: "Not shown in decision view." });
      } else {
        for (const el of Object.values(els)) {
          StateMatrix.render(el, { state: "error", message: "Could not load decision data." });
        }
      }
      return;
    }

    // --- Original/revisions ---
    StateMatrix.render(els.original, {
      state: "ready",
      html: `<p class="section-note">This build stores one row per signal id (no revision log) -- shown below is the only retained version, exactly as received.</p>
             <pre class="mono" style="white-space:pre-wrap; overflow-x:auto; max-width:100%;">${escapeHtml(JSON.stringify(signal.raw || {}, null, 2))}</pre>`,
    });

    // --- Parsed fields/evidence ---
    const parsedRows = [
      ["source", escapeHtml(signal.source)],
      ["symbol", escapeHtml(signal.symbol)],
      ["side", escapeHtml(signal.side)],
      ["asset_class", escapeHtml(signal.asset_class)],
      ["quantity", signal.quantity === null || signal.quantity === undefined ? "—" : fmtNum(signal.quantity)],
      ["price", signal.price === null || signal.price === undefined ? "—" : fmtNum(signal.price)],
      ["stop_loss", signal.stop_loss === null || signal.stop_loss === undefined ? "—" : fmtNum(signal.stop_loss)],
      ["take_profit", signal.take_profit === null || signal.take_profit === undefined ? "—" : fmtNum(signal.take_profit)],
      ["analyst", escapeHtml(signal.analyst || "(none)")],
    ];
    StateMatrix.render(els.parsed, {
      state: "ready",
      html: `${capSlot("tr05-cap-parsed-fields")}${table(
        ["Field", "Normalized value"],
        parsedRows.map(([f, v]) => [`<span class="mono">${f}</span>`, v]),
        "No parsed fields."
      )}`,
    });
    mountCapStates(els.parsed, [
      [
        "tr05-cap-parsed-fields",
        {
          status: "not_tracked",
          reason:
            "Source span, parser version, per-field validation and fallback provenance are not tracked per field in this build -- those columns are omitted rather than fabricated; only the field and its normalized value are real.",
        },
      ],
    ]);

    // --- Risk/stop/horizon plan ---
    StateMatrix.render(els.plan, {
      state: "ready",
      html: `<div class="econ-stats">
               <div><span class="muted">Quantity</span><br><span class="num">${signal.quantity === null || signal.quantity === undefined ? "—" : fmtNum(signal.quantity)}</span></div>
               <div><span class="muted">Entry price</span><br><span class="num">${signal.price === null || signal.price === undefined ? "—" : fmtNum(signal.price)}</span></div>
               <div><span class="muted">Stop loss</span><br><span class="num">${signal.stop_loss === null || signal.stop_loss === undefined ? "not set on this signal" : fmtNum(signal.stop_loss)}</span></div>
               <div><span class="muted">Take profit</span><br><span class="num">${signal.take_profit === null || signal.take_profit === undefined ? "not set on this signal" : fmtNum(signal.take_profit)}</span></div>
             </div>
             ${capSlot("tr05-cap-horizon")}
             <p class="section-note">This is a preview of what was parsed, not a live reservation or order -- see Execution links below for what, if anything, actually executed.</p>`,
    });
    mountCapStates(els.plan, [
      [
        "tr05-cap-horizon",
        {
          status: "not_tracked",
          reason: "Horizon (a time-based exit target) is not a field this signal model carries.",
        },
      ],
    ]);

    // --- Instrument resolution + Routing preview (need accounts/rules/brokers) ---
    const [rulesRes, accountsRes, brokersRes] = await Promise.all([
      ctx.fetchJSON("/routing-rules"),
      ctx.fetchJSON("/accounts"),
      ctx.fetchJSON("/brokers"),
    ]);
    if (rulesRes.status === 401 || rulesRes.status === 403 || accountsRes.status === 401 || accountsRes.status === 403 || brokersRes.status === 401 || brokersRes.status === 403) {
      StateMatrix.render(els.instrument, { state: "denied", deniedCode: 401 });
      StateMatrix.render(els.routing, { state: "denied", deniedCode: 401 });
    } else if (!rulesRes.ok || !accountsRes.ok || !brokersRes.ok) {
      StateMatrix.render(els.instrument, { state: "error", message: "Could not load routing/account configuration." });
      StateMatrix.render(els.routing, { state: "error", message: "Could not load routing/account configuration." });
    } else {
      const rules = (rulesRes.data && rulesRes.data.routing_rules) || [];
      const accounts = (accountsRes.data && accountsRes.data.accounts) || [];
      const brokersByName = new Map(((brokersRes.data && brokersRes.data.brokers) || []).map((b) => [b.name, b]));
      const accountById = new Map(accounts.map((a) => [a.account_id, a]));

      const matchingRules = rules.filter(
        (r) => r.source === signal.source && (!r.symbol_filter || !r.symbol_filter.length || r.symbol_filter.includes(signal.symbol))
      );
      const destinationIds = [...new Set(matchingRules.flatMap((r) => r.destinations))];

      if (!destinationIds.length) {
        StateMatrix.render(els.instrument, { state: "empty", emptyMessage: "No routing rule matched this signal's source/symbol -- no destination account to resolve an instrument against." });
      } else {
        const instrumentRows = destinationIds.map((id) => {
          const acct = accountById.get(id);
          const mapped = acct && acct.symbol_map ? acct.symbol_map[signal.symbol] : undefined;
          return [
            `<span class="mono">${escapeHtml(id)}</span>`,
            acct ? `<span class="mono">${escapeHtml(acct.broker)}</span>` : pill("account not configured", "bad"),
            `<span class="mono">${escapeHtml(mapped || signal.symbol)}</span>`,
            mapped ? pill("mapped override", "ok") : pill("unmapped (same symbol)", "muted"),
          ];
        });
        StateMatrix.render(els.instrument, {
          state: "ready",
          html: table(["Destination account", "Broker", "Resolved symbol", "Mapping"], instrumentRows, "No destinations."),
        });
      }

      const checklist = destinationIds.length
        ? destinationIds.map((id) => {
            const acct = accountById.get(id);
            const broker = acct ? brokersByName.get(acct.broker) : undefined;
            const rows = [];
            rows.push([
              `Routing rule matches (${escapeHtml(signal.source)} → ${escapeHtml(id)})`,
              pill("matched", "ok"),
              "ROUTED",
              "live config",
            ]);
            rows.push([
              `Destination account configured`,
              acct ? pill("configured", "ok") : pill("missing", "bad"),
              acct ? "OK" : "NO_ACCOUNT",
              "GET /accounts",
            ]);
            rows.push([
              `Account admits new entries (not paused)`,
              acct ? boolPill(acct.enabled, "enabled", "paused") : pill("n/a", "muted"),
              acct && !acct.enabled ? "PAUSED" : "OK",
              "GET /accounts (enabled)",
            ]);
            rows.push([
              `Broker adapter registered (${acct ? escapeHtml(acct.broker) : "—"})`,
              broker ? pill("registered", "ok") : pill("not registered", "bad"),
              broker ? "OK" : "NO_BROKER",
              "GET /brokers",
            ]);
            rows.push([
              `Broker declares support for ${escapeHtml(signal.asset_class)}`,
              broker && broker.supported_asset_classes
                ? boolPill(broker.supported_asset_classes.includes(signal.asset_class))
                : pill("undeclared (not verified as restricted)", "muted"),
              broker && broker.supported_asset_classes && !broker.supported_asset_classes.includes(signal.asset_class) ? "UNSUPPORTED_CLASS" : "OK",
              "GET /brokers",
            ]);
            return rows;
          }).flat()
        : [["No matching routing rule", pill("no route", "bad"), "NO_ROUTE", "GET /routing-rules"]];

      StateMatrix.render(els.routing, {
        state: "ready",
        html: `<p class="section-note">Each condition below is independently evaluated from this service's own live config (never collapsed into one badge) as of this page load.</p>${table(
          ["Condition", "Outcome", "Reason code", "Evidence"],
          checklist,
          "No conditions."
        )}`,
      });

      // --- Actions panel (needs accounts/brokers in scope for context) ---
      renderActions(ctx, els.actions, signal);
    }

    // --- Execution links ---
    const ordersRes = await ctx.fetchJSON("/orders?limit=500");
    if (ordersRes.status === 401 || ordersRes.status === 403) {
      StateMatrix.render(els.execution, { state: "denied", deniedCode: ordersRes.status });
    } else if (!ordersRes.ok) {
      StateMatrix.render(els.execution, { state: "error", message: "Could not load execution links." });
    } else {
      const linkedOrders = ((ordersRes.data && ordersRes.data.orders) || []).filter((o) => o.signal_id === eventId);
      if (!linkedOrders.length) {
        StateMatrix.render(els.execution, { state: "empty", emptyMessage: "No order was submitted from this signal." });
      } else {
        const rows = linkedOrders.map((o) => [
          `<span class="mono">${escapeHtml(o.broker_order_id || String(o.id))}</span>`,
          `<span class="mono">${escapeHtml(o.account_id)}</span>`,
          o.status === "filled" ? pill("filled", "ok") : o.status === "pending" ? pill("pending", "warn") : pill(o.status || "—", "bad"),
          fmtNum(o.requested_quantity),
          fmtNum(o.filled_quantity),
          `<a href="#/trade/orders">Open in Orders (TR-06)</a>`,
        ]);
        StateMatrix.render(els.execution, {
          state: "ready",
          html: table(["Order", "Account", "Status", "Requested", "Filled", ""], rows, "No orders."),
        });
      }
    }

    ctx.setChrome({ asOf: new Date().toISOString(), breadcrumb: "Trade / Signals / " + eventId });
  }

  function renderActions(ctx, el, signal) {
    const canReclassify = signal.raw && typeof signal.raw.text === "string" && signal.raw.text.length > 0;
    StateMatrix.render(el, {
      state: "ready",
      html: `
        <div class="tr-controls-row">
          <button type="button" id="tr05-reclassify" ${canReclassify ? "" : "disabled"}>Reclassify in sandbox</button>
          ${
            canReclassify
              ? `<span class="section-note">No-effects dry-run against the current parser grammar. Shows an assessment here -- does not create or replace this signal.</span>`
              : capSlot("tr05-cap-reclassify")
          }
        </div>
        <div id="tr05-reclassify-result"></div>
      `,
    });
    if (!canReclassify) {
      mountCapStates(el, [
        [
          "tr05-cap-reclassify",
          {
            status: "unsupported",
            reason:
              "Unsupported for this signal: it did not come through the free-text parser, so there is no source text to reclassify (structured-payload sources have nothing this sandbox can re-parse).",
          },
        ],
      ]);
    }
    const btn = el.querySelector("#tr05-reclassify");
    if (btn && canReclassify) {
      btn.addEventListener("click", async () => {
        const resultEl = el.querySelector("#tr05-reclassify-result");
        resultEl.textContent = "Running sandbox reclassification…";
        try {
          const result = await postJSON(`/sources/${encodeURIComponent(signal.source)}/classify-messages`, {
            texts: [signal.raw.text],
            asset_class: signal.asset_class,
            analyst: signal.analyst || null,
          });
          const d = (result.dispositions || [])[0];
          resultEl.innerHTML = d
            ? `<p class="section-note">Sandbox outcome (not persisted, not a new live signal): <strong>${escapeHtml(d.outcome)}</strong>${d.detail ? " — " + escapeHtml(d.detail) : ""}</p>`
            : `<p class="section-note">No disposition returned.</p>`;
        } catch (err) {
          resultEl.innerHTML = `<p class="sm-error-message">Reclassification failed: ${escapeHtml(err.message)}</p>`;
        }
      });
    }
    const unsupported = document.createElement("div");
    Components.renderCapabilityState(unsupported, {
      status: "unsupported",
      reason: "Compare parser versions (TR-05-A02) has no backing capability in this build -- there is no versioned parser registry to compare against.",
    });
    el.appendChild(unsupported);

    const openExec = document.createElement("div");
    openExec.className = "tr-controls-row";
    openExec.innerHTML = `<a href="#/trade/orders">Open execution (TR-06)</a>`;
    el.appendChild(openExec);
  }

  window.Views = window.Views || {};
  window.Views.tr05 = {
    title: "Signal detail",
    breadcrumb: "Trade / Signals / Detail",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = shell();
      await load(ctx);
      const signalId = ctx.params.event_id || ctx.params.id;
      ctx.registerPoll(`tr05:${signalId}`, 10000, () => load(ctx));
    },
  };
  Router.register("/trade/signals/:event_id", "tr05");
  Router.register("/trade/signals/:id", "tr05");
})();
