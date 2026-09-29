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
 */
(function () {
  "use strict";

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
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const [providersRes, accountsRes, signalsRes, brokersRes] = await Promise.all([
      ctx.fetchJSON("/providers"),
      ctx.fetchJSON("/accounts"),
      ctx.fetchJSON("/signals?limit=50"),
      ctx.fetchJSON("/brokers"),
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

    ctx.setChrome({ asOf: new Date().toISOString() });
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
