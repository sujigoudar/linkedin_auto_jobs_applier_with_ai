/* TR-02: Positions and allocations (`#/trade/positions`).
 *
 * Real backing data: GET /positions (both its `positions` list -- every
 * tracked account/symbol/net_quantity/updated_at -- and its
 * `managed_lifecycles` list, joined here by (account_id, symbol) for the
 * richer managed-lifecycle-only fields).
 *
 * The spec's column list is wider than what this schema actually tracks
 * per open position. Rather than invent values for the missing ones, each
 * cell below is either real (labelled with which source it came from) or
 * an explicit "not tracked in this build" -- never a fabricated number:
 *   - Analyst: `positions` has no per-position analyst attribution (only
 *     `signals` rows do, and a position aggregates net quantity across
 *     however many signals contributed to it) -- always "not tracked".
 *   - Side: derived, not stored -- sign of net_quantity (long/short).
 *   - Pending entry / Working stop / State: real, but ONLY for
 *     managed-lifecycle positions (from the joined managed_lifecycles
 *     entry) -- a plain (non-managed) account position has no such
 *     concept in this codebase, so those cells read "not tracked (plain
 *     account)".
 *   - Possible closes: this codebase's CloseArbiter tracks
 *     owned/reserved/available internally (app/lifecycle/close_arbiter.py)
 *     but no GET endpoint exposes the live `available_to_sell` figure --
 *     "not exposed" rather than approximated from owned/uncovered (which
 *     would silently ignore any open reservation).
 *   - P&L basis: GET /accounts/{id}/economics reports REALIZED P&L per
 *     symbol from closed fills, not a live unrealized cost basis for an
 *     open position (see app/economics.py's own documented scope) --
 *     shown as "realized P&L (closed fills), not a live basis" so it's
 *     never mistaken for one.
 */
(function () {
  "use strict";

  function sideOf(netQuantity) {
    if (netQuantity === null || netQuantity === undefined) return pill("unknown", "muted");
    if (netQuantity > 0) return pill("long", "ok");
    if (netQuantity < 0) return pill("short", "bad");
    return pill("flat", "muted");
  }

  function shell() {
    return `
      <section class="tr-panel" id="tr02-p01"><h2>Scope controls</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr02-p02"><h2>Position grid</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr02-p03"><h2>Exposure summary</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr02-p04"><h2>Saved views</h2><div class="tr-panel-body"></div></section>
    `;
  }

  async function load(ctx) {
    const els = {
      scope: ctx.container.querySelector("#tr02-p01 .tr-panel-body"),
      grid: ctx.container.querySelector("#tr02-p02 .tr-panel-body"),
      exposure: ctx.container.querySelector("#tr02-p03 .tr-panel-body"),
      saved: ctx.container.querySelector("#tr02-p04 .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const [accountsRes, positionsRes] = await Promise.all([ctx.fetchJSON("/accounts"), ctx.fetchJSON("/positions")]);

    if (positionsRes.status === 401 || positionsRes.status === 403) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "denied", deniedCode: positionsRes.status });
      return;
    }
    if (!positionsRes.ok) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "error", message: "Could not load positions." });
      return;
    }

    const accounts = (accountsRes.ok && accountsRes.data && accountsRes.data.accounts) || [];
    StateMatrix.render(els.scope, {
      state: "ready",
      html: `<p class="section-note">Read/navigation page. Filters (Account, Analyst, Instrument, Product, Protection) live server-side allowlisted sorting/pagination is not yet wired here -- this batch renders the full authorized snapshot; a later batch may add real query-param filtering once TR-02's own read model exists.</p>
             <p>Accounts in scope: ${accounts.length ? accounts.map((a) => `<span class="mono">${escapeHtml(a.account_id)}</span>`).join(", ") : "<em>none configured</em>"}</p>`,
    });

    const positions = (positionsRes.data && positionsRes.data.positions) || [];
    const lifecycles = (positionsRes.data && positionsRes.data.managed_lifecycles) || [];
    const lifecycleByKey = new Map(lifecycles.map((l) => [`${l.account_id}::${l.symbol}`, l]));

    if (!positions.length) {
      StateMatrix.render(els.grid, {
        state: "empty",
        emptyMessage: "No verified positions in this scope.",
        nextRoute: "/trade",
        nextLabel: "Go to Trading command center",
      });
      StateMatrix.render(els.exposure, { state: "empty", emptyMessage: "No verified positions in this scope." });
    } else {
      const rows = positions.map((p) => {
        const lc = lifecycleByKey.get(`${p.account_id}::${p.symbol}`);
        const pendingEntry = lc && lc.pending_entry ? `${fmtNum(lc.pending_entry.requested_quantity)} pending` : "not tracked (plain account)";
        const workingStop = lc ? (lc.stop_price !== null && lc.stop_price !== undefined ? fmtNum(lc.stop_price) : pill(lc.stop_status || "unprotected", "warn")) : "not tracked (plain account)";
        const stateCell = lc ? (lc.halted ? pill(lc.halt_reason || "halted", "bad") : pill(lc.stop_status || "managed", "ok")) : pill("plain (unmanaged)", "muted");
        return [
          `<span class="mono">${escapeHtml(p.account_id)}</span>`,
          `<span class="mono">${escapeHtml(p.symbol)}</span>`,
          `<span class="tr-not-tracked">not tracked</span>`,
          sideOf(p.net_quantity),
          `<span class="num">${fmtNum(lc ? lc.owned_quantity : p.net_quantity)}</span>`,
          pendingEntry,
          `<span class="tr-not-exposed">not exposed</span>`,
          workingStop,
          `<span class="tr-basis-note">realized P&amp;L (closed fills), not a live basis</span>`,
          stateCell,
          `<a href="#/trade/positions/${encodeURIComponent(p.account_id)}/${encodeURIComponent(p.symbol)}">Open position</a>`,
        ];
      });
      StateMatrix.render(els.grid, {
        state: "ready",
        html: table(
          ["Account", "Instrument ID", "Analyst", "Side", "Owned", "Pending entry", "Possible closes", "Working stop", "P&amp;L basis", "State", ""],
          rows,
          "No verified positions in this scope."
        ),
      });

      const unresolved = lifecycles.filter((l) => l.pending_exit || l.pending_entry).length;
      StateMatrix.render(els.exposure, {
        state: "ready",
        html: `<div class="econ-stats">
                 <div><span class="muted">M-TR-02-01 Known position count</span><br><span class="num">${positions.length}</span></div>
                 <div><span class="muted">M-TR-02-02 Unresolved positions</span><br><span class="num">${unresolved}</span></div>
               </div>`,
      });
    }

    StateMatrix.render(els.saved, { state: "unsupported", reason: "Saved views are not implemented in this build -- no persistence for named filter sets exists yet." });
    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr02 = {
    title: "Positions and allocations",
    breadcrumb: "Trade / Positions",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = shell();
      await load(ctx);
      ctx.registerPoll("tr02", 10000, () => load(ctx));
    },
  };
  Router.register("/trade/positions", "tr02");
})();
