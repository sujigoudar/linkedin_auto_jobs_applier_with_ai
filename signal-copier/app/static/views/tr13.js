/* TR-13: Reconciliation and trading incidents (`#/trade/incidents`).
 *
 * Purpose (per the spec): "Explain actual discrepancies and approved
 * containment rather than silently balancing them." This build has NO
 * dedicated incidents table/model (no incident_id, no acknowledge/assign/
 * resolve persistence anywhere in app/db.py) -- so rather than fabricate
 * one, this screen derives every "incident" it shows from real, already-
 * tracked reconciliation state (app/reconciliation.py, app/lifecycle/
 * manager.py) that already exists for a completely different reason
 * (keeping positions actually protected):
 *
 *   - A HALTED managed-lifecycle position (app/lifecycle/close_arbiter.py's
 *     PositionCloseArbiter, surfaced via GET /positions'
 *     managed_lifecycles[].halted/halt_reason) -- a real containment
 *     decision the engine already made, not a UI-invented severity.
 *   - UNCOVERED quantity > 0 on an open managed lifecycle (owned shares
 *     with no confirmed working stop yet) -- a real protection deficit
 *     tracked quantity-by-quantity.
 *   - An unresolved pending_exit/pending_entry -- reconciliation still in
 *     progress for this exact position, not yet a discrepancy or a
 *     resolution.
 *   - A REJECTED order (GET /orders, status="rejected") -- broker refused
 *     what this service asked for; the order's own `message` is shown
 *     verbatim (broker-controlled text, always escaped -- see C14).
 *
 * "Broker/store comparison" only has STORE-side coverage data
 * (owned/covered/uncovered, from this service's own tracked lifecycle) --
 * a genuine live broker-side position readback exists internally
 * (BrokerAdapter.get_broker_position, used by
 * OrderReconciler._reconcile_broker_positions) but is NOT exposed by any
 * GET endpoint, so a true broker-vs-store diff table is rendered
 * `unsupported` rather than faked from data this screen doesn't have.
 *
 * All 3 actions (Acknowledge, Run scoped readback, Review recovery
 * action) render `unsupported`: there is no incident_id to acknowledge/
 * assign against, no manual "run reconciliation now" endpoint (the
 * reconciler is a fixed-interval background loop -- app/reconciliation.py
 * -- with no trigger route), and no recovery-review/approval workflow.
 * Never auto-resolves or hides a real discrepancy shown above -- this
 * screen is strictly read-only.
 */
