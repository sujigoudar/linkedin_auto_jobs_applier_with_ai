/* TR-13: Reconciliation and trading incidents (`#/trade/incidents`).
 *
 * Purpose (per the spec): "Explain actual discrepancies and approved
 * containment rather than silently balancing them." This build has NO
 * dedicated incidents table/model (no incident_id, no acknowledge/assign/
 * resolve persistence anywhere in app/db.py) -- so rather than fabricate
 * one, every "incident" shown here is derived from real, already-tracked
 * reconciliation state (app/reconciliation.py, app/lifecycle/manager.py,
 * app/lifecycle/close_arbiter.py) that already exists for a completely
 * different reason (keeping positions actually protected):
 *
 *   - A HALTED managed-lifecycle position (app/lifecycle/close_arbiter.py's
 *     CloseArbiter, surfaced via GET /positions' managed_lifecycles[].
 *     halted/halt_reason) -- a real containment decision the engine
 *     already made, not a UI-invented severity.
 *   - UNCOVERED quantity > 0 on an open managed lifecycle (owned shares
 *     with no confirmed working stop yet) -- a real protection deficit
 *     tracked quantity-by-quantity.
 *   - A REJECTED order (GET /orders, status="rejected") -- broker refused
 *     what this service asked for; the order's own `message` is shown
 *     verbatim (broker-controlled text, always escaped -- see C14).
 *
 * 2026-09 design review asked for a genuine incident-lifecycle record
 * (severity / affected account / actual broker state / internal state /
 * possible exposure / detected time / owner / containment / current
 * status / evidence / resolution) and a reconciliation matrix (Broker qty
 * | Internal qty | Allocation qty | Protected qty | Possible closing qty |
 * Difference). Two fields have NO real backing data anywhere in this
 * codebase and are rendered `Components.renderCapabilityState('not_tracked',
 * ...)` per row/incident rather than a fabricated placeholder:
 *
 *   - Broker qty / actual broker state: `BrokerAdapter.get_broker_position`
 *     is REAL and already used internally by
 *     `OrderReconciler._reconcile_broker_positions` (app/reconciliation.py)
 *     for brokers with `has_position_readback_capability=True` (verified
 *     against app/brokers/base.py/paper.py/alpaca.py/ccxt_broker.py) -- but
 *     NO GET endpoint in this build (checked: GET /positions, /brokers,
 *     /accounts, /orders, /health -- see app/main.py) exposes that live
 *     read to the dashboard, not even for PaperBroker, whose state is
 *     otherwise fully internal/queryable. This screen is a browser-side
 *     file with no channel to that in-process state except an HTTP
 *     endpoint, so it stays honestly not_tracked for every broker --
 *     the per-broker REASON differs (readback-capable-but-not-exposed vs.
 *     no-readback-capability-at-all) using GET /brokers' own
 *     has_position_readback_capability flag, so an operator can tell those
 *     two very different gaps apart.
 *   - Detected time for a halt/protection-deficit "incident": CloseArbiter's
 *     `_Ledger` (app/lifecycle/close_arbiter.py) tracks only the CURRENT
 *     halted/halt_reason, never when it started -- there is no persisted
 *     halt-onset timestamp anywhere to show. A rejected order's detected
 *     time IS real (GET /orders' executed_at).
 *   - Resolution for a halt: `CloseArbiter.halt()` is only ever called to
 *     set `halted = True` -- grepped this whole tree; nothing ever sets it
 *     back to False. There is no automatic or manual un-halt mechanism in
 *     this build.
 *   - Owner/containment ARE real, just not a per-incident assignment: this
 *     deployment has exactly one authenticated role (`require_owner` /
 *     `require_owner_read`, app/main.py) -- no multi-user assignment model
 *     exists to name a different "owner" per incident, so every row states
 *     that single-owner fact rather than a placeholder like "unassigned".
 *     Containment is real for a halted position (the arbiter's own refusal
 *     mechanism) and honestly not_tracked for a non-halted protection
 *     deficit or a rejected order (no containment action exists there).
 *
 * The recovery action wired on halted/uncovered rows is
 * `POST /positions/{account_id}/{symbol}/close` -- the SAME real,
 * already-shipped "Exit now" endpoint TR-01/TR-06 use (app/main.py's
 * close_single_position), gated through the shared
 * Components.confirmAction preview -> impact -> confirm -> result flow
 * (app/static/components/action-confirm.js). It is not a fabricated
 * per-row action: `request_exit` (app/lifecycle/manager.py) itself
 * refuses cleanly (OrderResult REJECTED, "halted: ...") when the position
 * is halted, so offering it there is honest -- the preview says so before
 * the operator confirms. There is still no acknowledge/assign/manual
 * "run reconciliation now" endpoint anywhere in this build (the reconciler
 * is a fixed-interval background loop, app/reconciliation.py, with no HTTP
 * trigger route) -- those stay `unsupported`, not disguised as something
 * else.
 */
