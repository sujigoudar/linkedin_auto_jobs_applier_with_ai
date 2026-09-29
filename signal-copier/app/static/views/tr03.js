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
 */
(function () {
  "use strict";

  function shell() {
    return `
      <section class="tr-panel" id="tr03-p01"><h2>Identity and plan</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr03-p02"><h2>Quantity ledger</h2><div class="tr-panel-body"></div></section>
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
        StateMatrix.render(els.timeline, {
          state: "ready",
          html: `<p class="section-note">Order execution history (newest first) -- this build has no separately tracked stop-revision event log, so this timeline is the order journal, not a full price/stop revision history.</p>${table(
            ["Executed at", "Side", "Status", "Message"],
            timelineRows,
            "No order events."
          )}`,
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
