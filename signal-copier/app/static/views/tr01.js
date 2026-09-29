/* TR-01: Trading command center (`#/trade`).
 *
 * Real backing data, reusing existing endpoints (no new backend route --
 * see this batch's report for why: every panel below is answerable from
 * data app/main.py already exposes):
 *   - Identity/environment, Safety summary: GET /health
 *   - Account risk cards: GET /accounts + GET /accounts/{id}/balance
 *     (M-TR-01-01 "Verified net liquidation" = AccountBalance.equity, the
 *     broker's own live figure -- never summed across accounts with
 *     different brokers/currencies, since AccountBalance has no currency
 *     field to prove they match; shown per-account instead)
 *   - P&L and exposure: GET /accounts/{id}/economics (realized P&L per
 *     account) + GET /positions (open exposure count)
 *   - Priority incidents: GET /positions' managed_lifecycles[].halted
 *     (a real, code-computed halt flag from CloseArbiter -- not invented)
 *   - Recent activity: GET /orders
 *
 * M-TR-01-02 "Reserved risk" has NO backing data anywhere in this
 * codebase (no committed/reserved-risk ledger is exposed by any endpoint
 * -- app/capital_allocator.py's own reservations are internal, not
 * surfaced via any GET route) -- rendered "unsupported" honestly rather
 * than invented.
 */
