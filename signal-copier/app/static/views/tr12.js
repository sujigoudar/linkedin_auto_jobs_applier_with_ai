/* TR-12: Sizing, stops and profit policies (`#/trade/policies`).
 *
 * This screen's job (per this batch's own instructions) is to make
 * EXISTING policy real and visible/editable where a real config surface
 * already exists -- not to invent new policy dimensions. After reading
 * app/risk.py, app/lifecycle/models.py, app/lifecycle/manager.py and
 * app/engine.py, here's what this build actually enforces:
 *
 *   - SIZING (real, editable): app/risk.py's size_for_account -- an
 *     account-level fixed_quantity wins outright, else the signal's own
 *     quantity * account.multiplier. Layered account -> provider ->
 *     analyst overrides (SettingsOverride, app/providers.py) are real and
 *     ALREADY editable through this build's already-tested
 *     POST /providers/{id} / POST /providers/{id}/analysts/{id} (same
 *     endpoints TR-09/TR-10 use) -- this screen reuses them, adding no
 *     new write capability. account.max_notional_exposure is a real HARD
 *     ceiling (E03, app/engine.py's `_admit_entry`) no override can
 *     exceed -- editable only via the existing POST /accounts (TR-08).
 *   - INITIAL STOP (real, read-only): `initial_stop` is ALWAYS exactly
 *     `signal.stop_loss` -- the provider's own stop, verbatim
 *     (app/engine.py's `_handle_managed_entry`). There is no fallback-
 *     stop recipe registry anywhere in this codebase; a broker without
 *     native-bracket support rejects an entry that carries a stop it
 *     can't embed (see app/engine.py, "cannot embed stop_loss/take_profit
 *     into the entry"). No config knob for this exists to expose.
 *   - TARGETS (real, read-only): a single `signal.take_profit`, if
 *     present, becomes ONE logical SELL target for the full planned
 *     quantity (reduce_fraction=1.0) -- see app/engine.py. No partial-
 *     exit/multi-target recipe config exists.
 *   - TRAILING (verified UNSUPPORTED): app/lifecycle/models.py's
 *     TrailingPolicy and app/lifecycle/manager.py's _update_trailing exist
 *     and are exercised by this codebase's own lifecycle tests, but
 *     nothing anywhere ever CONSTRUCTS a non-null TrailingPolicy on a live
 *     plan (grepped: the only non-test write sites are the persistence
 *     round-trip in app/lifecycle/manager.py, which only reloads a value
 *     that was already there) -- so no live position can currently have
 *     an active trail. Rendered honestly as unsupported (Components.
 *     renderCapabilityState); no simulated entry -> trail workflow is
 *     built for a capability the engine cannot actually perform.
 *   - HOLDING/DEADLINES (verified UNSUPPORTED): `PositionPlan.time_exit`
 *     IS checked and enforced once set (app/lifecycle/manager.py's
 *     check_time_exits, called from app/reconciliation.py's PRO-02 pass)
 *     -- but nothing ever SETS it from a live signal or any config path.
 *     Same treatment: honestly not_tracked, not wired to a fake form.
 *   - VERSIONING (verified UNSUPPORTED): app/db.py's `config_accounts` /
 *     `config_providers` tables are single-current-row state -- no
 *     version/draft/history table exists anywhere in this schema. A saved
 *     override above takes effect immediately; there is no draft/activate
 *     workflow and no history to list. Rendered honestly as not_tracked
 *     rather than a fabricated one-entry "version list".
 *
 * --- 2026-09 design review response: tabbed policy editor ---
 * This build reorganizes the above into 8 tabs (Sizing | Initial
 * protection | Targets | Trailing | Deadlines | Limits | Preview |
 * Versions) -- see `TAB_DEFS` below. Every tab either shows REAL,
 * already-enforced behavior (with a real edit path where one already
 * exists) or an honest Components.renderCapabilityState badge; none
 * invents a new policy dimension the engine doesn't actually have.
 *
 * New in this pass (additive, backend `GET /policies/sizing-preview`,
 * app/main.py): "show expected position size under several representative
 * trades and current account conditions" (Sizing tab) and the "Preview"
 * tab's dry-run both call this ONE new endpoint, which itself calls
 * `app.risk.size_for_account` DIRECTLY -- the exact function
 * `app/engine.py` calls at real signal-admission time -- so neither tab
 * can ever compute a different expected size than the real engine would.
 * (The OLD client-side `sizeForAccount` mirror this file used to carry
 * for the effective-preview panel is removed in this pass in favor of
 * that real backend call -- see this batch's report for the temporary-
 * break verification performed against a real preview-vs-engine
 * comparison test before committing.)
 *
 * The Initial protection tab's "normalized price/risk chart" ("visualize
 * the stop on a normalized price/risk chart") reuses the exact same real
 * `stop_target_events` universe/aggregate this file's stop/target
 * lifecycle analytics panel (bottom of screen, Phase B10/PU-B10, kept
 * unchanged below) already computes -- one shared fetch+aggregate, no
 * duplicate network calls, no synthetic data. It charts each genuine
 * first STOP_PLACED's distance from that position's own real entry_price
 * as a PERCENT of that entry_price (a normalized, cross-symbol-comparable
 * view screen this same absolute-price-unit histogram at the bottom
 * cannot give on its own).
 *
 * --- Stop/target lifecycle analytics (Phase B10, PU-B10) ---
 * The FIRST consumer of Phase A4's real, append-only
 * `GET /positions/{account_id}/{symbol}/stop-events` log (see
 * app/lifecycle/models.py's `StopTargetEventType`: exactly STOP_PLACED,
 * STOP_TIGHTENED, PROTECTION_FAILED, TARGET_HIT exist on this branch --
 * there is no breakeven-move or trailing-activation event type here, and
 * this build does not invent one). That endpoint is scoped to one
 * (account, symbol) at a time, so a cross-position view needs a real
 * universe of positions to query it against and a client-side aggregate
 * on top -- built here rather than as a new backend endpoint (this stays
 * a read-only join over 3 already-existing, already-tested GETs:
 * GET /positions, GET /positions/excursions, GET /orders, plus GET
 * /signals which this screen already fetches).
 *
 *   1. Stop-tightening frequency -- real STOP_TIGHTENED counts per
 *      account, per symbol and overall, summed across every real event
 *      history this screen queries.
 *   2. Protection-failure rate -- real PROTECTION_FAILED count over real
 *      total stop attempts (PROTECTION_FAILED + STOP_PLACED) for the same
 *      universe.
 *   3. Initial-stop-distance distribution -- for a STOP_PLACED event with
 *      a real `previous_price === null` (a genuine FIRST placement, never
 *      a later STOP_TIGHTENED's price -- see this batch's load-bearing
 *      invariant, verified below), the real price distance from that
 *      position's own real `entry_price` (GET /positions'
 *      `managed_lifecycles[].entry_price` for a still-open position, or
 *      GET /positions/excursions' `entry_price` for the most recent
 *      closed episode of that account/symbol otherwise). A placement
 *      whose position has no known real entry_price is excluded from the
 *      distribution and counted separately, honestly, rather than
 *      guessed at.
 *   4. Target-hit rate -- real TARGET_HIT count over the real count of
 *      positions that had an actual target set. "Had a target set" is
 *      answered by a real join: this screen's own real GET /orders rows
 *      carry a real `signal_id` FK (app/db.py's `orders` table) -- the
 *      EARLIEST non-CLOSE order for a given account/symbol is that
 *      position's real entry order (app/engine.py never writes an
 *      `orders` row for a stop or target fill -- only
 *      app/lifecycle/manager.py's `stop_target_events` log does, via
 *      app/engine.py's own `save_order_result` call sites, which fire
 *      only from `_handle_managed_entry`/`_handle_managed_close`/the
 *      plain non-managed path); its `signal_id` is looked up against the
 *      real GET /signals list already fetched by this screen, and that
 *      signal's own real `take_profit` (not null) is what "a target was
 *      set" means -- exactly PositionPlan/signal.take_profit presence,
 *      per this batch's own instructions. A position whose entry
 *      order/signal can't be found this way is excluded from the
 *      denominator and disclosed separately, never silently folded into
 *      either side.
 *   5. Stop-tightening trajectory -- a real per-position drill-down: pick
 *      one real (account, symbol) pair from the universe above and see
 *      its own real STOP_TIGHTENED events (previous_price -> price, at)
 *      in order, from the exact same event list already fetched for the
 *      aggregates above (no extra request).
 *
 * Deliberately NOT built (per this batch's own scope, catalog items
 * explicitly excluded rather than fabricated):
 *   - Breakeven-move / trailing-activation distributions and profit-lock
 *     progression -- StopTargetEventType simply has no such event type on
 *     this branch (see above); the Trailing tab already documents this as
 *     verified-unsupported and this new panel does not repeat a fake
 *     chart for it.
 *   - Stop-efficiency-vs-MFE scatter (stop distance vs. real per-trade
 *     MFE-at-exit) -- buildable in principle (GET /positions/excursions
 *     already carries real `mfe`, and this panel already computes real
 *     initial-stop distances), but doing it honestly needs those two
 *     joined PER EPISODE, and this schema has no episode id linking a
 *     specific STOP_PLACED event to a specific position_excursions row
 *     when the same (account, symbol) has closed and reopened more than
 *     once -- the join used above for target-hit-rate (earliest order)
 *     is a reasonable single-episode approximation, but silently reusing
 *     it here to pair a stop distance with the WRONG episode's MFE would
 *     misrepresent stop efficiency, not just be imprecise. Deferred until
 *     stop_target_events (or position_excursions) carries a real episode
 *     id to join on.
 *   - Target-count distribution -- this codebase only ever constructs
 *     ONE take_profit target per entry (see the Targets tab below); a
 *     "distribution" over an always-1 value would be misleading, so this
 *     panel states that real fact in prose instead of drawing a
 *     pointless chart.
 */