(function () {
  "use strict";

  function capabilityBadgeHtml(opts) {
    const el = document.createElement("div");
    Components.renderCapabilityState(el, opts);
    return el.innerHTML;
  }

  function severityPill(sev) {
    if (sev === "high") return pill("high", "bad");
    if (sev === "medium") return pill("medium", "warn");
    return pill("low", "muted");
  }

  // Real per-broker reason for why Broker qty / actual broker state can
  // never be shown as a live number in this build -- see this module's
  // own docstring above for the underlying verification. Never fabricates
  // a number even for a readback-capable broker (e.g. "paper").
  function brokerReadbackGap(accountId, brokerByAccount, brokerCaps) {
    const brokerName = brokerByAccount.get(accountId);
    if (!brokerName) {
      return {
        status: "not_tracked",
        reason: `No broker configuration found for account "${accountId}" (GET /accounts) -- this account's broker identity is unknown, so no readback capability can even be looked up.`,
      };
    }
    if (!brokerCaps.has(brokerName)) {
      return {
        status: "not_tracked",
        reason: `Broker "${brokerName}" is not a registered adapter in this deployment (GET /brokers) -- no readback capability information exists to check.`,
      };
    }
    if (brokerCaps.get(brokerName)) {
      return {
        status: "not_tracked",
        reason: `Broker "${brokerName}" DOES support a real position readback (has_position_readback_capability=true, GET /brokers) -- app/reconciliation.py's OrderReconciler already calls BrokerAdapter.get_broker_position on it internally to true up owned_quantity. But no GET endpoint in this build exposes that live read to the dashboard (checked GET /positions, /orders, /brokers, /accounts, /health), so it cannot be shown here as a current, independently-verifiable number -- not even for this broker.`,
      };
    }
    return {
      status: "not_tracked",
      reason: `Broker "${brokerName}" adapter has no live position-readback capability at all (has_position_readback_capability=false, GET /brokers) -- there is no live broker read for this broker in this build, period.`,
    };
  }

  function allocationQtyFor(l) {
    if (l.pending_exit) return l.pending_exit.unresolved_remainder;
    if (l.pending_entry) {
      return Math.max(0, (l.pending_entry.requested_quantity || 0) - (l.pending_entry.confirmed_filled_quantity || 0));
    }
    return 0;
  }

  function possibleClosingQtyFor(l) {
    const locked = l.pending_exit ? l.pending_exit.unresolved_remainder || 0 : 0;
    return Math.max(0, (l.owned_quantity || 0) - locked);
  }

  function highlight(value, tone) {
    // `--crit`/`--warn` design tokens (app/static/design-system.css),
    // applied inline since this is a plain view file (no scoped stylesheet
    // of its own) -- see this module's docstring on the design review's
    // "highlight using --crit/--warn tokens" ask.
    return `<strong style="color:var(--${tone});">${escapeHtml(fmtNum(value))}</strong>`;
  }

  const OWNER_NOTE =
    "Sole operator (this deployment's single-owner auth model -- app/main.py's require_owner/require_owner_read; no multi-user assignment concept exists to name a different owner per incident).";

  function shell() {
    return `
      <section class="tr-panel" id="tr13-p01"><h2>Prioritized incidents</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr13-p02"><h2>Reconciliation matrix</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr13-p03"><h2>Evidence timeline</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr13-p04"><h2>Containment</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr13-p05"><h2>Recovery</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr13-actions"><h2>Actions</h2><div class="tr-panel-body"></div></section>
    `;
  }

  // Real preview for the "Close this position now" action -- built from
  // data this view already has loaded (GET /positions), never a fabricated
  // guess. Honestly warns when the position is currently halted, since
  // app/lifecycle/manager.py's request_exit will then cleanly refuse the
  // request (OrderResult REJECTED, "halted: ...") rather than close it.
  function previewClose(l) {
    const rows = [
      { label: "Owned", value: `${fmtNum(l.owned_quantity)} sh`, tone: "neutral" },
      { label: "Covered (working stop)", value: `${fmtNum(l.covered_quantity)} sh`, tone: "ok" },
      { label: "Uncovered", value: `${fmtNum(l.uncovered_quantity)} sh`, tone: l.uncovered_quantity > 0 ? "warn" : "ok" },
    ];
    const notes = [
      "Submits a real order through app/main.py's POST /positions/{account}/{symbol}/close -- the same 'Exit now' action used elsewhere in this dashboard.",
      "Fill price cannot be previewed -- this service has no quote-before-order capability wired into this action.",
    ];
    if (l.halted) {
      notes.unshift(
        `This position is currently HALTED (${l.halt_reason || "reason not recorded"}) -- app/lifecycle/manager.py's request_exit will refuse this request cleanly (a rejected result, not an error) rather than close it. Confirming here submits the request anyway so the refusal is recorded as real evidence.`
      );
    }
    return { severity: l.halted ? "critical" : l.uncovered_quantity > 0 ? "warning" : "info", rows, notes };
  }

  function renderCloseResult(outcome) {
    if (!outcome || !outcome.ok) {
      const message = (outcome && outcome.error) || "Unknown error.";
      return `<p class="action-confirm-result-heading">Failed</p><p class="action-confirm-note action-confirm-impact-crit">${escapeHtml(message)}</p>`;
    }
    const r = outcome.result || {};
    return `<p class="action-confirm-result-heading">Real result from POST /positions/${escapeHtml(r.account_id || "")}/${escapeHtml(r.symbol || "")}/close</p>
      <div class="action-confirm-row"><span class="ac-label">Status</span><span class="ac-value">${escapeHtml(r.status || "—")}</span></div>
      <div class="action-confirm-row"><span class="ac-label">Filled</span><span class="ac-value">${r.filled_quantity ?? "—"}</span></div>
      ${r.message ? `<p class="action-confirm-note">${escapeHtml(r.message)}</p>` : ""}`;
  }

  function wireCloseButtons(ctx, container, lifecycleByKey) {
    container.querySelectorAll(".tr13-close-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const accountId = btn.getAttribute("data-account");
        const symbol = btn.getAttribute("data-symbol");
        const l = lifecycleByKey.get(`${accountId}::${symbol}`);
        if (!l) return;
        await Components.confirmAction({
          title: `Close position: ${symbol} (${accountId})`,
          confirmWord: symbol,
          confirmLabel: "Close this position now",
          previewFn: async () => previewClose(l),
          onConfirm: () => postJSON(`/positions/${encodeURIComponent(accountId)}/${encodeURIComponent(symbol)}/close`, {}),
          renderResult: renderCloseResult,
        });
        await load(ctx);
      });
    });
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

    const [positionsRes, ordersRes, healthRes, accountsRes, brokersRes] = await Promise.all([
      ctx.fetchJSON("/positions"),
      ctx.fetchJSON("/orders?limit=100"),
      ctx.fetchJSON("/health"),
      ctx.fetchJSON("/accounts"),
      ctx.fetchJSON("/brokers"),
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
    const plainPositions = (positionsRes.data && positionsRes.data.positions) || [];
    const orders = (ordersRes.ok && ordersRes.data && ordersRes.data.orders) || [];
    const health = healthRes.ok ? healthRes.data : null;
    const configAccounts = (accountsRes.ok && accountsRes.data && accountsRes.data.accounts) || [];
    const brokerList = (brokersRes.ok && brokersRes.data && brokersRes.data.brokers) || [];

    const brokerByAccount = new Map(configAccounts.map((a) => [a.account_id, a.broker]));
    const brokerCaps = new Map(brokerList.map((b) => [b.name, b.has_position_readback_capability]));
    const lifecycleByKey = new Map(lifecycles.map((l) => [`${l.account_id}::${l.symbol}`, l]));

    const rejectedOrders = orders.filter((o) => o.status === "rejected");
    const halted = lifecycles.filter((l) => l.halted);
    const uncovered = lifecycles.filter((l) => !l.halted && l.uncovered_quantity > 0);

    // --- Prioritized incidents: full lifecycle records, every field
    // either real or an explicit not_tracked capability badge -- never a
    // fabricated placeholder (see this module's docstring). ---
    const incidentRows = [];
    for (const l of halted) {
      const brokerGap = brokerReadbackGap(l.account_id, brokerByAccount, brokerCaps);
      incidentRows.push({
        sev: "high",
        canClose: true,
        accountId: l.account_id,
        symbol: l.symbol,
        cells: [
          `halt: ${escapeHtml(l.symbol)}`,
          severityPill("high"),
          `<span class="mono">${escapeHtml(l.account_id)}</span>`,
          capabilityBadgeHtml(brokerGap),
          `${fmtNum(l.owned_quantity)} owned, ${fmtNum(l.covered_quantity)} covered, ${fmtNum(l.uncovered_quantity)} uncovered (stop_status=${escapeHtml(l.stop_status)})`,
          `${fmtNum(l.uncovered_quantity)} sh with no confirmed working stop`,
          capabilityBadgeHtml({
            status: "not_tracked",
            reason: "No halt-onset timestamp is persisted anywhere in this build (CloseArbiter's ledger, app/lifecycle/close_arbiter.py, tracks only the CURRENT halted/halt_reason, never when it started) -- this is a live status check, not an event log entry.",
          }),
          OWNER_NOTE,
          `Close arbiter refuses new close/exit attempts for this (account, symbol) -- app/lifecycle/close_arbiter.py's CloseArbiter.reserve() returns false while halted.`,
          pill("halted", "bad"),
          escapeHtml(l.halt_reason || "halted"),
          capabilityBadgeHtml({
            status: "not_tracked",
            reason: "CloseArbiter.halt() (app/lifecycle/close_arbiter.py) is only ever called to set halted=True -- nothing in this codebase ever sets it back to False. There is no automatic or manual un-halt mechanism in this build; the position must be closed (see Recovery) to remove it from the open-lifecycle set.",
          }),
          `<button type="button" class="tr13-close-btn" data-account="${escapeHtml(l.account_id)}" data-symbol="${escapeHtml(l.symbol)}">Close this position now</button>`,
        ],
      });
    }
    for (const l of uncovered) {
      const brokerGap = brokerReadbackGap(l.account_id, brokerByAccount, brokerCaps);
      const resolving = Boolean(l.pending_exit || l.pending_entry);
      incidentRows.push({
        sev: "medium",
        canClose: true,
        accountId: l.account_id,
        symbol: l.symbol,
        cells: [
          `protection deficit: ${escapeHtml(l.symbol)}`,
          severityPill("medium"),
          `<span class="mono">${escapeHtml(l.account_id)}</span>`,
          capabilityBadgeHtml(brokerGap),
          `${fmtNum(l.owned_quantity)} owned, ${fmtNum(l.covered_quantity)} covered, ${fmtNum(l.uncovered_quantity)} uncovered (stop_status=${escapeHtml(l.stop_status)})`,
          `${fmtNum(l.uncovered_quantity)} sh with no confirmed working stop`,
          capabilityBadgeHtml({
            status: "not_tracked",
            reason: "No protection-deficit-onset timestamp is persisted anywhere in this build -- uncovered_quantity is a live, current-state computation (owned minus covered), not an event log entry.",
          }),
          OWNER_NOTE,
          resolving
            ? "None triggered -- a not-yet-halted deficit does not refuse new orders; app/reconciliation.py's retry_unprotected_positions (below) is remediation, not containment."
            : capabilityBadgeHtml({
                status: "not_tracked",
                reason: "No containment mechanism exists for a protection deficit that hasn't triggered a halt -- CloseArbiter only refuses orders once halted=True; this row isn't halted.",
              }),
          resolving ? pill("resolving", "warn") : pill("open", "bad"),
          l.pending_exit || l.pending_entry ? `pending ${l.pending_exit ? "exit" : "entry"}, broker_order_id=${escapeHtml((l.pending_exit || l.pending_entry).broker_order_id || "—")}` : "no pending order in flight",
          `Auto-retried every reconciliation pass (RECONCILE_INTERVAL_SECONDS) by app/reconciliation.py's retry_unprotected_positions until stop_status becomes stop_confirmed -- a real, already-running remediation loop, not a manual step.`,
          `<button type="button" class="tr13-close-btn" data-account="${escapeHtml(l.account_id)}" data-symbol="${escapeHtml(l.symbol)}">Close this position now</button>`,
        ],
      });
    }
    for (const o of rejectedOrders.slice(0, 25)) {
      const brokerGap = brokerReadbackGap(o.account_id, brokerByAccount, brokerCaps);
      incidentRows.push({
        sev: "medium",
        canClose: false,
        cells: [
          `order rejected: ${escapeHtml(o.symbol || "—")}`,
          severityPill("medium"),
          `<span class="mono">${escapeHtml(o.account_id)}</span>`,
          capabilityBadgeHtml(brokerGap),
          `order status=rejected, ${fmtNum(o.filled_quantity || 0)} filled / ${fmtNum(o.requested_quantity)} requested`,
          capabilityBadgeHtml({
            status: "not_tracked",
            reason: "A rejected order never opened a position -- there is no ongoing risk exposure to report here (see Evidence for the unfilled request quantity instead).",
          }),
          o.executed_at ? escapeHtml(o.executed_at) : capabilityBadgeHtml({ status: "not_tracked", reason: "This order row has no executed_at timestamp recorded." }),
          OWNER_NOTE,
          capabilityBadgeHtml({
            status: "not_tracked",
            reason: "No containment action applies to a rejected order -- it never executed, so there is nothing open to contain.",
          }),
          pill("rejected", "bad"),
          o.message ? escapeHtml(o.message) : "rejected (no message recorded)",
          capabilityBadgeHtml({
            status: "not_tracked",
            reason: "A rejected order is terminal -- grepped this codebase for an automatic retry-of-rejected-order mechanism; none exists, so there is no real resolution path to report beyond what a fresh signal would do.",
          }),
          "—",
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
        html: `<p class="section-note">Every incident below is derived from real, already-tracked reconciliation state (app/lifecycle/manager.py's coverage tracking, app/lifecycle/close_arbiter.py's halt ledger, and app/reconciliation.py) -- not a separate incidents store (this build has none). A field with no real backing data anywhere in this codebase is shown as an explicit "Not tracked" badge (expand it for why) rather than a fabricated value. Nothing here is auto-resolved by this screen.</p>${table(
          ["Incident", "Severity", "Account", "Actual broker state", "Internal state", "Possible exposure", "Detected", "Owner", "Containment", "Status", "Evidence", "Resolution", "Action"],
          incidentRows.map((r) => r.cells),
          "No active incidents."
        )}`,
      });
      wireCloseButtons(ctx, els.incidents, lifecycleByKey);
    }

    // --- Reconciliation matrix: Broker qty | Internal qty | Allocation
    // qty | Protected qty | Possible closing qty | Difference. Every
    // column is real except Broker qty (see this module's docstring) --
    // Difference is only ever computed from two REAL sides (Internal vs.
    // Protected, i.e. the tracked uncovered_quantity), never fabricated
    // against the not_tracked Broker qty. ---
    const matrixKeys = new Set(lifecycles.map((l) => `${l.account_id}::${l.symbol}`));
    const plainOnly = plainPositions.filter((p) => !matrixKeys.has(`${p.account_id}::${p.symbol}`));
    if (!lifecycles.length && !plainOnly.length) {
      StateMatrix.render(els.comparison, { state: "empty", emptyMessage: "No open position to reconcile." });
    } else {
      const rows = [];
      for (const l of lifecycles) {
        const brokerGap = brokerReadbackGap(l.account_id, brokerByAccount, brokerCaps);
        const diff = l.uncovered_quantity || 0;
        const tone = diff > 0 ? (l.halted ? "crit" : "warn") : null;
        rows.push([
          `<span class="mono">${escapeHtml(l.account_id)}</span>`,
          escapeHtml(l.symbol),
          capabilityBadgeHtml(brokerGap),
          fmtNum(l.owned_quantity),
          fmtNum(allocationQtyFor(l)),
          fmtNum(l.covered_quantity),
          fmtNum(possibleClosingQtyFor(l)),
          tone ? highlight(diff, tone) : fmtNum(diff),
        ]);
      }
      for (const p of plainOnly) {
        const brokerGap = brokerReadbackGap(p.account_id, brokerByAccount, brokerCaps);
        rows.push([
          `<span class="mono">${escapeHtml(p.account_id)}</span>`,
          escapeHtml(p.symbol),
          capabilityBadgeHtml(brokerGap),
          fmtNum(p.net_quantity),
          capabilityBadgeHtml({
            status: "not_tracked",
            reason: "This account is not managed-lifecycle -- no in-flight-order allocation tracking exists for it (app/lifecycle/manager.py's PendingEntry/PendingExit only cover managed-lifecycle positions).",
          }),
          capabilityBadgeHtml({
            status: "not_tracked",
            reason: "This account is not managed-lifecycle -- no stop-confirmation/coverage tracking exists for it at all.",
          }),
          fmtNum(p.net_quantity),
          capabilityBadgeHtml({
            status: "not_tracked",
            reason: "No protected-quantity concept exists for a non-managed-lifecycle position to diff Internal qty against, and Broker qty is unavailable too (see above) -- there is no real quantity left to compute a Difference from.",
          }),
        ]);
      }
      StateMatrix.render(els.comparison, {
        state: "ready",
        html: `<p class="section-note">Internal/Allocation/Protected/Possible-closing qty are this service's own real tracked state (app/lifecycle/manager.py). Broker qty is honestly "Not tracked" for every row -- expand each badge for the exact reason (a readback-capable broker whose live read simply isn't exposed by any GET endpoint here, vs. a broker with no readback capability at all). Difference is Internal minus Protected (the real, already-tracked uncovered quantity) for managed-lifecycle rows -- a genuine Broker-vs-Internal diff isn't shown since Broker qty can't be sourced. Any nonzero Difference is highlighted.</p>${table(
          ["Account", "Symbol", "Broker qty", "Internal qty", "Allocation qty", "Protected qty", "Possible closing qty", "Difference"],
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
        html: `<p class="section-note">Real containment: this build's CloseArbiter refuses new close/exit attempts for a halted (account, symbol) rather than let two conflicting close paths race -- see app/lifecycle/close_arbiter.py's module docstring. This is the actual containment mechanism, not a UI-only label.</p>${table(
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

    // --- Actions: the ONE real, specific recovery action this build has
    // (per-position close, wired directly on halted/protection-deficit
    // rows above) is documented here rather than duplicated; everything
    // else genuinely has no backing capability. ---
    StateMatrix.render(els.actions, {
      state: "ready",
      html: `<p class="section-note">The real, specific recovery action available in this build is <strong>"Close this position now"</strong> (POST /positions/{account}/{symbol}/close, app/main.py's close_single_position) -- wired directly on each halted/protection-deficit row in Prioritized incidents above, gated through the same preview -> impact -> confirm -> result flow as every other trade-affecting command in this dashboard. It is per-position and specific, never a generic "reconcile everything".</p>
        ${capabilityBadgeHtml({
          status: "unsupported",
          reason:
            "Acknowledge/assign an incident and a manual 'run reconciliation now' trigger have no backing capability in this build: no incident_id/acknowledge-assign-resolve persistence exists anywhere in app/db.py, and app/reconciliation.py's OrderReconciler runs on a fixed background interval with no HTTP trigger route.",
        })}`,
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