(function () {
  "use strict";

  function panelShell() {
    return `
      <section class="tr-panel" id="tr01-p01"><h2>Identity / environment</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p02"><h2>Safety summary</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p03"><h2>Account risk cards</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p04"><h2>P&amp;L and exposure</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p05"><h2>Priority incidents</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr01-p06"><h2>Recent activity</h2><div class="tr-panel-body"></div></section>
    `;
  }

  async function load(ctx) {
    const panelEls = {
      identity: ctx.container.querySelector("#tr01-p01 .tr-panel-body"),
      safety: ctx.container.querySelector("#tr01-p02 .tr-panel-body"),
      risk: ctx.container.querySelector("#tr01-p03 .tr-panel-body"),
      pnl: ctx.container.querySelector("#tr01-p04 .tr-panel-body"),
      incidents: ctx.container.querySelector("#tr01-p05 .tr-panel-body"),
      activity: ctx.container.querySelector("#tr01-p06 .tr-panel-body"),
    };
    for (const el of Object.values(panelEls)) StateMatrix.render(el, { state: "loading" });

    const health = await ctx.fetchJSON("/health");
    if (health.status === 401 || health.status === 403) {
      for (const el of Object.values(panelEls)) StateMatrix.render(el, { state: "denied", deniedCode: health.status });
      return;
    }
    if (!health.ok) {
      for (const el of Object.values(panelEls)) StateMatrix.render(el, { state: "error", message: "Could not reach the service." });
      return;
    }
    const h = health.data || {};

    StateMatrix.render(panelEls.identity, {
      state: "ready",
      html: `<p>Environment: <strong>private_execution</strong> (single-owner engine). Access scope: <strong>private_owner</strong>.</p>
             <p class="section-note">This is this service's own record of what it has sent/tracked -- not a live broker read (see each panel's own data source).</p>`,
    });

    const safetyRows = [
      ["Database", boolPill(h.database_ok)],
      ["Price monitor", boolPill(h.price_monitor_ok)],
      ["Order reconciler", boolPill(h.reconciler_ok)],
      ["Provider scout", h.provider_scout_ok === null || h.provider_scout_ok === undefined ? pill("n/a", "muted") : boolPill(h.provider_scout_ok)],
      ["Relay (commercial export)", h.relay_ok === null || h.relay_ok === undefined ? pill("not configured", "muted") : boolPill(h.relay_ok)],
    ];
    StateMatrix.render(panelEls.safety, {
      state: "ready",
      html: table(["Subsystem", "Status"], safetyRows.map(([k, v]) => [escapeHtml(k), v]), "No subsystem status available."),
    });

    const [accountsRes, positionsRes] = await Promise.all([ctx.fetchJSON("/accounts"), ctx.fetchJSON("/positions")]);
    if (accountsRes.status === 401 || accountsRes.status === 403) {
      StateMatrix.render(panelEls.risk, { state: "denied", deniedCode: accountsRes.status });
      StateMatrix.render(panelEls.pnl, { state: "denied", deniedCode: accountsRes.status });
    } else if (!accountsRes.ok) {
      StateMatrix.render(panelEls.risk, { state: "error", message: "Could not load accounts." });
      StateMatrix.render(panelEls.pnl, { state: "error", message: "Could not load accounts." });
    } else {
      const accounts = (accountsRes.data && accountsRes.data.accounts) || [];
      if (!accounts.length) {
        StateMatrix.render(panelEls.risk, {
          state: "empty",
          emptyMessage: "No brokerage account is configured for this workspace.",
          nextRoute: "/trade/positions",
          nextLabel: "Go to Positions and allocations",
        });
        StateMatrix.render(panelEls.pnl, { state: "empty", emptyMessage: "No brokerage account is configured for this workspace." });
      } else {
        const balances = await Promise.all(
          accounts.map((a) => ctx.fetchJSON(`/accounts/${encodeURIComponent(a.account_id)}/balance`))
        );
        const riskRows = accounts.map((a, i) => {
          const b = balances[i].ok ? balances[i].data : null;
          const netLiq = b && b.equity !== null && b.equity !== undefined ? fmtNum(b.equity) : pill("unknown", "muted");
          return [
            `<span class="mono">${escapeHtml(a.account_id)}</span>`,
            `<span class="mono">${escapeHtml(a.broker)}</span>`,
            netLiq,
            boolPill(a.enabled, "enabled", "disabled"),
          ];
        });
        StateMatrix.render(panelEls.risk, {
          state: "ready",
          html: `<p class="section-note">M-TR-01-01: verified net liquidation (broker-reported equity), not summed across accounts -- different accounts may use different brokers/currencies and this build has no cross-account currency normalization.</p>${table(
            ["Account", "Broker", "Verified net liquidation", "Enabled"],
            riskRows,
            "No accounts."
          )}`,
        });

        const economics = await Promise.all(
          accounts.map((a) => ctx.fetchJSON(`/accounts/${encodeURIComponent(a.account_id)}/economics`))
        );
        const positions = (positionsRes.ok && positionsRes.data && positionsRes.data.positions) || [];
        const lifecycles = (positionsRes.ok && positionsRes.data && positionsRes.data.managed_lifecycles) || [];
        const deficitCount = lifecycles.filter((l) => l.uncovered_quantity > 0).length;
        // The snapshot only ever carries a pending_exit/pending_entry while
        // it is still unresolved (app/main.py's _managed_lifecycle_snapshot
        // reads it straight off the lifecycle, which clears each field the
        // moment resolve_pending_exit/resolve_pending_entry finishes) -- so
        // presence alone means "unknown operation," no extra flag to check.
        const unknownOps = lifecycles.filter((l) => l.pending_exit || l.pending_entry).length;
        const pnlRows = accounts.map((a, i) => {
          const e = economics[i].ok ? economics[i].data : null;
          const pnl = e ? fmtNum(e.realized_pnl) : pill("unknown", "muted");
          const cls = e && e.realized_pnl >= 0 ? "ok-text" : "bad-text";
          return [`<span class="mono">${escapeHtml(a.account_id)}</span>`, `<span class="num ${cls}">${pnl}</span>`];
        });
        StateMatrix.render(panelEls.pnl, {
          state: "ready",
          html: `${table(["Account", "Realized P&amp;L"], pnlRows, "No accounts.")}
                 <div class="econ-stats" style="margin-top:10px;">
                   <div><span class="muted">Open positions (all accounts)</span><br><span class="num">${positions.length}</span></div>
                   <div><span class="muted">M-TR-01-03 Protection deficit (allocations uncovered)</span><br><span class="num">${deficitCount}</span></div>
                   <div><span class="muted">M-TR-01-04 Unknown operations</span><br><span class="num">${unknownOps}</span></div>
                 </div>
                 <div class="tr-unsupported-note">${escapeHtml("M-TR-01-02 Reserved risk: not available -- this build exposes no committed/reserved-risk ledger via any read endpoint.")}</div>`,
        });
      }
    }

    if (positionsRes.status === 401 || positionsRes.status === 403) {
      StateMatrix.render(panelEls.incidents, { state: "denied", deniedCode: positionsRes.status });
    } else if (!positionsRes.ok) {
      StateMatrix.render(panelEls.incidents, { state: "error", message: "Could not load incidents." });
    } else {
      const lifecycles = (positionsRes.data && positionsRes.data.managed_lifecycles) || [];
      const halted = lifecycles.filter((l) => l.halted);
      if (!halted.length) {
        StateMatrix.render(panelEls.incidents, { state: "ready", html: `<p class="section-note">No halted managed-lifecycle positions right now.</p>` });
      } else {
        const rows = halted.map((l) => [
          `<span class="mono">${escapeHtml(l.account_id)}</span>`,
          `<span class="mono">${escapeHtml(l.symbol)}</span>`,
          pill(l.halt_reason || "halted", "bad"),
          `<a href="#/trade/positions/${encodeURIComponent(l.account_id)}/${encodeURIComponent(l.symbol)}">Open position</a>`,
        ]);
        StateMatrix.render(panelEls.incidents, { state: "ready", html: table(["Account", "Symbol", "Reason", ""], rows, "No incidents.") });
      }
    }

    const ordersRes = await ctx.fetchJSON("/orders?limit=15");
    if (ordersRes.status === 401 || ordersRes.status === 403) {
      StateMatrix.render(panelEls.activity, { state: "denied", deniedCode: ordersRes.status });
    } else if (!ordersRes.ok) {
      StateMatrix.render(panelEls.activity, { state: "error", message: "Could not load recent activity." });
    } else {
      const orders = (ordersRes.data && ordersRes.data.orders) || [];
      if (!orders.length) {
        StateMatrix.render(panelEls.activity, { state: "empty", emptyMessage: "No recent activity." });
      } else {
        const rows = orders.map((o) => [
          o.executed_at || "—",
          `<span class="mono">${escapeHtml(o.account_id)}</span>`,
          `<span class="mono">${escapeHtml(o.symbol || "—")}</span>`,
          escapeHtml(o.side || "—"),
          o.status === "filled" ? pill("filled", "ok") : o.status === "pending" ? pill("pending", "warn") : pill(o.status || "—", "bad"),
        ]);
        StateMatrix.render(panelEls.activity, { state: "ready", html: table(["Executed at", "Account", "Symbol", "Side", "Status"], rows, "No recent activity.") });
      }
    }

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr01 = {
    title: "Trading command center",
    breadcrumb: "Trade",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = panelShell();
      await load(ctx);
      ctx.registerPoll("tr01", 10000, () => load(ctx));
    },
  };
  Router.register("/trade", "tr01");
})();
