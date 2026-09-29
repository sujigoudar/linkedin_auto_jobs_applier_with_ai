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
 *
 * --- Phase B7: Capital utilization (this batch) ---
 *
 * app/capital_allocator.py's `CapitalAllocator` (E03's per-account notional
 * admission gate) was, before this batch, process-internal only -- used by
 * app/engine.py's admission-control path, never surfaced via any GET
 * endpoint (confirmed by reading that module in full before writing any of
 * this). This batch adds exactly ONE new, narrowly-scoped, read-only
 * endpoint, `GET /capital-allocation`, exposing that module's own real
 * current state (nothing else in it changed) -- see its docstring in
 * app/main.py for the exact field-by-field provenance.
 *
 *   - Capital state (current): a real, point-in-time breakdown of
 *     deployed/reserved/ceiling/available notional per account, straight
 *     from `GET /capital-allocation`. `deployed_notional` is
 *     `confirmed_open_notional` (real confirmed-fill exposure, the same
 *     figure the E03 gate itself reads); `reserved_notional` is the
 *     allocator's real in-memory provisional reservation for orders not
 *     yet resolved to a terminal status (process-lifetime only, resets on
 *     a restart -- never a persisted ledger); `max_notional_exposure`/
 *     `available_notional` are `null` for any account that has not opted
 *     into an exposure ceiling (E03's default) -- never a guessed capacity
 *     number.
 *   - Capital utilization over time: this codebase has NO persisted
 *     capital/margin/buying-power time series anywhere (Phase B1's own
 *     investigation, re-confirmed here: `GET /accounts/{id}/balance` is a
 *     live, CURRENT-only broker read; `capital_allocator.py`'s reservations
 *     are in-memory and were, until this batch, not even readable). So
 *     rather than fabricate a capital-over-time series, this chart plots
 *     Phase A3's real, persisted `cumulative_pnl` history (GET
 *     /accounts/{id}/equity-history) as an honest PROXY axis, clearly
 *     labeled as historical P&L (never "capital"), alongside the real
 *     current-only capital-state snapshot rendered as a separate panel
 *     right below it -- never combined into one dual-axis chart (this
 *     codebase's own dataviz convention forbids two y-scales on one plot,
 *     and here it would additionally blur a real historical series
 *     together with a real but non-historical one).
 *   - Return on deployed capital: NOT built. This needs a real, non-
 *     fabricated capital DENOMINATOR to divide `cumulative_pnl` by.
 *     Per Phase A5's own investigation (app/equity_history.py's module
 *     docstring, re-checked here): `DestinationAccount`/`AccountRequest`
 *     have no `starting_balance`/`starting_capital` field at all -- there
 *     is no real baseline capital figure anywhere in this schema. The only
 *     real capital figure this batch newly exposes,
 *     `deployed_notional`, is a CURRENT-only snapshot, not a starting
 *     baseline -- dividing an all-time accumulated `cumulative_pnl` by a
 *     single current point-in-time denominator would silently conflate a
 *     historical numerator with a non-historical denominator (the ratio
 *     would swing every time open notional changes for reasons that have
 *     nothing to do with return), which is exactly the kind of arbitrary
 *     number this project's other modules refuse to produce. Rendered as
 *     an honest "unsupported" panel instead.
 *   - Deliberately NOT built (each needs infrastructure this codebase does
 *     not have, confirmed rather than assumed):
 *       - Idle cash over time: needs a persisted cash/buying-power time
 *         series (same gap as above) -- `GET /accounts/{id}/balance` is
 *         current-only and this build tracks no idle-cash concept at all.
 *       - Capital by provider: needs per-provider capital attribution.
 *         app/statistics.py's own module docstring (Phase B4/B5's
 *         concurrent investigation) confirms this codebase has no
 *         per-provider/per-analyst equity or capital allocation anywhere --
 *         only per-ACCOUNT figures exist.
 *       - Opportunity queue vs. available capital: there is no signal
 *         "opportunity queue" concept anywhere in this codebase (a signal
 *         is either routed and acted on immediately by app/engine.py, or
 *         it isn't -- nothing is queued/backlogged for later capital
 *         availability), so there is no real queue depth to plot against
 *         `available_notional`.
 *       - Capital Sankey (source -> account -> position flows): needs a
 *         real capital-FLOW ledger (which reservation/fill funded which
 *         specific position, over time) -- this build tracks current
 *         aggregate notional only, never a flow graph between capital
 *         pools and positions.
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
      <section class="tr-panel" id="tr02-p05"><h2>Capital state (current)</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr02-p06"><h2>Capital utilization over time</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr02-p07"><h2>Return on deployed capital</h2><div class="tr-panel-body"></div></section>
    `;
  }

  // Same fixed, dataviz-skill-validated categorical palette tr01.js already
  // established for this app (dark surface --panel #141926) -- reused here
  // rather than inventing a second one. Deployed/reserved are two fixed
  // series (identity, not rank), so each keeps one stable palette slot.
  const DEPLOYED_COLOR = "#3987e5";
  const RESERVED_COLOR = "#d95926";
  const PNL_LINE_COLOR = "#199e70";

  let capitalBarChart = null;
  let pnlProxyChart = null;

  // --- Saved views (TR-02 Saved views panel) ---
  //
  // Real, persisted named filter sets (GET/POST/DELETE /saved-views,
  // app/db.py's `saved_views` table) -- but this screen still has NO real
  // server-side query-param filtering (see the scope-controls note
  // rendered below, unchanged from before this batch): `GET /positions`
  // always returns the full authorized snapshot. So a "saved view" here
  // is honestly a CLIENT-side filter-state blob: the exact real values of
  // this screen's own Account/Instrument/Side controls at save time,
  // re-applied to the already-fetched `positions`/`managed_lifecycles`
  // rows on "Apply" -- never a server-side query this endpoint doesn't
  // actually perform.
  let filterState = { account_id: "", symbol: "", side: "" };
  let cachedPositions = [];
  let cachedLifecycleByKey = new Map();

  function applyFilters(positions) {
    return positions.filter((p) => {
      if (filterState.account_id && p.account_id !== filterState.account_id) return false;
      if (filterState.symbol && !p.symbol.toUpperCase().includes(filterState.symbol.toUpperCase())) return false;
      if (filterState.side) {
        const sign = p.net_quantity > 0 ? "long" : p.net_quantity < 0 ? "short" : "flat";
        if (sign !== filterState.side) return false;
      }
      return true;
    });
  }

  function renderCapitalStateBar(wrap, accounts) {
    if (capitalBarChart) {
      capitalBarChart.destroy();
      capitalBarChart = null;
    }
    if (!wrap) return;
    if (!accounts.length) {
      wrap.innerHTML = `<div class="empty">No configured accounts.</div>`;
      return;
    }
    wrap.innerHTML = `<div class="chart-container" style="height:240px;"><canvas id="tr02-capital-bar"></canvas></div>`;
    capitalBarChart = new Chart(wrap.querySelector("#tr02-capital-bar").getContext("2d"), {
      type: "bar",
      data: {
        labels: accounts.map((a) => a.account_id),
        datasets: [
          { label: "Deployed (confirmed open notional)", data: accounts.map((a) => a.deployed_notional), backgroundColor: DEPLOYED_COLOR },
          { label: "Reserved (in-flight, provisional)", data: accounts.map((a) => a.reserved_notional), backgroundColor: RESERVED_COLOR },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "bottom", labels: { color: "#e6e9f0", boxWidth: 12, font: { size: 11 } } },
        },
        scales: { y: { beginAtZero: true } },
      },
    });
  }

  function renderPnlProxyChart(wrap, seriesByAccount) {
    if (pnlProxyChart) {
      pnlProxyChart.destroy();
      pnlProxyChart = null;
    }
    if (!wrap) return;
    const accountIds = Object.keys(seriesByAccount).filter((id) => seriesByAccount[id].length);
    if (!accountIds.length) {
      wrap.innerHTML = `<div class="empty">No real equity-history snapshots yet for any account (app/equity_history.py's EquitySnapshotter has not captured one) -- no fabricated proxy series shown.</div>`;
      return;
    }
    wrap.innerHTML = `<div class="chart-container" style="height:260px;"><canvas id="tr02-pnl-proxy-chart"></canvas></div>`;
    const colorFor = (i) => (accountIds.length === 1 ? PNL_LINE_COLOR : [PNL_LINE_COLOR, DEPLOYED_COLOR, RESERVED_COLOR, "#c98500", "#d55181", "#008300"][i % 6]);
    // No date-adapter vendor bundle is available in this build (see
    // app/static/vendor/README.md) -- same reason tr15.js's own equity-curve
    // chart uses a plain category axis of real captured_at strings rather
    // than Chart.js's "time" scale, which requires one. All series are
    // merged onto one shared, de-duplicated, sorted label set of real
    // captured_at values so every account's line lands on the correct label.
    const labelSet = new Set();
    for (const id of accountIds) for (const s of seriesByAccount[id]) labelSet.add(s.captured_at);
    const labels = Array.from(labelSet).sort();
    pnlProxyChart = new Chart(wrap.querySelector("#tr02-pnl-proxy-chart").getContext("2d"), {
      type: "line",
      data: {
        labels,
        datasets: accountIds.map((accountId, i) => {
          const byLabel = new Map(seriesByAccount[accountId].map((s) => [s.captured_at, s.cumulative_pnl]));
          return {
            label: `${accountId} -- cumulative P&L (proxy, historical)`,
            data: labels.map((l) => (byLabel.has(l) ? byLabel.get(l) : null)),
            borderColor: colorFor(i),
            backgroundColor: "transparent",
            tension: 0,
            pointRadius: 2,
            spanGaps: true,
            fill: false,
          };
        }),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "bottom", labels: { color: "#e6e9f0", boxWidth: 12, font: { size: 11 } } },
        },
        scales: { y: { beginAtZero: false } },
      },
    });
  }

  function renderGridAndExposure(els) {
    const positions = applyFilters(cachedPositions);
    if (!cachedPositions.length) {
      StateMatrix.render(els.grid, {
        state: "empty",
        emptyMessage: "No verified positions in this scope.",
        nextRoute: "/trade",
        nextLabel: "Go to Trading command center",
      });
      StateMatrix.render(els.exposure, { state: "empty", emptyMessage: "No verified positions in this scope." });
      return;
    }
    if (!positions.length) {
      StateMatrix.render(els.grid, {
        state: "empty",
        emptyMessage: "No positions match the current filters.",
      });
      StateMatrix.render(els.exposure, { state: "empty", emptyMessage: "No positions match the current filters." });
      return;
    }
    const rows = positions.map((p) => {
      const lc = cachedLifecycleByKey.get(`${p.account_id}::${p.symbol}`);
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
        "No positions match the current filters."
      ),
    });

    const filteredKeys = new Set(positions.map((p) => `${p.account_id}::${p.symbol}`));
    const unresolved = Array.from(cachedLifecycleByKey.entries()).filter(
      ([key, l]) => filteredKeys.has(key) && (l.pending_exit || l.pending_entry)
    ).length;
    StateMatrix.render(els.exposure, {
      state: "ready",
      html: `<div class="econ-stats">
               <div><span class="muted">M-TR-02-01 Known position count</span><br><span class="num">${positions.length}</span></div>
               <div><span class="muted">M-TR-02-02 Unresolved positions</span><br><span class="num">${unresolved}</span></div>
             </div>`,
    });
  }

  function renderScope(els, accounts) {
    const accountOptions = accounts
      .map((a) => `<option value="${escapeHtml(a.account_id)}"${filterState.account_id === a.account_id ? " selected" : ""}>${escapeHtml(a.account_id)}</option>`)
      .join("");
    StateMatrix.render(els.scope, {
      state: "ready",
      html: `<p class="section-note">Read/navigation page. These filters run entirely CLIENT-side over the full authorized snapshot GET /positions already returns (Analyst/Product/Protection are not real fields on a position in this schema -- see this file's module docstring -- so only Account/Instrument/Side are filterable here); no real server-side query-param filtering exists yet.</p>
             <form class="inline-form" onsubmit="return false;">
               <label>Account
                 <select id="tr02-filter-account"><option value="">All accounts</option>${accountOptions}</select>
               </label>
               <label>Instrument
                 <input id="tr02-filter-symbol" type="text" placeholder="e.g. AAPL" value="${escapeHtml(filterState.symbol)}" autocomplete="off">
               </label>
               <label>Side
                 <select id="tr02-filter-side">
                   <option value="">All sides</option>
                   <option value="long"${filterState.side === "long" ? " selected" : ""}>Long</option>
                   <option value="short"${filterState.side === "short" ? " selected" : ""}>Short</option>
                   <option value="flat"${filterState.side === "flat" ? " selected" : ""}>Flat</option>
                 </select>
               </label>
               <span class="actions"><button type="button" class="ghost" id="tr02-filter-clear">Clear filters</button></span>
             </form>
             <p>Accounts in scope: ${accounts.length ? accounts.map((a) => `<span class="mono">${escapeHtml(a.account_id)}</span>`).join(", ") : "<em>none configured</em>"}</p>`,
    });
    const accountSel = els.scope.querySelector("#tr02-filter-account");
    const symbolInput = els.scope.querySelector("#tr02-filter-symbol");
    const sideSel = els.scope.querySelector("#tr02-filter-side");
    const clearBtn = els.scope.querySelector("#tr02-filter-clear");
    if (accountSel) accountSel.addEventListener("change", () => { filterState.account_id = accountSel.value; renderGridAndExposure(els); });
    if (symbolInput) symbolInput.addEventListener("input", () => { filterState.symbol = symbolInput.value; renderGridAndExposure(els); });
    if (sideSel) sideSel.addEventListener("change", () => { filterState.side = sideSel.value; renderGridAndExposure(els); });
    if (clearBtn)
      clearBtn.addEventListener("click", () => {
        filterState = { account_id: "", symbol: "", side: "" };
        renderScope(els, accounts);
        renderGridAndExposure(els);
      });
  }

  // Real GET/POST/DELETE /saved-views calls -- these are real mutating
  // (POST/DELETE) requests, so they go through the SAME `postJSON`/
  // `deleteJSON` helpers (dashboard.html's globals, shared by every other
  // routed view's script tag on this page) every other mutating control in
  // this app uses -- CSRF-token-attached, session-expiry-aware -- never a
  // bespoke fetch call that would skip that handling. `ctx.fetchJSON` (the
  // non-throwing GET-only helper) is used for the read.
  async function renderSavedViews(ctx, els) {
    StateMatrix.render(els.saved, { state: "loading" });
    const res = await ctx.fetchJSON("/saved-views?screen=positions");
    if (res.status === 401 || res.status === 403) {
      StateMatrix.render(els.saved, { state: "denied", deniedCode: res.status });
      return;
    }
    if (!res.ok) {
      StateMatrix.render(els.saved, { state: "error", message: "Could not load saved views." });
      return;
    }
    const views = (res.data && res.data.saved_views) || [];
    const rows = views.map((v) => [
      escapeHtml(v.name),
      `<span class="muted">account=${escapeHtml(v.filters.account_id || "any")}, symbol=${escapeHtml(v.filters.symbol || "any")}, side=${escapeHtml(v.filters.side || "any")}</span>`,
      `<button type="button" class="tr02-apply-view" data-id="${v.id}">Apply</button>
       <button type="button" class="tr02-delete-view" data-id="${v.id}">Delete</button>`,
    ]);
    StateMatrix.render(els.saved, {
      state: "ready",
      html: `<p class="section-note">Real, persisted named filter sets (GET/POST/DELETE /saved-views, app/db.py's saved_views table). Since this screen has no real server-side query-param filtering yet, "Apply" re-populates the Account/Instrument/Side controls above from the saved view's real stored filter state and re-filters the already-fetched positions -- it does not re-query the server.</p>
             <form class="inline-form" onsubmit="return false;">
               <label>Name
                 <input id="tr02-save-name" type="text" placeholder="Name this view" autocomplete="off">
               </label>
               <span class="actions"><button type="button" id="tr02-save-current">Save current filters</button></span>
             </form>
             ${table(["Name", "Filters", ""], rows, "No saved views yet.")}`,
    });

    const saveBtn = els.saved.querySelector("#tr02-save-current");
    const nameInput = els.saved.querySelector("#tr02-save-name");
    if (saveBtn) {
      saveBtn.addEventListener("click", async () => {
        const name = (nameInput.value || "").trim();
        if (!name) return;
        saveBtn.disabled = true;
        try {
          await postJSON("/saved-views", { name, screen: "positions", filters: { ...filterState } });
          await renderSavedViews(ctx, els);
        } catch (err) {
          StateMatrix.render(els.saved, { state: "error", message: `Could not save this view: ${err.message}` });
        } finally {
          saveBtn.disabled = false;
        }
      });
    }
    els.saved.querySelectorAll(".tr02-apply-view").forEach((btn) => {
      btn.addEventListener("click", () => {
        const v = views.find((view) => String(view.id) === btn.dataset.id);
        if (!v) return;
        filterState = { account_id: v.filters.account_id || "", symbol: v.filters.symbol || "", side: v.filters.side || "" };
        renderScope(els, els.__accounts || []);
        renderGridAndExposure(els);
      });
    });
    els.saved.querySelectorAll(".tr02-delete-view").forEach((btn) => {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          await deleteJSON(`/saved-views/${encodeURIComponent(btn.dataset.id)}`);
          await renderSavedViews(ctx, els);
        } catch (err) {
          StateMatrix.render(els.saved, { state: "error", message: `Could not delete this view: ${err.message}` });
        }
      });
    });
  }

  async function load(ctx) {
    const els = {
      scope: ctx.container.querySelector("#tr02-p01 .tr-panel-body"),
      grid: ctx.container.querySelector("#tr02-p02 .tr-panel-body"),
      exposure: ctx.container.querySelector("#tr02-p03 .tr-panel-body"),
      saved: ctx.container.querySelector("#tr02-p04 .tr-panel-body"),
      capitalState: ctx.container.querySelector("#tr02-p05 .tr-panel-body"),
      capitalOverTime: ctx.container.querySelector("#tr02-p06 .tr-panel-body"),
      returnOnCapital: ctx.container.querySelector("#tr02-p07 .tr-panel-body"),
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
    els.__accounts = accounts;
    renderScope(els, accounts);

    cachedPositions = (positionsRes.data && positionsRes.data.positions) || [];
    const lifecycles = (positionsRes.data && positionsRes.data.managed_lifecycles) || [];
    cachedLifecycleByKey = new Map(lifecycles.map((l) => [`${l.account_id}::${l.symbol}`, l]));
    renderGridAndExposure(els);

    await renderSavedViews(ctx, els);

    // --- Phase B7: Capital utilization (see this file's module docstring
    // for full provenance/scope) ---
    const capitalRes = await ctx.fetchJSON("/capital-allocation");
    if (capitalRes.status === 401 || capitalRes.status === 403) {
      StateMatrix.render(els.capitalState, { state: "denied", deniedCode: capitalRes.status });
      StateMatrix.render(els.capitalOverTime, { state: "denied", deniedCode: capitalRes.status });
    } else if (!capitalRes.ok) {
      StateMatrix.render(els.capitalState, { state: "error", message: "Could not load capital-allocation state." });
      StateMatrix.render(els.capitalOverTime, { state: "error", message: "Could not load capital-allocation state." });
    } else {
      const capAccounts = (capitalRes.data && capitalRes.data.accounts) || [];
      if (!capAccounts.length) {
        StateMatrix.render(els.capitalState, { state: "empty", emptyMessage: "No configured accounts." });
      } else {
        const rows = capAccounts.map((a) => [
          `<span class="mono">${escapeHtml(a.account_id)}</span>`,
          `<span class="num">${fmtNum(a.deployed_notional)}</span>`,
          `<span class="num">${fmtNum(a.reserved_notional)}</span>`,
          a.max_notional_exposure === null || a.max_notional_exposure === undefined
            ? pill("no ceiling configured", "muted")
            : `<span class="num">${fmtNum(a.max_notional_exposure)}</span>`,
          a.available_notional === null || a.available_notional === undefined
            ? `<span class="tr-not-exposed">not computable (no ceiling configured)</span>`
            : `<span class="num">${fmtNum(a.available_notional)}</span>`,
        ]);
        StateMatrix.render(els.capitalState, {
          state: "ready",
          html: `<p class="section-note">Real, CURRENT-only snapshot from GET /capital-allocation (app/capital_allocator.py's own state, newly exposed this batch) -- never a historical series. Deployed is confirmed-fill exposure; reserved is this process's real in-memory provisional reservation for orders not yet resolved (resets on a restart, never persisted). Ceiling/available are per-account opt-in -- most accounts have none configured.</p>
                 <div id="tr02-capital-bar-wrap"></div>
                 ${table(["Account", "Deployed", "Reserved", "Ceiling", "Available"], rows, "No configured accounts.")}`,
        });
        renderCapitalStateBar(els.capitalState.querySelector("#tr02-capital-bar-wrap"), capAccounts);
      }

      const pnlHistories = await Promise.all(
        accounts.map((a) => ctx.fetchJSON(`/accounts/${encodeURIComponent(a.account_id)}/equity-history?limit=2000`))
      );
      const anyDenied = pnlHistories.some((r) => r.status === 401 || r.status === 403);
      if (anyDenied) {
        StateMatrix.render(els.capitalOverTime, { state: "denied", deniedCode: 403 });
      } else if (!accounts.length) {
        StateMatrix.render(els.capitalOverTime, { state: "empty", emptyMessage: "No configured accounts." });
      } else {
        const seriesByAccount = {};
        accounts.forEach((a, i) => {
          const r = pnlHistories[i];
          seriesByAccount[a.account_id] = r.ok && r.data && r.data.snapshots ? r.data.snapshots : [];
        });
        StateMatrix.render(els.capitalOverTime, {
          state: "ready",
          html: `<p class="section-note">This codebase has no persisted capital/margin/buying-power time series (GET /accounts/{id}/balance is a live, CURRENT-only broker read; app/capital_allocator.py's reservations are in-memory only). The line below is instead a real, HISTORICAL proxy -- each account's persisted <code>cumulative_pnl</code> series (GET /accounts/{id}/equity-history, Phase A3) -- clearly labeled as P&amp;L, never capital. The capital-state panel above it is the real but CURRENT-only figure; the two are shown separately, never combined into one dual-axis chart.</p>
                 <div id="tr02-pnl-proxy-chart-wrap"></div>`,
        });
        renderPnlProxyChart(els.capitalOverTime.querySelector("#tr02-pnl-proxy-chart-wrap"), seriesByAccount);
      }
    }

    StateMatrix.render(els.returnOnCapital, {
      state: "unsupported",
      reason:
        "Not available in this build: computing a return would need a real, non-fabricated starting/baseline capital denominator to divide cumulative_pnl by. This schema's DestinationAccount/AccountRequest have no starting_balance/starting_capital field at all (confirmed absent -- see app/equity_history.py's own module docstring), and the only real capital figure this build tracks (deployed/reserved notional) is a CURRENT-only snapshot, not a baseline -- dividing an all-time P&L figure by a today-only number would be an arbitrary ratio, not a real return on capital.",
    });

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