(function () {
  "use strict";

  const charts = { initialStopDistance: null, normalizedStopDistance: null };

  function destroyChart(key) {
    if (charts[key]) {
      charts[key].destroy();
      charts[key] = null;
    }
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

  // Real histogram bucketing over real distances -- no padding, no
  // fabricated bucket if `values` is empty (caller handles that case).
  function histogramBuckets(values, bucketCount) {
    const min = Math.min(...values);
    const max = Math.max(...values);
    if (min === max) return { labels: [fmtNum(min)], counts: [values.length] };
    const width = (max - min) / bucketCount;
    const counts = new Array(bucketCount).fill(0);
    for (const v of values) {
      let idx = Math.floor((v - min) / width);
      if (idx >= bucketCount) idx = bucketCount - 1;
      if (idx < 0) idx = 0;
      counts[idx]++;
    }
    const labels = [];
    for (let i = 0; i < bucketCount; i++) {
      const lo = min + i * width;
      const hi = i === bucketCount - 1 ? max : min + (i + 1) * width;
      labels.push(`${fmtNum(lo)}–${fmtNum(hi)}`);
    }
    return { labels, counts };
  }

  function positionKey(accountId, symbol) {
    return `${accountId}::${symbol}`;
  }

  // Real universe of (account, symbol) pairs to query GET
  // /positions/{account}/{symbol}/stop-events against -- every currently
  // open managed-lifecycle position plus every closed one this account
  // has real excursion history for. `entry_price` prefers the OPEN
  // lifecycle's own real entry_price when the pair is currently open;
  // otherwise the most-recently-closed real excursion's entry_price
  // (GET /positions/excursions is already newest-closed-first) -- honest
  // best-effort when the same pair has closed/reopened more than once
  // (see this file's module docstring on why the stop-efficiency scatter
  // is deferred instead of guessing further here).
  function buildPositionUniverse(openPositions, closedPositions) {
    const universe = new Map();
    for (const p of openPositions) {
      const key = positionKey(p.account_id, p.symbol);
      universe.set(key, { key, account_id: p.account_id, symbol: p.symbol, entry_price: p.entry_price, status: "open" });
    }
    for (const p of closedPositions) {
      const key = positionKey(p.account_id, p.symbol);
      if (universe.has(key)) continue; // an open lifecycle's own entry_price wins
      universe.set(key, { key, account_id: p.account_id, symbol: p.symbol, entry_price: p.entry_price, status: "closed" });
    }
    return [...universe.values()];
  }

  // Real join: the EARLIEST non-CLOSE `orders` row for a given
  // account/symbol is that position's real entry order -- see this
  // file's module docstring for why (app/engine.py never writes an
  // `orders` row for a stop/target action; only stop_target_events does).
  function earliestEntryOrdersByPair(orders) {
    const byPair = new Map();
    for (const o of orders) {
      if (o.side === "close") continue;
      const key = positionKey(o.account_id, o.symbol);
      const existing = byPair.get(key);
      if (!existing || Date.parse(o.executed_at) < Date.parse(existing.executed_at)) {
        byPair.set(key, o);
      }
    }
    return byPair;
  }

  async function fetchStopEventsForUniverse(ctx, pairs) {
    const eventsByKey = new Map();
    await Promise.all(
      pairs.map(async (p) => {
        const res = await ctx.fetchJSON(`/positions/${encodeURIComponent(p.account_id)}/${encodeURIComponent(p.symbol)}/stop-events`);
        eventsByKey.set(p.key, (res.ok && res.data && res.data.events) || []);
      })
    );
    return eventsByKey;
  }

  // The real, non-fabricated aggregate this panel (and the Initial
  // protection tab's normalized chart) render -- pure computation over
  // already-fetched real data, no network calls.
  function computeStopTargetAnalytics(pairs, eventsByKey, orders, signals) {
    let totalTightened = 0;
    let totalStopPlaced = 0;
    let totalProtectionFailed = 0;
    let totalTargetHit = 0;
    const tightenedByAccount = new Map();
    const tightenedBySymbol = new Map();
    const initialStopDistances = [];
    // Parallel to initialStopDistances (same index = same real event) --
    // that same real distance expressed as a PERCENT of the position's own
    // real entry_price, for the Initial protection tab's normalized chart.
    // Never a separate/re-derived figure: pushed from the exact same `abs`
    // computed alongside it below.
    const initialStopDistancePercents = [];
    let initialStopUnknownEntryCount = 0;

    for (const p of pairs) {
      const events = eventsByKey.get(p.key) || [];
      for (const e of events) {
        if (e.event_type === "stop_tightened") {
          totalTightened++;
          tightenedByAccount.set(p.account_id, (tightenedByAccount.get(p.account_id) || 0) + 1);
          tightenedBySymbol.set(p.symbol, (tightenedBySymbol.get(p.symbol) || 0) + 1);
        } else if (e.event_type === "stop_placed") {
          totalStopPlaced++;
          // LOAD-BEARING: only a genuine first placement (previous_price
          // === null) may contribute to the INITIAL-stop-distance
          // distribution -- a STOP_TIGHTENED's own (later, tighter) price
          // must never be substituted here, or the "initial" distribution
          // would silently describe tightened stops instead.
          if (e.previous_price === null || e.previous_price === undefined) {
            if (p.entry_price !== null && p.entry_price !== undefined && e.price !== null && e.price !== undefined) {
              const abs = Math.abs(e.price - p.entry_price);
              initialStopDistances.push(abs);
              if (p.entry_price !== 0) initialStopDistancePercents.push((abs / Math.abs(p.entry_price)) * 100);
            } else {
              initialStopUnknownEntryCount++;
            }
          }
        } else if (e.event_type === "protection_failed") {
          totalProtectionFailed++;
        } else if (e.event_type === "target_hit") {
          totalTargetHit++;
        }
      }
    }

    const signalsById = new Map(signals.map((s) => [String(s.id), s]));
    const entryOrders = earliestEntryOrdersByPair(orders);
    let targetBearingPositions = 0;
    let targetStatusUnknownCount = 0;
    for (const p of pairs) {
      const order = entryOrders.get(p.key);
      const signal = order ? signalsById.get(String(order.signal_id)) : undefined;
      if (!signal) {
        targetStatusUnknownCount++;
        continue;
      }
      if (signal.take_profit !== null && signal.take_profit !== undefined) targetBearingPositions++;
    }

    const totalStopAttempts = totalProtectionFailed + totalStopPlaced;
    return {
      totalTightened,
      tightenedByAccount,
      tightenedBySymbol,
      totalProtectionFailed,
      totalStopPlaced,
      totalStopAttempts,
      protectionFailureRate: totalStopAttempts > 0 ? totalProtectionFailed / totalStopAttempts : null,
      initialStopDistances,
      initialStopDistancePercents,
      initialStopUnknownEntryCount,
      totalTargetHit,
      targetBearingPositions,
      targetStatusUnknownCount,
      targetHitRate: targetBearingPositions > 0 ? totalTargetHit / targetBearingPositions : null,
    };
  }

  function capabilityHtml(opts) {
    const el = document.createElement("div");
    Components.renderCapabilityState(el, opts);
    return el.outerHTML;
  }

  // Plain, always-visible (never collapsed behind a <details>) disclosure
  // paragraph -- used for inline "this specific figure/field has no real
  // backing capability" notes embedded WITHIN an otherwise-real, ready
  // panel (e.g. one line inside the Initial protection tab, or one metric
  // inside the bottom analytics panel). Components.renderCapabilityState's
  // reason text lives inside a collapsed <details>, which is the right
  // call for a WHOLE dedicated tab (Trailing/Deadlines/Versions, per this
  // batch's own instructions) but would hide these shorter inline notes
  // from a plain read of the panel.
  function unsupportedNote(reason) {
    const el = document.createElement("div");
    StateMatrix.render(el, { state: "unsupported", reason });
    return el.outerHTML;
  }

  // This screen's outer container (`#route-panels`, app/static/router.js)
  // is a 12-column CSS grid (see app/static/dashboard.html's `.tr-grid`)
  // whose direct children are the actual grid items -- `.tr-panel:first-
  // of-type` only gets its special 8/12-column treatment when it's a
  // DIRECT grid child. Wrapping the existing panels in a per-tab
  // container div would otherwise make that wrapper (not the panel) the
  // grid item, breaking every panel's layout. `grid-column: 1 / -1` below
  // keeps the tab bar and each tab's wrapper spanning the full 12-column
  // row (same as every other TR-0x screen's panels), and the inner flex
  // column restores normal vertical spacing between the panels a tab
  // holds -- all inline, since app/static/design-system.css is out of
  // this batch's scope.
  const FULL_WIDTH_STACK = 'style="grid-column: 1 / -1; display: flex; flex-direction: column; gap: 16px;"';
  // The tab bar itself keeps `.tr-nav-primary`'s own row/flex-wrap layout
  // (see design-system.css) -- it only needs the grid-column override, not
  // FULL_WIDTH_STACK's flex-direction: column (which would stack the tab
  // buttons vertically instead of laying them out as a row of pills).
  const FULL_WIDTH_ROW = 'style="grid-column: 1 / -1;"';

  function shell() {
    return `
      <div class="tr-nav-primary" id="tr12-tabbar" role="tablist" aria-label="Policy sections" ${FULL_WIDTH_ROW}>
        <button type="button" class="tr-nav-tab" data-tab="sizing" role="tab" aria-current="page">Sizing</button>
        <button type="button" class="tr-nav-tab" data-tab="protection" role="tab">Initial protection</button>
        <button type="button" class="tr-nav-tab" data-tab="targets" role="tab">Targets</button>
        <button type="button" class="tr-nav-tab" data-tab="trailing" role="tab">Trailing</button>
        <button type="button" class="tr-nav-tab" data-tab="deadlines" role="tab">Deadlines</button>
        <button type="button" class="tr-nav-tab" data-tab="limits" role="tab">Limits</button>
        <button type="button" class="tr-nav-tab" data-tab="preview" role="tab">Preview</button>
        <button type="button" class="tr-nav-tab" data-tab="versions" role="tab">Versions</button>
      </div>

      <div class="tr12-tabpanel" id="tr12-tab-sizing" role="tabpanel" ${FULL_WIDTH_STACK}>
        <section class="tr-panel" id="tr12-p01"><h2>Policy scope</h2><div class="tr-panel-body"></div></section>
        <section class="tr-panel" id="tr12-p02"><h2>Size/risk controls</h2><div class="tr-panel-body"></div></section>
        <section class="tr-panel" id="tr12-p02b"><h2>Expected size under representative trades</h2><div class="tr-panel-body"></div></section>
      </div>

      <div class="tr12-tabpanel" id="tr12-tab-protection" role="tabpanel" hidden ${FULL_WIDTH_STACK}>
        <section class="tr-panel" id="tr12-p03"><h2>Initial protection</h2><div class="tr-panel-body"></div></section>
      </div>

      <div class="tr12-tabpanel" id="tr12-tab-targets" role="tabpanel" hidden ${FULL_WIDTH_STACK}>
        <section class="tr-panel" id="tr12-p04"><h2>Targets</h2><div class="tr-panel-body"></div></section>
      </div>

      <div class="tr12-tabpanel" id="tr12-tab-trailing" role="tabpanel" hidden ${FULL_WIDTH_STACK}>
        <section class="tr-panel" id="tr12-p04t"><h2>Trailing</h2><div class="tr-panel-body"></div></section>
      </div>

      <div class="tr12-tabpanel" id="tr12-tab-deadlines" role="tabpanel" hidden ${FULL_WIDTH_STACK}>
        <section class="tr-panel" id="tr12-p05"><h2>Deadlines</h2><div class="tr-panel-body"></div></section>
      </div>

      <div class="tr12-tabpanel" id="tr12-tab-limits" role="tabpanel" hidden ${FULL_WIDTH_STACK}>
        <section class="tr-panel" id="tr12-p08"><h2>Limits</h2><div class="tr-panel-body"></div></section>
      </div>

      <div class="tr12-tabpanel" id="tr12-tab-preview" role="tabpanel" hidden ${FULL_WIDTH_STACK}>
        <section class="tr-panel" id="tr12-p06"><h2>Preview</h2><div class="tr-panel-body"></div></section>
      </div>

      <div class="tr12-tabpanel" id="tr12-tab-versions" role="tabpanel" hidden ${FULL_WIDTH_STACK}>
        <section class="tr-panel" id="tr12-p09"><h2>Versions</h2><div class="tr-panel-body"></div></section>
      </div>

      <section class="tr-panel" id="tr12-p07"><h2>Stop &amp; target lifecycle analytics</h2><div class="tr-panel-body"></div></section>
    `;
  }

  const TAB_NAMES = ["sizing", "protection", "targets", "trailing", "deadlines", "limits", "preview", "versions"];

  function wireTabs(container) {
    const bar = container.querySelector("#tr12-tabbar");
    if (!bar || bar.dataset.wired) return;
    bar.dataset.wired = "1";
    bar.addEventListener("click", (event) => {
      const btn = event.target.closest("button[data-tab]");
      if (!btn) return;
      showTab(container, btn.dataset.tab);
    });
  }

  function showTab(container, name) {
    for (const tabName of TAB_NAMES) {
      const panel = container.querySelector(`#tr12-tab-${tabName}`);
      const btn = container.querySelector(`#tr12-tabbar button[data-tab="${tabName}"]`);
      if (panel) panel.hidden = tabName !== name;
      if (btn) {
        if (tabName === name) btn.setAttribute("aria-current", "page");
        else btn.removeAttribute("aria-current");
      }
    }
  }

  async function load(ctx) {
    wireTabs(ctx.container);

    const els = {
      scope: ctx.container.querySelector("#tr12-p01 .tr-panel-body"),
      size: ctx.container.querySelector("#tr12-p02 .tr-panel-body"),
      sizePreview: ctx.container.querySelector("#tr12-p02b .tr-panel-body"),
      stop: ctx.container.querySelector("#tr12-p03 .tr-panel-body"),
      targets: ctx.container.querySelector("#tr12-p04 .tr-panel-body"),
      trailing: ctx.container.querySelector("#tr12-p04t .tr-panel-body"),
      deadlines: ctx.container.querySelector("#tr12-p05 .tr-panel-body"),
      limits: ctx.container.querySelector("#tr12-p08 .tr-panel-body"),
      preview: ctx.container.querySelector("#tr12-p06 .tr-panel-body"),
      versions: ctx.container.querySelector("#tr12-p09 .tr-panel-body"),
      analytics: ctx.container.querySelector("#tr12-p07 .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const [providersRes, accountsRes, signalsRes, brokersRes, positionsRes, excursionsRes, ordersRes, capitalRes] = await Promise.all([
      ctx.fetchJSON("/providers"),
      ctx.fetchJSON("/accounts"),
      // Limit raised from 50 to 500 (PU-B10) -- reused both by "Preview"
      // below (still only shows the most recent 25 in its dropdown) and
      // by the real target-hit-rate join, which needs the take_profit of
      // whichever real signal an entry order actually referenced, not
      // just the most-recent handful.
      ctx.fetchJSON("/signals?limit=500"),
      ctx.fetchJSON("/brokers"),
      ctx.fetchJSON("/positions"),
      ctx.fetchJSON("/positions/excursions?limit=500"),
      ctx.fetchJSON("/orders?limit=500"),
      ctx.fetchJSON("/capital-allocation"),
    ]);
    if (providersRes.status === 401 || providersRes.status === 403) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "denied", deniedCode: providersRes.status });
      return;
    }
    if (!providersRes.ok || !accountsRes.ok) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "error", message: "Could not load providers/accounts." });
      return;
    }
    const providers = (providersRes.data && providersRes.data.providers) || [];
    const accounts = (accountsRes.data && accountsRes.data.accounts) || [];
    const signals = (signalsRes.ok && signalsRes.data && signalsRes.data.signals) || [];
    const brokers = (brokersRes.ok && brokersRes.data && brokersRes.data.brokers) || [];
    const openPositions = (positionsRes.ok && positionsRes.data && positionsRes.data.managed_lifecycles) || [];
    const closedPositions = (excursionsRes.ok && excursionsRes.data && excursionsRes.data.excursions) || [];
    const orders = (ordersRes.ok && ordersRes.data && ordersRes.data.orders) || [];
    const capitalByAccount = new Map(
      ((capitalRes.ok && capitalRes.data && capitalRes.data.accounts) || []).map((a) => [a.account_id, a])
    );

    if (!providers.length && !accounts.length) {
      for (const key of Object.keys(els)) {
        StateMatrix.render(els[key], {
          state: "empty",
          emptyMessage: "No compatible released management policy is available.",
          nextRoute: "/trade/sources/new",
          nextLabel: "Source onboarding and parser laboratory (TR-10)",
        });
      }
      ctx.setChrome({ asOf: new Date().toISOString() });
      return;
    }

    // --- Real (account, symbol) universe + stop/target event aggregate --
    // computed ONCE here, shared by the Initial protection tab's
    // normalized chart AND the bottom analytics panel (no duplicate
    // network calls, one source of truth). ---
    const pairs = buildPositionUniverse(openPositions, closedPositions);
    const eventsByKey = pairs.length ? await fetchStopEventsForUniverse(ctx, pairs) : new Map();
    const stats = pairs.length ? computeStopTargetAnalytics(pairs, eventsByKey, orders, signals) : null;

    // --- Policy scope ---
    StateMatrix.render(els.scope, {
      state: "ready",
      html: `
        <p class="section-note">Real, layered scope (app/providers.py): account default → provider override → analyst override, narrowest wins. Applies to every signal via Signal.source (provider) and Signal.analyst.</p>
        <div id="tr12-scope-kpi"></div>
        ${capabilityHtml({
          status: "not_tracked",
          reason: "A typed 'product profile' (product_profile_id) defining currency/quantity/trigger semantics per instrument is not tracked in this build -- sizing units follow whatever the signal itself carries.",
        })}
      `,
    });
    Components.renderKPIBand(els.scope.querySelector("#tr12-scope-kpi"), {
      items: [
        { label: "Accounts configured", value: fmtNum(accounts.length), tone: "neutral" },
        { label: "Providers configured", value: fmtNum(providers.length), tone: "neutral" },
        { label: "Analysts configured", value: fmtNum(providers.reduce((n, p) => n + p.analysts.length, 0)), tone: "neutral" },
      ],
    });

    // --- Size/risk controls (real, editable) ---
    const sizeRows = [];
    for (const a of accounts) {
      sizeRows.push([
        `<span class="mono">${escapeHtml(a.account_id)}</span>`,
        "account default",
        "—",
        a.fixed_quantity !== null && a.fixed_quantity !== undefined ? `fixed ${fmtNum(a.fixed_quantity)}` : `× ${fmtNum(a.multiplier)}`,
        a.max_notional_exposure !== null && a.max_notional_exposure !== undefined ? fmtNum(a.max_notional_exposure) : pill("none set", "muted"),
        a.fixed_quantity !== null && a.fixed_quantity !== undefined ? `fixed ${fmtNum(a.fixed_quantity)}` : `× ${fmtNum(a.multiplier)}`,
        "all future entries",
      ]);
    }
    for (const p of providers) {
      if (p.settings.multiplier !== null || p.settings.fixed_quantity !== null || p.settings.enabled === false) {
        sizeRows.push([
          `<span class="mono">${escapeHtml(p.provider_id)}</span>`,
          "provider override",
          "account default",
          describeOverride(p.settings),
          pill("bounded by account hard ceiling", "muted"),
          describeOverride(p.settings),
          `signals from "${escapeHtml(p.provider_id)}"`,
        ]);
      }
      for (const a of p.analysts) {
        if (a.settings.multiplier !== null || a.settings.fixed_quantity !== null || a.settings.enabled === false) {
          sizeRows.push([
            `<span class="mono">${escapeHtml(p.provider_id)}/${escapeHtml(a.analyst_id)}</span>`,
            "analyst override",
            `provider "${escapeHtml(p.provider_id)}"`,
            describeOverride(a.settings),
            pill("bounded by account hard ceiling", "muted"),
            describeOverride(a.settings),
            `signals from analyst "${escapeHtml(a.analyst_id)}"`,
          ]);
        }
      }
    }
    StateMatrix.render(els.size, {
      state: "ready",
      html: `
        <p class="section-note">Real, already-enforced sizing (app/risk.py's size_for_account) and its real overrides (app/providers.py). Edited through this build's existing, already-tested POST /providers/{id} and POST /providers/{id}/analysts/{id} below -- account-level multiplier/fixed_quantity/max_notional_exposure is edited on <a href="#/trade/accounts/new">Broker account configuration (TR-08)</a>, not duplicated here.</p>
        ${table(["Setting", "Source", "Inherited", "Requested", "Hard ceiling", "Effective", "Applies to"], sizeRows, "No sizing overrides set.")}
        <h3 class="section-note" style="margin-top:12px;">Edit a provider/analyst override</h3>
        <label>Provider ID<input type="text" id="tr12-override-provider" maxlength="80" placeholder="telegram"></label>
        <label>Analyst ID (optional -- blank edits the provider level)<input type="text" id="tr12-override-analyst" maxlength="80"></label>
        <label>Multiplier (blank = inherit)<input type="number" step="any" id="tr12-override-multiplier"></label>
        <label>Fixed quantity (blank = inherit)<input type="number" step="any" id="tr12-override-fixed"></label>
        <label class="checkbox"><input type="checkbox" id="tr12-override-enabled" checked> Enabled (admits new entries at this level)</label>
        <div class="form-error" id="tr12-override-error"></div>
        <div class="tr-controls-row"><button type="button" id="tr12-save-override">Save policy draft (sizing)</button></div>
        <div id="tr12-override-result"></div>
      `,
    });
    els.size.querySelector("#tr12-save-override").addEventListener("click", async () => {
      const errorEl = els.size.querySelector("#tr12-override-error");
      errorEl.textContent = "";
      const providerId = els.size.querySelector("#tr12-override-provider").value.trim();
      const analystId = els.size.querySelector("#tr12-override-analyst").value.trim();
      if (!providerId) { errorEl.textContent = "Provider ID is required."; return; }
      const multRaw = els.size.querySelector("#tr12-override-multiplier").value;
      const fixedRaw = els.size.querySelector("#tr12-override-fixed").value;
      const body = {
        display_name: "",
        multiplier: multRaw ? parseFloat(multRaw) : null,
        fixed_quantity: fixedRaw ? parseFloat(fixedRaw) : null,
        managed_lifecycle: null,
        enabled: els.size.querySelector("#tr12-override-enabled").checked,
      };
      try {
        if (analystId) {
          await postJSON(`/providers/${encodeURIComponent(providerId)}/analysts/${encodeURIComponent(analystId)}`, body);
        } else {
          await postJSON(`/providers/${encodeURIComponent(providerId)}`, body);
        }
        els.size.querySelector("#tr12-override-result").innerHTML = `<p class="section-note">Saved. Takes effect on the very next matching signal -- no restart, no live position retrofit.</p>`;
        await load(ctx);
      } catch (err) {
        errorEl.textContent = err.message;
      }
    });

    // --- Expected size under representative trades (real, new: GET
    // /policies/sizing-preview) ---
    await renderSizingPreview(ctx, els.sizePreview);

    // --- Initial protection (real, read-only) + normalized chart ---
    StateMatrix.render(els.stop, {
      state: "ready",
      html: `
        <p class="section-note">Real, code-verified rule (app/engine.py): the initial stop is ALWAYS exactly the provider's own <code>signal.stop_loss</code>, verbatim -- there is no fallback-stop recipe, no volatility-based calculation, and no way to configure one in this build. A broker adapter that cannot embed a stop into its entry order (no native bracket support) REJECTS an entry that carries one rather than silently dropping it.</p>
        ${table(
          ["Broker", "Native bracket (can embed stop)", "Effective behavior for a stopped signal"],
          brokers.map((b) => [
            `<span class="mono">${escapeHtml(b.name)}</span>`,
            boolPill(b.supports_native_bracket),
            b.supports_native_bracket ? pill("embeds stop_loss/take_profit into the entry order", "ok") : pill("rejects the entry if stop_loss/take_profit is present (unless managed_lifecycle)", "warn"),
          ]),
          "No brokers registered."
        )}
        <h3 class="section-note">Stop distance, normalized to price (real, from stop_target_events)</h3>
        <p class="section-note">Each genuine first STOP_PLACED event's real distance from that position's own real entry_price, expressed as a percent of that entry_price -- lets a $5 stop on a $100 position and a $50 stop on a $1000 position (both 5%) be compared on one axis, which the absolute-price-unit histogram at the bottom of this screen cannot do.</p>
        <div id="tr12-normalized-stop-chart-area"></div>
        ${capabilityHtml({
          status: "not_tracked",
          reason: "Fallback stop recipe (F-STOP-RECIPE) -- calculated fallback distance/method, provider-stop-treatment policy, coverage/unprotected-window policy -- has no backing engine capability. Do not configure a setting the engine never reads at signal time.",
        })}
      `,
    });
    renderNormalizedStopChart(els.stop.querySelector("#tr12-normalized-stop-chart-area"), stats);

    // --- Targets (real, single-target rule) ---
    StateMatrix.render(els.targets, {
      state: "ready",
      html: `
        <p class="section-note">Real, code-verified rule (app/engine.py): if the provider's signal carries <code>take_profit</code>, exactly ONE logical target is created -- a full-size (100%) SELL at that price. No partial-exit levels, no multiple targets, no configurable reduction fraction exist in this build.</p>
      `,
    });

    // --- Trailing (verified unsupported -- see module docstring) ---
    els.trailing.innerHTML = `
      <p class="section-note" style="margin-top:0;">The design review asked for an entry → target 1 → partial reduction → stop-to-breakeven → target 2 → trail simulation here. That workflow is not simulated: verified by direct code inspection (app/lifecycle/models.py's <code>TrailingPolicy</code>, app/lifecycle/manager.py's <code>_update_trailing</code>, and <code>StopTargetEventType</code>, which has no breakeven-move or trailing-activation event type on this branch), nothing in this branch's live signal-handling path ever constructs a non-null <code>TrailingPolicy</code>, a partial-reduction target, or a move-to-breakeven command -- no live position can currently trail, partially reduce, or move its stop to breakeven. A simulated preview of that workflow would show the owner a capability this engine cannot actually perform, which would be actively misleading.</p>
      ${capabilityHtml({
        status: "unsupported",
        reason: "Trailing / breakeven-stop / partial-reduction (F-TRAIL-RECIPE) -- code-verified unsupported: TrailingPolicy and its update logic exist and are covered by this repo's own lifecycle tests, but no non-test call site ever sets plan.trailing to anything but None, and no call site ever constructs a partial-reduction target or a move-to-breakeven command on the live signal path.",
        remediation: "This capability exists on the sibling claude/signal-copier-safety-features branch, not on this one -- merging that work would be the real remediation, not a form here.",
      })}
    `;

    // --- Deadlines (verified unsupported) ---
    els.deadlines.innerHTML = `
      <p class="section-note" style="margin-top:0;">Real, code-verified rule (app/lifecycle/manager.py's <code>check_time_exits</code>, run from app/reconciliation.py's periodic pass): a <code>PositionPlan.time_exit</code>, once set, IS enforced -- but nothing anywhere ever SETS one from a live signal or any config path today.</p>
      ${capabilityHtml({
        status: "not_tracked",
        reason: "No holding-period/deadline recipe (F-HOLD-RECIPE) exists to configure -- there is no signal field, provider setting, or account setting anywhere in this codebase that ever produces a non-null time_exit. Building a form for it would edit a field the engine never reads at signal time.",
      })}
    `;

    // --- Limits (real hard-ceiling exposure, from GET /capital-allocation) ---
    renderLimitsTab(els.limits, accounts, capitalByAccount);

    // --- Preview (real dry-run, GET /policies/sizing-preview?signal_id=) ---
    renderPreviewTab(ctx, els.preview, signals);

    // --- Versions (verified unsupported -- single-current-row config) ---
    els.versions.innerHTML = `
      <p class="section-note" style="margin-top:0;">Real, code-verified fact (app/db.py): <code>config_accounts</code> and <code>config_providers</code> are single-current-row state -- there is no version/draft/history table anywhere in this schema.</p>
      ${capabilityHtml({
        status: "not_tracked",
        reason: "Policy config is not currently versioned; a saved override above (Sizing tab) takes effect immediately, the same as every other already-tested provider/account config write in this project. There is no draft/activate workflow and no history to list -- a one-entry 'version list' would misrepresent a capability this build doesn't have.",
      })}
    `;

    // --- Stop & target lifecycle analytics (real, PU-B10) ---
    await renderStopTargetAnalytics(els.analytics, pairs, eventsByKey, stats);

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  // --- Sizing tab: "expected position size under several representative
  // trades and current account conditions" -- real, backend-computed
  // (GET /policies/sizing-preview), never client-reimplemented. ---
  async function renderSizingPreview(ctx, el) {
    el.innerHTML = `
      <p class="section-note" style="margin-top:0;">Calls this build's own <code>app.risk.size_for_account</code> (the exact function <code>app/engine.py</code> calls at real signal-admission time) against a few representative HYPOTHETICAL signal quantities and every configured destination account's REAL, current capital-allocation state -- never a separately-reimplemented approximation. No order is submitted; nothing here is persisted or routed.</p>
      <label>Representative price for notional (optional -- this build has no reliable "current market price" independent of a real signal)<input type="number" step="any" id="tr12-sizing-preview-price" placeholder="e.g. 100"></label>
      <div class="tr-controls-row"><button type="button" id="tr12-sizing-preview-run">Compute expected sizes</button></div>
      <div id="tr12-sizing-preview-result"></div>
    `;
    const run = async () => {
      const priceRaw = el.querySelector("#tr12-sizing-preview-price").value;
      const query = priceRaw ? `?price=${encodeURIComponent(priceRaw)}` : "";
      const res = await ctx.fetchJSON(`/policies/sizing-preview${query}`);
      const resultEl = el.querySelector("#tr12-sizing-preview-result");
      if (!res.ok) { resultEl.innerHTML = `<p class="sm-error-message">Could not compute sizing preview.</p>`; return; }
      const accountsOut = (res.data && res.data.accounts) || [];
      if (!accountsOut.length) { resultEl.innerHTML = `<div class="empty">No destination accounts configured.</div>`; return; }
      const rows = [];
      for (const a of accountsOut) {
        for (const s of a.scenarios) {
          rows.push([
            `<span class="mono">${escapeHtml(a.account_id)}</span>`,
            escapeHtml(s.label),
            fmtNum(s.expected_quantity),
            s.notional === null ? pill("no price given", "muted") : fmtNum(s.notional),
            s.would_exceed_ceiling === null
              ? pill("unknown (no ceiling or no price)", "muted")
              : s.would_exceed_ceiling
              ? pill("would exceed hard ceiling", "bad")
              : pill("within hard ceiling", "ok"),
          ]);
        }
      }
      resultEl.innerHTML = table(["Account", "Representative trade", "Expected size (units)", "Notional", "Vs. hard ceiling"], rows, "No scenarios computed.");
    };
    el.querySelector("#tr12-sizing-preview-run").addEventListener("click", run);
    await run();
  }

  function renderNormalizedStopChart(container, stats) {
    if (!container) return;
    destroyChart("normalizedStopDistance");
    if (!stats || !stats.initialStopDistancePercents.length) {
      container.innerHTML = `<div class="empty">No genuine first STOP_PLACED event with a known real, non-zero entry_price exists yet to normalize.</div>`;
      return;
    }
    container.innerHTML = `<div class="chart-container"><canvas id="tr12-normalized-stop-chart"></canvas></div>`;
    const bucketCount = Math.min(6, stats.initialStopDistancePercents.length);
    const { labels, counts } = histogramBuckets(stats.initialStopDistancePercents, bucketCount);
    charts.normalizedStopDistance = renderBarChart(
      container.querySelector("#tr12-normalized-stop-chart"),
      labels.map((l) => `${l}%`),
      counts,
      "Initial stop distance (% of entry price)"
    );
  }

  function renderLimitsTab(el, accounts, capitalByAccount) {
    const rows = accounts.map((a) => {
      const cap = capitalByAccount.get(a.account_id);
      const maxExposure = a.max_notional_exposure !== null && a.max_notional_exposure !== undefined ? a.max_notional_exposure : null;
      return [
        `<span class="mono">${escapeHtml(a.account_id)}</span>`,
        maxExposure !== null ? fmtNum(maxExposure) : pill("no ceiling set (E03 gate opted out)", "muted"),
        cap ? fmtNum(cap.deployed_notional) : "—",
        cap ? fmtNum(cap.reserved_notional) : "—",
        cap && cap.available_notional !== null && cap.available_notional !== undefined ? fmtNum(cap.available_notional) : pill("n/a (no ceiling)", "muted"),
      ];
    });
    el.innerHTML = `
      <p class="section-note" style="margin-top:0;">Real, already-enforced hard ceiling (E03, app/capital_allocator.py) -- <code>account.max_notional_exposure</code>, editable only via <a href="#/trade/accounts/new">Broker account configuration (TR-08)</a>. No override on the Sizing tab can exceed this. "Deployed"/"Reserved"/"Available" are this account's REAL, current capital-allocation state (GET /capital-allocation) -- the exact figures the E03 admission gate itself reads before deciding, never a separately-maintained copy.</p>
      ${table(["Account", "Hard ceiling (notional)", "Deployed (confirmed)", "Reserved (in-flight)", "Available"], rows, "No accounts configured.")}
    `;
    for (const a of accounts) {
      const cap = capitalByAccount.get(a.account_id);
      if (!cap || a.max_notional_exposure === null || a.max_notional_exposure === undefined) continue;
      const wrap = document.createElement("div");
      wrap.className = "section-note";
      wrap.innerHTML = `<strong>${escapeHtml(a.account_id)}</strong>`;
      const bar = document.createElement("div");
      Components.renderQuantityLedgerBar(bar, {
        total: a.max_notional_exposure,
        segments: [
          { label: "Deployed", value: cap.deployed_notional, tone: "accent" },
          { label: "Reserved", value: cap.reserved_notional, tone: "warn" },
          {
            label: "Available",
            value: cap.available_notional !== null && cap.available_notional !== undefined ? Math.max(0, cap.available_notional) : 0,
            tone: "ok",
          },
        ],
      });
      wrap.appendChild(bar);
      el.appendChild(wrap);
    }
  }

  // --- Preview tab: "a dry-run showing what would happen if a
  // representative signal arrived right now under the current saved
  // policy" -- reuses the exact same GET /policies/sizing-preview
  // endpoint (via `signal_id`) that the Sizing tab's representative-
  // scenario mode calls, so this can never diverge from either the real
  // engine or the Sizing tab's own figures. Scoped to sizing/protection
  // preview only -- no routing-rule matching is rebuilt here (TR-11 owns
  // that). ---
  function renderPreviewTab(ctx, el, signals) {
    const signalOptions = signals
      .slice(0, 25)
      .map((s) => `<option value="${escapeAttr(String(s.id))}">#${escapeHtml(String(s.id))} ${escapeHtml(s.source)} ${escapeHtml(s.symbol)} ${escapeHtml(s.side)}</option>`)
      .join("");
    el.innerHTML = `
      <p class="section-note" style="margin-top:0;">Dry-run: what the Sizing tab's real <code>size_for_account</code> call would expect for every destination account, right now, if this real already-received signal arrived again under the current saved policy. Same backend call as the Sizing tab's representative-trade table above (GET /policies/sizing-preview) -- read-only, no broker call, no order submitted. Routing-rule matching (which account(s) a signal would actually be sent to) is TR-11's "Routing and allocation rules" screen, not duplicated here.</p>
      <label>Signal<select id="tr12-preview-signal">${signalOptions || '<option value="">No signals received yet</option>'}</select></label>
      <button type="button" id="tr12-preview-run" ${signals.length ? "" : "disabled"}>Preview on signal</button>
      <div id="tr12-preview-result"></div>
      ${capabilityHtml({
        status: "not_tracked",
        reason: "Request review (TR-12-A03) has no backing capability: this build has no owner-review/approval-queue workflow for a policy change -- a saved override on the Sizing tab takes effect directly, the same as every other already-tested provider/account config write in this project.",
      })}
    `;
    if (!signals.length) return;
    el.querySelector("#tr12-preview-run").addEventListener("click", async () => {
      const signalId = el.querySelector("#tr12-preview-signal").value;
      const resultEl = el.querySelector("#tr12-preview-result");
      if (!signalId) { resultEl.innerHTML = `<p class="sm-error-message">Select a signal.</p>`; return; }
      const res = await ctx.fetchJSON(`/policies/sizing-preview?signal_id=${encodeURIComponent(signalId)}`);
      if (!res.ok) { resultEl.innerHTML = `<p class="sm-error-message">Could not compute preview for this signal.</p>`; return; }
      const accountsOut = (res.data && res.data.accounts) || [];
      const rows = accountsOut.map((a) => {
        const overCeiling = a.would_exceed_ceiling === true;
        return [
          `condition: account "${escapeHtml(a.account_id)}" sizing resolves`,
          overCeiling
            ? pill(`would exceed hard ceiling (${fmtNum(a.expected_quantity)} units)`, "bad")
            : pill(`${fmtNum(a.expected_quantity)} units`, "ok"),
          overCeiling ? "OVER_HARD_CEILING" : "OK",
          "app/risk.py size_for_account (real backend call, GET /policies/sizing-preview)",
        ];
      });
      resultEl.innerHTML = table(["Condition", "Actual outcome", "Reason code", "Evidence"], rows, "No destination accounts configured.");
    });
  }

  async function renderStopTargetAnalytics(el, pairs, eventsByKey, stats) {
    if (!pairs.length) {
      StateMatrix.render(el, {
        state: "ready",
        html: `
          <p class="section-note" style="margin-top:0;">Real, code-verified rule (app/lifecycle/models.py's <code>StopTargetEventType</code>): STOP_PLACED, STOP_TIGHTENED, PROTECTION_FAILED and TARGET_HIT are the only real event types this branch's own lifecycle manager ever appends -- no breakeven-move or trailing-activation event type exists here.</p>
          <p class="section-note">No open or closed managed-lifecycle position exists yet for this account/provider scope, so there is no real (account, symbol) universe to query <code>GET /positions/&lt;account&gt;/&lt;symbol&gt;/stop-events</code> against. Every count below would be an honest 0 of 0 -- shown as "no positions yet" rather than a table of zeroes.</p>
        `,
      });
      return;
    }

    destroyChart("initialStopDistance");
    const accountRows = [...stats.tightenedByAccount.entries()].map(([a, n]) => [a, fmtNum(n)]);
    const symbolRows = [...stats.tightenedBySymbol.entries()].map(([s, n]) => [s, fmtNum(n)]);

    const failureRateText =
      stats.protectionFailureRate === null
        ? unsupportedNote("No real STOP_PLACED/PROTECTION_FAILED attempt exists yet for this universe -- a rate with a real 0-attempt denominator would be undefined, not a fabricated 0%.")
        : `<p class="section-note"><strong>${fmtNum(stats.protectionFailureRate * 100)}%</strong> (${fmtNum(stats.totalProtectionFailed)} PROTECTION_FAILED of ${fmtNum(stats.totalStopAttempts)} real stop attempts = PROTECTION_FAILED + STOP_PLACED).</p>`;

    const targetHitRateText =
      stats.targetHitRate === null
        ? unsupportedNote("No position in this universe has a real, join-confirmed take_profit yet -- a rate with a real 0-in-denominator would be undefined, not a fabricated 0%.")
        : `<p class="section-note"><strong>${fmtNum(stats.targetHitRate * 100)}%</strong> (${fmtNum(stats.totalTargetHit)} real TARGET_HIT events of ${fmtNum(stats.targetBearingPositions)} real positions whose entry order's own signal carried a take_profit).</p>`;

    const distanceNote =
      stats.initialStopDistances.length === 0
        ? `<div class="empty">No genuine first STOP_PLACED (previous_price = null) with a known real entry_price exists yet in this universe.</div>`
        : `<div class="chart-container"><canvas id="tr12-initial-stop-distance-chart"></canvas></div>`;

    el.innerHTML = `
      <p class="section-note" style="margin-top:0;">First consumer of Phase A4's real, append-only <code>GET /positions/&lt;account&gt;/&lt;symbol&gt;/stop-events</code> log, aggregated client-side across ${fmtNum(pairs.length)} real (account, symbol) position${pairs.length === 1 ? "" : "s"} this account/provider scope has ever had a managed lifecycle for (open or closed). Only STOP_PLACED, STOP_TIGHTENED, PROTECTION_FAILED and TARGET_HIT are real event types on this branch -- no breakeven-move or trailing-activation event exists to chart.</p>

      <h3 class="section-note" style="margin-top:0;">Stop-tightening frequency (real STOP_TIGHTENED count)</h3>
      <p class="section-note">Overall: <strong>${fmtNum(stats.totalTightened)}</strong> real tightening events across every queried position.</p>
      ${table(["Account", "Real tightening count"], accountRows, "No STOP_TIGHTENED events recorded for any account yet.")}
      ${table(["Symbol", "Real tightening count"], symbolRows, "No STOP_TIGHTENED events recorded for any symbol yet.")}

      <h3 class="section-note">Protection-failure rate</h3>
      ${failureRateText}

      <h3 class="section-note">Initial-stop-distance distribution (price units from real entry_price)</h3>
      <p class="section-note">${fmtNum(stats.initialStopDistances.length)} genuine first placement${stats.initialStopDistances.length === 1 ? "" : "s"} charted${stats.initialStopUnknownEntryCount ? `; ${fmtNum(stats.initialStopUnknownEntryCount)} excluded (no known real entry_price for that position)` : ""}.</p>
      ${distanceNote}

      <h3 class="section-note">Target-hit rate</h3>
      ${targetHitRateText}
      ${stats.targetStatusUnknownCount ? `<p class="section-note">${fmtNum(stats.targetStatusUnknownCount)} position(s) excluded from this rate entirely -- no real entry order/signal join could be found for them, so whether they ever had a target is genuinely unknown, not assumed either way.</p>` : ""}
      ${unsupportedNote("Target-count distribution: this codebase only ever constructs ONE take_profit target per entry (see the Targets tab above) -- a distribution over an always-1 value would be misleading, so this is stated as a fact rather than charted.")}
      ${unsupportedNote("Stop-efficiency-vs-MFE scatter: buildable in principle from this screen's own real initial-stop distances plus GET /positions/excursions' real mfe, but this schema has no episode id linking one specific STOP_PLACED event to one specific position_excursions row when the same account/symbol has closed and reopened more than once -- pairing a stop distance with the wrong episode's MFE would misrepresent stop efficiency rather than merely be imprecise, so this is deferred until a real per-episode join key exists.")}

      <h3 class="section-note">Stop-tightening trajectory (single-position drill-down)</h3>
      <label>Position<select id="tr12-trajectory-position">${pairs.map((p) => `<option value="${escapeAttr(p.key)}">${escapeHtml(p.account_id)} / ${escapeHtml(p.symbol)} (${p.status})</option>`).join("")}</select></label>
      <button type="button" id="tr12-trajectory-run">Show real tightening trajectory</button>
      <div id="tr12-trajectory-result"></div>
    `;

    if (stats.initialStopDistances.length) {
      const bucketCount = Math.min(6, stats.initialStopDistances.length);
      const { labels, counts } = histogramBuckets(stats.initialStopDistances, bucketCount);
      charts.initialStopDistance = renderBarChart(
        el.querySelector("#tr12-initial-stop-distance-chart"),
        labels,
        counts,
        "Initial STOP_PLACED positions"
      );
    }

    el.querySelector("#tr12-trajectory-run").addEventListener("click", () => {
      const key = el.querySelector("#tr12-trajectory-position").value;
      const events = (eventsByKey.get(key) || []).filter((e) => e.event_type === "stop_tightened");
      const resultEl = el.querySelector("#tr12-trajectory-result");
      const rows = events.map((e) => [escapeHtml(e.at), fmtNum(e.previous_price), fmtNum(e.price)]);
      resultEl.innerHTML = table(["At", "Previous price", "New (tighter) price"], rows, "No real STOP_TIGHTENED events for this position yet.");
    });
  }

  function describeOverride(settings) {
    const parts = [];
    if (settings.fixed_quantity !== null && settings.fixed_quantity !== undefined) parts.push(`fixed ${fmtNum(settings.fixed_quantity)}`);
    else if (settings.multiplier !== null && settings.multiplier !== undefined) parts.push(`× ${fmtNum(settings.multiplier)}`);
    else parts.push("inherit");
    if (settings.enabled === false) parts.push(pill("muted", "warn"));
    return parts.join(" ");
  }

  window.Views = window.Views || {};
  window.Views.tr12 = {
    title: "Sizing, stops and profit policies",
    breadcrumb: "Trade / Policies",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = shell();
      await load(ctx);
      ctx.registerPoll("tr12", 10000, () => load(ctx));
    },
  };
  Router.register("/trade/policies", "tr12");
})();
