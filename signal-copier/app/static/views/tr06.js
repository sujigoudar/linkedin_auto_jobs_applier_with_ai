/* TR-06: Orders, fills and commands (`#/trade/orders`).
 *
 * Real backing data: GET /orders (every recorded OrderResult -- the same
 * endpoint TR-01/TR-03 already read). There is no separate "command"
 * table in this schema distinct from the order row itself -- app/engine.py
 * processes a routed signal synchronously per destination account and
 * writes exactly one `orders` row per attempt with whatever status the
 * broker adapter returned (pending/filled/rejected/error) -- so "Command
 * queue" (P01) is the full order list read AS the record of every command
 * this service issued (columns match the spec's own Intent/Account/
 * Instrument/Purpose/Broker ID/Acknowledged/Filled/Remaining/Outcome
 * list, mapped onto what an order row actually carries), and "Unknown
 * outcome queue" (P03) is the real, narrower subset with status='pending'
 * -- per app/reconciliation.py's own docstring, a PENDING order's outcome
 * is genuinely not yet confirmed by the broker (this service recorded an
 * optimistic guess) until the reconciler loop resolves it, which is
 * exactly "UNKNOWN is not rejected" from this screen's acceptance line.
 *
 * Honest gaps, disclosed rather than worked around:
 *   - "Purpose" (why this order was placed -- entry/stop/target/manual
 *     exit) is not a tracked field distinct from the order's free-text
 *     `message` -- shown as "not tracked (see message)", same convention
 *     TR-03 already uses for this exact column.
 *   - Filters: Account and Status are real (client-side, over the fetched
 *     page, same limitation TR-04 already discloses for its own filters).
 *     Family and Purpose have no queryable backing field in this build --
 *     omitted rather than faked; a family grouping is instead offered
 *     locally per-row via "Open family" (TR-06-A01), which groups already-
 *     fetched orders by signal_id (the closest real "family" concept this
 *     schema has: every order sharing one signal_id came from the same
 *     originating instruction).
 *   - TR-06-A02 "Request outcome reconciliation" has NO owner-facing HTTP
 *     action anywhere in this codebase -- app/reconciliation.py's
 *     OrderReconciler only runs on its own background schedule (see its
 *     `start`/`_run_loop`), with no route to trigger a pass on demand.
 *     Rendered "unsupported" rather than faked, same treatment TR-03 gives
 *     its own three unimplemented actions.
 *   - "Correlations" (P04, chart) has no verified report snapshot/
 *     definition IDs backing it in this build -- no plot when data is
 *     absent, per the panel's own contract; rendered unsupported.
 */