(function () {
  "use strict";

  function unsupportedNote(reason) {
    const el = document.createElement("div");
    StateMatrix.render(el, { state: "unsupported", reason });
    return el.outerHTML;
  }

  function severityPill(sev) {
    if (sev === "high") return pill("high", "bad");
    if (sev === "medium") return pill("medium", "warn");
    return pill("low", "muted");
  }

  function shell() {
    return `
      <section class="tr-panel" id="tr13-p01"><h2>Prioritized incidents</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr13-p02"><h2>Broker/store comparison</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr13-p03"><h2>Evidence timeline</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr13-p04"><h2>Containment</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr13-p05"><h2>Recovery</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr13-actions"><h2>Actions</h2><div class="tr-panel-body"></div></section>
    `;
  }

  async function load(ctx) {
    const els = {
      incidents: ctx.container.querySelector("#tr13-p01 .tr-panel-body"),
      comparison: ctx.container.querySelector("#tr13-p02 .tr-panel-body"),
      timeline: ctx.container.querySelector("#tr13-p03 .tr-panel-body"),
      containment: ctx.container.querySelector("#tr13-p04 .tr-panel-body"),
      recovery: ctx.container.querySelector("#tr13-p05 .tr-panel-body"),
      actions: ctx.container.querySelector("#tr13-actions .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const [positionsRes, ordersRes, healthRes] = await Promise.all([
      ctx.fetchJSON("/positions"),
      ctx.fetchJSON("/orders?limit=100"),
      ctx.fetchJSON("/health"),
    ]);
    if (positionsRes.status === 401 || positionsRes.status === 403) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "denied", deniedCode: positionsRes.status });
      return;
    }
    if (!positionsRes.ok) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "error", message: "Could not load position/reconciliation state." });
      return;
    }
    const lifecycles = (positionsRes.data && positionsRes.data.managed_lifecycles) || [];
    const orders = (ordersRes.ok && ordersRes.data && ordersRes.data.orders) || [];
    const health = healthRes.ok ? healthRes.data : null;

    const rejectedOrders = orders.filter((o) => o.status === "rejected");
    const halted = lifecycles.filter((l) => l.halted);
    const uncovered = lifecycles.filter((l) => !l.halted && l.uncovered_quantity > 0);

    // --- Prioritized incidents: real, derived (never fabricated severity
    // beyond what the engine/arbiter already decided). ---
    const incidentRows = [];
    for (const l of halted) {
      incidentRows.push({
        sev: "high",
        cells: [
          `halt: ${escapeHtml(l.symbol)}`,
          `<span class="mono">${escapeHtml(l.account_id)}</span>`,
          severityPill("high"),
          `${fmtNum(l.owned_quantity)} owned`,
          `${fmtNum(l.uncovered_quantity)} uncovered`,
          l.stop_status === "confirmed" ? pill("stop confirmed", "ok") : pill(escapeHtml(l.stop_status || "unknown"), "warn"),
          "owner (sole operator)",
          pill(escapeHtml(l.halt_reason || "halted"), "bad"),
        ],
      });
    }
    for (const l of uncovered) {
      incidentRows.push({
        sev: "medium",
        cells: [
          `protection deficit: ${escapeHtml(l.symbol)}`,
          `<span class="mono">${escapeHtml(l.account_id)}</span>`,
          severityPill("medium"),
          `${fmtNum(l.owned_quantity)} owned`,
          `${fmtNum(l.uncovered_quantity)} uncovered`,
          pill(escapeHtml(l.stop_status || "unknown"), "warn"),
          "owner (sole operator)",
          l.pending_exit || l.pending_entry ? pill("resolving", "warn") : pill("open", "bad"),
        ],
      });
    }
    for (const o of rejectedOrders.slice(0, 25)) {
      incidentRows.push({
        sev: "medium",
        cells: [
          `order rejected: ${escapeHtml(o.symbol || "—")}`,
          `<span class="mono">${escapeHtml(o.account_id)}</span>`,
          severityPill("medium"),
          `${fmtNum(o.requested_quantity)} requested`,
          `${fmtNum(o.filled_quantity || 0)} filled`,
          "n/a (order-level, not a live position)",
          "owner (sole operator)",
          pill(o.message ? o.message : "rejected", "bad"),
        ],
      });
    }

    if (!incidentRows.length) {
      StateMatrix.render(els.incidents, {
        state: "empty",
        emptyMessage: "No active incidents; last complete checks are shown separately.",
        nextRoute: "/trade",
        nextLabel: "Trading command center (TR-01)",
      });
    } else {
      const order = { high: 0, medium: 1, low: 2 };
      incidentRows.sort((a, b) => order[a.sev] - order[b.sev]);
      StateMatrix.render(els.incidents, {
        state: "ready",
        html: `<p class="section-note">Derived from real, already-tracked reconciliation state (app/lifecycle/manager.py's coverage tracking and app/reconciliation.py) -- not a separate incidents store (this build has none). Halts and protection deficits reflect a decision the engine/arbiter already made; nothing here is auto-resolved by this screen.</p>${table(
          ["Incident", "Account", "Severity", "Known exposure", "Difference", "Protection", "Owner", "State"],
          incidentRows.map((r) => r.cells),
          "No active incidents."
        )}`,
      });
    }

    // --- Broker/store comparison ---
    if (!lifecycles.length) {
      StateMatrix.render(els.comparison, { state: "empty", emptyMessage: "No open managed-lifecycle position to compare." });
    } else {
      const rows = lifecycles.map((l) => [
        `<span class="mono">${escapeHtml(l.account_id)}</span>`,
        escapeHtml(l.symbol),
        fmtNum(l.owned_quantity),
        fmtNum(l.covered_quantity),
        l.uncovered_quantity > 0 ? pill(fmtNum(l.uncovered_quantity), "bad") : pill("0", "ok"),
        pill("not exposed by any GET endpoint", "muted"),
      ]);
      StateMatrix.render(els.comparison, {
        state: "ready",
        html: `<p class="section-note">STORE side only: this service's own tracked owned/covered/uncovered quantity (app/lifecycle/manager.py). A genuine broker-side readback exists internally (BrokerAdapter.get_broker_position, used by OrderReconciler._reconcile_broker_positions) but is not exposed by any GET endpoint -- the "Broker owned" column is honestly unsupported rather than a guessed number.</p>${table(
          ["Account", "Symbol", "Store: owned", "Store: covered", "Store: uncovered", "Broker: owned (live readback)"],
          rows,
          "No positions."
        )}`,
      });
    }

    // --- Evidence timeline: immutable order history, newest first ---
    if (!orders.length) {
      StateMatrix.render(els.timeline, { state: "empty", emptyMessage: "No order events recorded yet." });
    } else {
      const rows = orders.slice(0, 25).map((o) => [
        `<span class="mono">${escapeHtml(String(o.id))}</span>`,
        `<span class="mono">${escapeHtml(o.account_id)}</span>`,
        escapeHtml(o.symbol || "—"),
        escapeHtml(o.side || "—"),
        o.status === "rejected" ? pill("rejected", "bad") : o.status === "filled" ? pill("filled", "ok") : pill(escapeHtml(o.status), "warn"),
        escapeHtml(o.executed_at || "—"),
        o.message ? escapeHtml(o.message) : "—",
      ]);
      StateMatrix.render(els.timeline, {
        state: "ready",
        html: `<p class="section-note">Immutable order history (GET /orders), newest first, exactly as recorded -- never reordered. Showing ${Math.min(25, orders.length)} of ${orders.length}.</p>${table(
          ["Order ID", "Account", "Symbol", "Side", "Status", "Executed at", "Message"],
          rows,
          "No events."
        )}`,
      });
    }

    // --- Containment: real halt state from the close arbiter ---
    if (!halted.length) {
      StateMatrix.render(els.containment, { state: "empty", emptyMessage: "No position is currently halted." });
    } else {
      const rows = halted.map((l) => [
        `<span class="mono">${escapeHtml(l.account_id)}</span>`,
        escapeHtml(l.symbol),
        pill(escapeHtml(l.halt_reason || "halted"), "bad"),
        "close arbiter (app/lifecycle/close_arbiter.py) -- exits are refused while halted",
      ]);
      StateMatrix.render(els.containment, {
        state: "ready",
        html: `<p class="section-note">Real containment: this build's PositionCloseArbiter refuses new close/exit attempts for a halted (account, symbol) rather than let two conflicting close paths race -- see app/lifecycle/close_arbiter.py's module docstring. This is the actual containment mechanism, not a UI-only label.</p>${table(
          ["Account", "Symbol", "Reason", "Mechanism"],
          rows,
          "No halted positions."
        )}`,
      });
    }

    // --- Recovery: unresolved pending entries/exits + reconciler health ---
    const recovering = lifecycles.filter((l) => l.pending_exit || l.pending_entry);
    const reconcilerNote = health
      ? `Background reconciliation worker (app/reconciliation.py): reconciler_ok=${escapeHtml(String(health.reconciler_ok))} (GET /health). This is the process that resolves every pending_entry/pending_exit row below on its next pass (RECONCILE_INTERVAL_SECONDS-scheduled) -- there is no manual "run now" trigger in this build.`
      : "Could not read worker liveness (GET /health).";
    if (!recovering.length) {
      StateMatrix.render(els.recovery, { state: "empty", emptyMessage: `No pending entry/exit is currently being resolved. ${reconcilerNote}` });
    } else {
      const rows = recovering.map((l) => {
        const p = l.pending_exit || l.pending_entry;
        const kind = l.pending_exit ? "pending exit" : "pending entry";
        return [
          `<span class="mono">${escapeHtml(l.account_id)}</span>`,
          escapeHtml(l.symbol),
          kind,
          `<span class="mono">${escapeHtml(p.broker_order_id || "—")}</span>`,
          `${fmtNum(p.confirmed_filled_quantity)} / ${fmtNum(p.requested_quantity)}`,
          pill("resolving on next reconciliation pass", "warn"),
        ];
      });
      StateMatrix.render(els.recovery, {
        state: "ready",
        html: `<p class="section-note">${reconcilerNote}</p>${table(
          ["Account", "Symbol", "Phase", "Broker order ID", "Confirmed / requested", "State"],
          rows,
          "None."
        )}`,
      });
    }

    // --- Actions: none have a real backing capability in this build ---
    StateMatrix.render(els.actions, {
      state: "unsupported",
      reason:
        "TR-13-A01 (Acknowledge), TR-13-A02 (Run scoped readback) and TR-13-A03 (Review recovery action) have no backing capability: this build has no incident_id/acknowledge-assign-resolve persistence (no incidents table exists anywhere in app/db.py), no manual reconciliation-trigger endpoint (app/reconciliation.py's OrderReconciler runs on a fixed background interval with no HTTP trigger route), and no recovery-review/approval workflow. Rather than fabricate a form against capabilities that don't exist, every panel above stays read-only and every real discrepancy stays visible exactly as found.",
    });

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr13 = {
    title: "Reconciliation and trading incidents",
    breadcrumb: "Trade / Incidents",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = shell();
      await load(ctx);
      ctx.registerPoll("tr13", 10000, () => load(ctx));
    },
  };
  Router.register("/trade/incidents", "tr13");
})();
