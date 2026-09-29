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
 *     an active trail. Rendered honestly as unsupported; no config UI is
 *     wired to a field the engine never reads from anywhere at signal
 *     time.
 *   - HOLDING/DEADLINES (verified UNSUPPORTED): `PositionPlan.time_exit`
 *     IS checked and enforced once set (app/lifecycle/manager.py's
 *     check_time_exits, called from app/reconciliation.py's PRO-02 pass)
 *     -- but nothing ever SETS it from a live signal or any config path.
 *     Same treatment: honestly unsupported, not wired to a fake form.
 *
 * "Preview on signal" (TR-12-A02) is real, not fabricated: given a
 * selected real, already-received signal (GET /signals) it replicates
 * app/risk.py's size_for_account formula EXACTLY, client-side, against
 * every configured destination account -- read-only, no broker call, no
 * order submitted, same "no live effect" contract as every other Preview
 * action in this batch.
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
 * /signals which this screen already fetches for "Preview on signal").
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
 *     this branch (see above); the existing "Targets/trailing" panel
 *     below already documents this as verified-unsupported and this new
 *     panel does not repeat a fake chart for it.
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
 *     ONE take_profit target per entry (see the existing "Targets/
 *     trailing" panel below); a "distribution" over an always-1 value
 *     would be misleading, so this panel states that real fact in prose
 *     instead of drawing a pointless chart.
 */
(function () {
  "use strict";

  const charts = { initialStopDistance: null };

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

  // The real, non-fabricated aggregate this panel renders -- pure
  // computation over already-fetched real data, no network calls.
  function computeStopTargetAnalytics(pairs, eventsByKey, orders, signals) {
    let totalTightened = 0;
    let totalStopPlaced = 0;
    let totalProtectionFailed = 0;
    let totalTargetHit = 0;
    const tightenedByAccount = new Map();
    const tightenedBySymbol = new Map();
    const initialStopDistances = [];
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
              initialStopDistances.push(Math.abs(e.price - p.entry_price));
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
      initialStopUnknownEntryCount,
      totalTargetHit,
      targetBearingPositions,
      targetStatusUnknownCount,
      targetHitRate: targetBearingPositions > 0 ? totalTargetHit / targetBearingPositions : null,
    };
  }

  function unsupportedNote(reason) {
    const el = document.createElement("div");
    StateMatrix.render(el, { state: "unsupported", reason });
    return el.outerHTML;
  }

  // Mirrors app/risk.py's size_for_account EXACTLY -- read-only preview,
  // never used to place or size a real order.
  function sizeForAccount(signal, account) {
    if (account.fixed_quantity !== null && account.fixed_quantity !== undefined) return account.fixed_quantity;
    const base = signal.quantity !== null && signal.quantity !== undefined ? signal.quantity : 1.0;
    return base * account.multiplier;
  }

  function shell() {
    return `
      <section class="tr-panel" id="tr12-p01"><h2>Policy scope</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr12-p02"><h2>Size/risk controls</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr12-p03"><h2>Initial protection</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr12-p04"><h2>Targets/trailing</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr12-p05"><h2>Deadlines</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr12-p06"><h2>Effective preview</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr12-p07"><h2>Stop &amp; target lifecycle analytics</h2><div class="tr-panel-body"></div></section>
    `;
  }

  async function load(ctx) {
    const els = {
      scope: ctx.container.querySelector("#tr12-p01 .tr-panel-body"),
      size: ctx.container.querySelector("#tr12-p02 .tr-panel-body"),
      stop: ctx.container.querySelector("#tr12-p03 .tr-panel-body"),
      targets: ctx.container.querySelector("#tr12-p04 .tr-panel-body"),
      deadlines: ctx.container.querySelector("#tr12-p05 .tr-panel-body"),
      preview: ctx.container.querySelector("#tr12-p06 .tr-panel-body"),
      analytics: ctx.container.querySelector("#tr12-p07 .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const [providersRes, accountsRes, signalsRes, brokersRes, positionsRes, excursionsRes, ordersRes] = await Promise.all([
      ctx.fetchJSON("/providers"),
      ctx.fetchJSON("/accounts"),
      // Limit raised from 50 to 500 this batch (PU-B10) -- reused both by
      // "Preview on signal" below (still only shows the most recent 25 in
      // its dropdown) and by the real target-hit-rate join, which needs
      // the take_profit of whichever real signal an entry order actually
      // referenced, not just the most-recent handful.
      ctx.fetchJSON("/signals?limit=500"),
      ctx.fetchJSON("/brokers"),
      ctx.fetchJSON("/positions"),
      ctx.fetchJSON("/positions/excursions?limit=500"),
      ctx.fetchJSON("/orders?limit=500"),
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
    const brokersByName = new Map(brokers.map((b) => [b.name, b]));
    const openPositions = (positionsRes.ok && positionsRes.data && positionsRes.data.managed_lifecycles) || [];
    const closedPositions = (excursionsRes.ok && excursionsRes.data && excursionsRes.data.excursions) || [];
    const orders = (ordersRes.ok && ordersRes.data && ordersRes.data.orders) || [];

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

    // --- Policy scope ---
    StateMatrix.render(els.scope, {
      state: "ready",
      html: `
        <p class="section-note">Real, layered scope (app/providers.py): account default → provider override → analyst override, narrowest wins. Applies to every signal via Signal.source (provider) and Signal.analyst.</p>
        <ul>
          <li>Accounts configured: ${accounts.length}</li>
          <li>Providers configured: ${providers.length}</li>
          <li>Analysts configured: ${providers.reduce((n, p) => n + p.analysts.length, 0)}</li>
        </ul>
        ${unsupportedNote("A typed 'product profile' (product_profile_id) defining currency/quantity/trigger semantics per instrument is not tracked in this build -- sizing units follow whatever the signal itself carries.")}
      `,
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

    // --- Initial protection (real, read-only) ---
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
        ${unsupportedNote("Fallback stop recipe (F-STOP-RECIPE) -- calculated fallback distance/method, provider-stop-treatment policy, coverage/unprotected-window policy -- has no backing engine capability. Do not configure a setting the engine never reads at signal time.")}
      `,
    });

    // --- Targets/trailing (real target rule, unsupported trailing) ---
    StateMatrix.render(els.targets, {
      state: "ready",
      html: `
        <p class="section-note">Real, code-verified rule (app/engine.py): if the provider's signal carries <code>take_profit</code>, exactly ONE logical target is created -- a full-size (100%) SELL at that price. No partial-exit levels, no multiple targets, no configurable reduction fraction exist in this build.</p>
        ${unsupportedNote("Trailing/profit-lock recipe (F-TRAIL-RECIPE) -- verified unsupported by direct code inspection: app/lifecycle/models.py's TrailingPolicy and its update logic (app/lifecycle/manager.py) exist and are covered by this repo's own lifecycle tests, but nothing in the live signal-handling path ever constructs a non-null TrailingPolicy on a real plan (grepped: no non-test call site sets plan.trailing to anything but None). No live position can currently trail. This form is intentionally not offered rather than wired to a field the engine never reads.")}
      `,
    });

    // --- Deadlines (unsupported) ---
    StateMatrix.render(els.deadlines, {
      state: "unsupported",
      reason: "Verified unsupported by direct code inspection: PositionPlan.time_exit IS enforced once set (app/lifecycle/manager.py's check_time_exits, run from app/reconciliation.py's periodic pass) -- but nothing anywhere sets it from a live signal or any config path, so there is no live way to configure a holding/deadline recipe (F-HOLD-RECIPE) today.",
    });

    // --- Effective preview (checklist, real formula against a real signal) ---
    const signalOptions = signals.slice(0, 25).map((s) => `<option value="${escapeAttr(String(s.id))}">#${escapeHtml(String(s.id))} ${escapeHtml(s.source)} ${escapeHtml(s.symbol)} ${escapeHtml(s.side)}</option>`).join("");
    StateMatrix.render(els.preview, {
      state: "ready",
      html: `
        <label>Signal<select id="tr12-preview-signal">${signalOptions || '<option value="">No signals received yet</option>'}</select></label>
        <button type="button" id="tr12-preview-run" ${signals.length ? "" : "disabled"}>Preview on signal</button>
        <p class="section-note">Replicates app/risk.py's size_for_account formula exactly, read-only, against a real already-received signal and every configured destination account -- no order is submitted.</p>
        <div id="tr12-preview-result"></div>
      `,
    });
    if (signals.length) {
      els.preview.querySelector("#tr12-preview-run").addEventListener("click", () => {
        // BUG FOUND DURING TESTING: signal ids are UUID strings (see
        // app/models.py's Signal.id), not integers -- parseInt() here used
        // to truncate a UUID like "1268ffb0-9ed2-..." down to 1268 and
        // NaN-compare it against the real string id, so the lookup below
        // always failed silently and "Preview on signal" never rendered
        // anything. Compare the raw string value instead.
        const signalId = els.preview.querySelector("#tr12-preview-signal").value;
        const signal = signals.find((s) => String(s.id) === signalId);
        const resultEl = els.preview.querySelector("#tr12-preview-result");
        if (!signal) { resultEl.innerHTML = `<p class="sm-error-message">Select a signal.</p>`; return; }
        const rows = accounts.map((a) => {
          const quantity = sizeForAccount(signal, a);
          const notional = signal.price ? quantity * signal.price : null;
          const overCeiling = a.max_notional_exposure !== null && a.max_notional_exposure !== undefined && notional !== null && notional > a.max_notional_exposure;
          return [
            `condition: account "${escapeHtml(a.account_id)}" sizing resolves`,
            overCeiling ? pill(`would exceed hard ceiling (${fmtNum(quantity)} units)`, "bad") : pill(`${fmtNum(quantity)} units`, "ok"),
            overCeiling ? "OVER_HARD_CEILING" : "OK",
            "app/risk.py size_for_account (mirrored client-side, read-only)",
          ];
        });
        resultEl.innerHTML = table(["Condition", "Actual outcome", "Reason code", "Evidence"], rows, "No destination accounts configured.");
      });
    }

    // --- Actions not otherwise offered above ---
    const actionsEl = document.createElement("div");
    StateMatrix.render(actionsEl, {
      state: "unsupported",
      reason: "Request review (TR-12-A03) has no backing capability: this build has no owner-review/approval-queue workflow for a policy change -- a saved override above takes effect directly, the same as every other already-tested provider/account config write in this project.",
    });
    els.preview.insertAdjacentHTML("beforeend", actionsEl.innerHTML);

    // --- Stop & target lifecycle analytics (real, PU-B10) ---
    await renderStopTargetAnalytics(ctx, els.analytics, openPositions, closedPositions, orders, signals);

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  async function renderStopTargetAnalytics(ctx, el, openPositions, closedPositions, orders, signals) {
    const pairs = buildPositionUniverse(openPositions, closedPositions);
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

    const eventsByKey = await fetchStopEventsForUniverse(ctx, pairs);
    const stats = computeStopTargetAnalytics(pairs, eventsByKey, orders, signals);

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
      ${unsupportedNote("Target-count distribution: this codebase only ever constructs ONE take_profit target per entry (see the Targets/trailing panel above) -- a distribution over an always-1 value would be misleading, so this is stated as a fact rather than charted.")}
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