(function () {
  "use strict";

  function shell() {
    return `
      <section class="tr-panel" id="tr06-p01"><h2>Command queue</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr06-p02"><h2>Orders/fills tabs</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr06-p03"><h2>Unknown outcome queue</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr06-p04"><h2>Correlations</h2><div class="tr-panel-body"></div></section>
    `;
  }

  function outcomePill(status) {
    if (status === "filled") return pill("filled", "ok");
    if (status === "pending") return pill("pending / unknown", "warn");
    return pill(status || "—", "bad");
  }

  function commandRow(o) {
    const remaining =
      o.requested_quantity === null || o.requested_quantity === undefined || o.filled_quantity === null || o.filled_quantity === undefined
        ? "—"
        : fmtNum(o.requested_quantity - o.filled_quantity);
    return [
      `<a class="mono" href="#" data-open-family="${escapeAttr(String(o.signal_id))}">${escapeHtml(o.side || "—")}</a>`,
      `<span class="mono">${escapeHtml(o.account_id)}</span>`,
      `<span class="mono">${escapeHtml(o.symbol || "—")}</span>`,
      `<span class="tr-not-tracked">not tracked (see message)</span>`,
      `<span class="mono">${escapeHtml(o.broker_order_id || "—")}</span>`,
      boolPill(o.status === "filled" || o.status === "pending", "acknowledged", "not acknowledged"),
      fmtNum(o.filled_quantity),
      remaining,
      outcomePill(o.status),
    ];
  }

  function applyFilters(orders, filters) {
    return orders.filter((o) => {
      if (filters.account && o.account_id !== filters.account) return false;
      if (filters.status && o.status !== filters.status) return false;
      return true;
    });
  }

  async function load(ctx, filters, tab) {
    filters = filters || {};
    tab = tab || "orders";
    const els = {
      queue: ctx.container.querySelector("#tr06-p01 .tr-panel-body"),
      tabs: ctx.container.querySelector("#tr06-p02 .tr-panel-body"),
      unknown: ctx.container.querySelector("#tr06-p03 .tr-panel-body"),
      correlations: ctx.container.querySelector("#tr06-p04 .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const ordersRes = await ctx.fetchJSON("/orders?limit=500");
    if (ordersRes.status === 401 || ordersRes.status === 403) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "denied", deniedCode: ordersRes.status });
      return;
    }
    if (!ordersRes.ok) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "error", message: "Could not load orders." });
      return;
    }

    const allOrders = (ordersRes.data && ordersRes.data.orders) || [];
    const orders = applyFilters(allOrders, filters);

    StateMatrix.render(els.correlations, {
      state: "unsupported",
      reason: "No verified correlation report snapshot with definition IDs exists in this build -- no plot is shown rather than one built from unverified data.",
    });

    if (!allOrders.length) {
      StateMatrix.render(els.queue, {
        state: "empty",
        emptyMessage: "No order or command records in this scope.",
        nextRoute: "/trade/signals",
        nextLabel: "Incoming signal stream (TR-04)",
      });
      StateMatrix.render(els.tabs, { state: "empty", emptyMessage: "No order or command records in this scope." });
      StateMatrix.render(els.unknown, { state: "empty", emptyMessage: "No order or command records in this scope." });
      return;
    }

    const accounts = [...new Set(allOrders.map((o) => o.account_id))];
    const statuses = [...new Set(allOrders.map((o) => o.status))];
    const filterForm = `
      <form id="tr06-filter-form" class="inline-form" style="margin:0;">
        <label>Account
          <select name="account">
            <option value="">(any)</option>
            ${accounts.map((a) => `<option value="${escapeAttr(a)}" ${filters.account === a ? "selected" : ""}>${escapeHtml(a)}</option>`).join("")}
          </select>
        </label>
        <label>Status
          <select name="status">
            <option value="">(any)</option>
            ${statuses.map((s) => `<option value="${escapeAttr(s)}" ${filters.status === s ? "selected" : ""}>${escapeHtml(s)}</option>`).join("")}
          </select>
        </label>
        <div class="actions"><button type="submit">Apply</button> <button type="button" class="ghost" id="tr06-clear-filters">Clear</button></div>
      </form>
      <p class="section-note">Filters: Account and Status are applied client-side over the fetched page. Family and Purpose have no queryable field in this build -- use "Open family" on a row instead (groups by originating signal_id, the closest real family concept this schema has). Time filtering is not yet wired.</p>
    `;

    if (!orders.length) {
      StateMatrix.render(els.queue, { state: "empty", emptyMessage: "No order or command records in this scope.", nextRoute: "/trade/signals", nextLabel: "Incoming signal stream (TR-04)" });
    } else {
      StateMatrix.render(els.queue, {
        state: "ready",
        html: `${filterForm}${table(
          ["Intent", "Account", "Instrument", "Purpose", "Broker ID", "Acknowledged", "Filled", "Remaining", "Outcome"],
          orders.map(commandRow),
          "No commands."
        )}<div id="tr06-family-detail"></div>`,
      });
      const form = els.queue.querySelector("#tr06-filter-form");
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        const f = new FormData(form);
        load(ctx, { account: f.get("account") || "", status: f.get("status") || "" }, tab);
      });
      els.queue.querySelector("#tr06-clear-filters").addEventListener("click", () => load(ctx, {}, tab));
      els.queue.querySelectorAll("[data-open-family]").forEach((a) => {
        a.addEventListener("click", (e) => {
          e.preventDefault();
          const signalId = a.getAttribute("data-open-family");
          const siblings = allOrders.filter((o) => String(o.signal_id) === signalId);
          const detailEl = els.queue.querySelector("#tr06-family-detail");
          detailEl.innerHTML = `<h3 class="section-note">Family for signal <a href="#/trade/signals/${encodeURIComponent(signalId)}">${escapeHtml(signalId)}</a> (${siblings.length} order(s), same read model/selection, presentation only)</h3>${table(
            ["Account", "Symbol", "Status", "Requested", "Filled", "Executed at"],
            siblings.map((o) => [
              escapeHtml(o.account_id),
              escapeHtml(o.symbol || "—"),
              outcomePill(o.status),
              fmtNum(o.requested_quantity),
              fmtNum(o.filled_quantity),
              o.executed_at || "—",
            ]),
            "No sibling orders."
          )}`;
        });
      });
    }

    // --- Orders/fills tabs: same read model, client-side presentational
    // toggle between "all orders" and "fills only" -- no live effect.
    const tabbed = tab === "fills" ? orders.filter((o) => o.status === "filled") : orders;
    if (!tabbed.length) {
      StateMatrix.render(els.tabs, { state: "empty", emptyMessage: tab === "fills" ? "No fills in this scope." : "No orders in this scope." });
    } else {
      const rows = tabbed.map((o) => [
        o.executed_at || "—",
        `<span class="mono">${escapeHtml(o.account_id)}</span>`,
        `<span class="mono">${escapeHtml(o.symbol || "—")}</span>`,
        escapeHtml(o.side || "—"),
        fmtNum(o.filled_quantity),
        o.filled_price === null || o.filled_price === undefined ? "—" : fmtNum(o.filled_price),
        outcomePill(o.status),
      ]);
      StateMatrix.render(els.tabs, {
        state: "ready",
        html: `<div class="tr-controls-row">
                 <button type="button" class="${tab === "orders" ? "" : "ghost"}" id="tr06-tab-orders">All orders</button>
                 <button type="button" class="${tab === "fills" ? "" : "ghost"}" id="tr06-tab-fills">Fills only</button>
               </div>${table(["Executed at", "Account", "Symbol", "Side", "Filled qty", "Filled price", "Status"], rows, "No rows.")}`,
      });
      els.tabs.querySelector("#tr06-tab-orders").addEventListener("click", () => load(ctx, filters, "orders"));
      els.tabs.querySelector("#tr06-tab-fills").addEventListener("click", () => load(ctx, filters, "fills"));
    }

    // --- Unknown outcome queue: status='pending', genuinely unresolved. ---
    const unknown = orders.filter((o) => o.status === "pending");
    if (!unknown.length) {
      StateMatrix.render(els.unknown, { state: "ready", html: `<p class="section-note">No orders with an unresolved (pending, not yet broker-confirmed) outcome in this scope.</p>` });
    } else {
      const rows = unknown.map((o) => [
        `<span class="mono">${escapeHtml(o.broker_order_id || String(o.id))}</span>`,
        `<span class="mono">${escapeHtml(o.account_id)}</span>`,
        `<span class="mono">${escapeHtml(o.symbol || "—")}</span>`,
        o.executed_at || "—",
        pill("UNKNOWN -- pending broker confirmation, not rejected", "warn"),
      ]);
      StateMatrix.render(els.unknown, {
        state: "ready",
        html: `<p class="section-note">Resolved automatically by app/reconciliation.py's background loop once the broker confirms a terminal outcome -- there is no owner-facing action to trigger that pass on demand (see below).</p>${table(
          ["Order", "Account", "Symbol", "Executed at", "Outcome"],
          rows,
          "No unknown-outcome orders."
        )}`,
      });
    }

    const unsupportedActions = document.createElement("div");
    StateMatrix.render(unsupportedActions, {
      state: "unsupported",
      reason: "Request outcome reconciliation (TR-06-A02) has no backing capability in this build -- app/reconciliation.py's OrderReconciler only runs on its own schedule; there is no owner-facing HTTP action to enqueue a readback on demand. Not implemented here rather than faked.",
    });
    els.unknown.appendChild(unsupportedActions);

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr06 = {
    title: "Orders, fills and commands",
    breadcrumb: "Trade / Orders",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = shell();
      await load(ctx, {}, "orders");
      ctx.registerPoll("tr06", 10000, () => load(ctx, {}, "orders"));
    },
  };
  Router.register("/trade/orders", "tr06");
})();
