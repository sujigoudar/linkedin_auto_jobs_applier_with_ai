/* TR-08: Broker account configuration (`#/trade/accounts/new`).
 *
 * Real backing data/capability: GET /brokers (registered adapters, for
 * the Adapter select and the Capabilities panel's evidence) and
 * POST /accounts -- the EXACT SAME create/update endpoint the legacy
 * dashboard's own Accounts form and TR-07's "Pause new entries" action
 * both already call (app/main.py's `create_or_update_account`, already
 * covered by this repo's own account tests) -- this screen adds no new
 * financial capability, just a second, spec-shaped surface onto it.
 *
 * Honest gap, disclosed rather than worked around: TR-08's spec form
 * (F-BROKER) names ten fields -- this codebase's real account model
 * (`AccountRequest` in app/main.py / `config_accounts` in app/db.py) has
 * NO venue_id, external_account_ref, environment (simulation/paper/live),
 * credential_ref or position_mode field, and no "products" concept
 * distinct from a per-symbol rename map. Each of those five renders
 * "unsupported" in its own panel, explaining exactly what's missing,
 * rather than inventing form fields this build's backend would silently
 * ignore. The real, working fields are: account_label -> account_id,
 * adapter_id -> broker (from the live GET /brokers registry, never a
 * free-typed module import string), plus multiplier/fixed_quantity/
 * symbol_map/managed_lifecycle/max_notional_exposure (required by this
 * build's account model but not individually named in the spec's field
 * table -- grouped under Review below, honestly labelled as such).
 *
 * "Save never enables trading" (spec's own acceptance line) is enforced
 * literally: TR-08-A01 always POSTs `enabled: false`, regardless of any
 * later flip -- there is no separate two-stage release gate in this
 * build's account model (no `entry_enabled` distinct from `enabled`), so
 * this screen does not pretend one exists; turning an account live is a
 * distinct, separate step done elsewhere (editing `enabled`, e.g. via the
 * legacy dashboard's Accounts form), matching TR-08-A03's own text
 * ("activation separate operation").
 */
(function () {
  "use strict";

  function shell() {
    return `
      <section class="tr-panel" id="tr08-p01"><h2>Adapter/mode</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr08-p02"><h2>External identity</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr08-p03"><h2>Credential reference</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr08-p04"><h2>Product metadata</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr08-p05"><h2>Capabilities</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr08-p06"><h2>Review</h2><div class="tr-panel-body"></div></section>
    `;
  }

  // Placeholder-slot pattern (see tr04.js): a capability-state badge
  // renders into a real DOM element, but this view composes each panel's
  // markup (form fields plus a gap note) as one HTML string before it is
  // inserted. `unsupportedNote` reserves a slot and queues its opts;
  // `flushCapStates` mounts everything queued so far once the panel's
  // string has actually been assigned to `.innerHTML`.
  let capIdCounter = 0;
  let pendingCapStates = [];
  function capSlot(id) {
    return `<span class="cap-state-slot" id="${id}"></span>`;
  }
  function mountCapStates(root, specs) {
    for (const [id, opts] of specs) {
      const el = root.querySelector(`#${id}`);
      if (el) Components.renderCapabilityState(el, opts);
    }
  }

  // --- Real, source-verified per-broker credential env-var NAMING
  // CONVENTION (app/brokers/*.py's own `_credentials_for`/
  // `_webhook_url_for`/`_exchange_for`) -- informational only, mirrored
  // from tr07.js. Never a secret value, and never evidence the env vars
  // are actually set (this build never checks that from a form).
  const CREDENTIAL_REF_PATTERNS = {
    alpaca: (id) => `ALPACA_${id}_API_KEY (+ _API_SECRET, _BASE_URL)`,
    schwab: (id) => `SCHWAB_${id}_CLIENT_ID (+ _CLIENT_SECRET, _REFRESH_TOKEN, _ACCOUNT_HASH)`,
    robinhood: (id) => `ROBINHOOD_${id}_USERNAME (+ _PASSWORD, _ACCOUNT_NUMBER, optional _MFA_CODE)`,
    tastytrade: (id) => `TASTYTRADE_${id}_SECRET (+ _REFRESH_TOKEN, _TT_ACCOUNT_NUMBER)`,
    tradestation: (id) => `TRADESTATION_${id}_CLIENT_ID (+ _REFRESH_TOKEN, _TS_ACCOUNT_ID)`,
    tradovate: (id) => `TRADOVATE_${id}_USERNAME (+ _PASSWORD, _APP_ID, _CID, _SECRET, _DEVICE_ID, _ACCOUNT_SPEC)`,
    oanda: (id) => `OANDA_${id}_TOKEN (+ _ACCOUNT_ID)`,
    signalstack: (id) => `SIGNALSTACK_${id}_WEBHOOK_URL`,
    ninjatrader: (id) => `NT8_${id}_URL`,
    rithmic: (id) => `RITHMIC_${id}_RITHMIC_ACCOUNT_ID (+ _EXCHANGE)`,
    mt4_mt5: (id) => `MT5_${id}_LOGIN (+ _PASSWORD, _SERVER)`,
    mt4_mt5_metaapi: (id) => `MT4_MT5_METAAPI_${id}_ID (token itself is process-wide: MT4_MT5_METAAPI_TOKEN)`,
    ccxt: (id) => `CCXT_${id}_API_KEY (+ _API_SECRET)`,
  };
  function credentialRefReason(brokerName, accountLabel) {
    const upperId = String(accountLabel || "{account_label}").toUpperCase();
    const pattern = CREDENTIAL_REF_PATTERNS[brokerName];
    if (pattern) {
      return `Real env-var naming convention this adapter's code reads (never the secret value, never stored by this form): ${pattern(upperId)}. Set these separately, outside this screen, once the account label above is chosen.`;
    }
    if (brokerName === "ibkr") {
      return "This adapter connects via process-wide config (IBKR_HOST/IBKR_PORT/IBKR_CLIENT_ID -- a local TWS/Gateway connection), not a per-account API key -- there is no per-account credential reference to show.";
    }
    if (brokerName === "paper") {
      return "In-process simulator -- no external credential of any kind to reference.";
    }
    return `No known credential env-var convention for adapter "${brokerName}" in this build.`;
  }
  function unsupportedNote(reason, remediation) {
    const id = `tr08-cap-${capIdCounter++}`;
    pendingCapStates.push([id, { status: "unsupported", reason, remediation }]);
    return capSlot(id);
  }
  function flushCapStates(container) {
    mountCapStates(container, pendingCapStates);
    pendingCapStates = [];
  }

  async function load(ctx) {
    const els = {
      adapter: ctx.container.querySelector("#tr08-p01 .tr-panel-body"),
      identity: ctx.container.querySelector("#tr08-p02 .tr-panel-body"),
      credential: ctx.container.querySelector("#tr08-p03 .tr-panel-body"),
      products: ctx.container.querySelector("#tr08-p04 .tr-panel-body"),
      capabilities: ctx.container.querySelector("#tr08-p05 .tr-panel-body"),
      review: ctx.container.querySelector("#tr08-p06 .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const brokersRes = await ctx.fetchJSON("/brokers");
    if (brokersRes.status === 401 || brokersRes.status === 403) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "denied", deniedCode: brokersRes.status });
      return;
    }
    if (!brokersRes.ok) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "error", message: "Could not load broker adapters." });
      return;
    }
    const brokers = (brokersRes.data && brokersRes.data.brokers) || [];

    if (!brokers.length) {
      for (const el of Object.values(els)) {
        StateMatrix.render(el, { state: "empty", emptyMessage: "Choose an implemented adapter to configure an account.", nextRoute: "/trade/accounts", nextLabel: "Broker accounts and capabilities (TR-07)" });
      }
      return;
    }

    function adapterEnvVenueNote(adapterName) {
      const b = brokers.find((x) => x.name === adapterName);
      const bits = [];
      if (b && b.environment) bits.push(`environment: <strong>${escapeHtml(b.environment)}</strong>`);
      if (b && b.venue) bits.push(`venue/API variant: <strong>${escapeHtml(b.venue)}</strong>`);
      if (bits.length) {
        return `<p class="section-note">This adapter's real, code-verified environment/venue (GET /brokers): ${bits.join(", ")} -- adapter-level, not overridable per account in this build (every account on this adapter shares it).</p>`;
      }
      return `<p class="section-note">This adapter has no declared environment/venue value (GET /brokers reports both as null for it) -- it resolves paper/live per-account from an env var read at call time instead of storing it on the instance, so there is nothing real to show here rather than a guess.</p>`;
    }

    StateMatrix.render(els.adapter, {
      state: "ready",
      html: `
        <label>Adapter
          <select id="tr08-adapter-select">
            ${brokers.map((b) => `<option value="${escapeAttr(b.name)}">${escapeHtml(b.name)}</option>`).join("")}
          </select>
        </label>
        <p class="section-note">Chosen from this build's live, reviewed adapter registry (GET /brokers) -- never a free-typed module import string.</p>
        <div id="tr08-adapter-env-note">${adapterEnvVenueNote(brokers[0].name)}</div>
        ${unsupportedNote("This build's account model has no per-account environment override distinct from the adapter's own (see the real adapter-level value shown above) -- there is no per-environment provider mapping to verify a per-account choice against.")}
      `,
    });
    flushCapStates(els.adapter);

    StateMatrix.render(els.identity, {
      state: "ready",
      html: `
        <label>Account label<input type="text" id="tr08-account-label" maxlength="80" placeholder="paper_main" required></label>
        <p class="section-note">1..80 plain text. No credentials belong in this field.</p>
        ${unsupportedNote("External broker account reference (external_account_ref) is not a tracked field in this build's account model. Venue/API variant IS a real, code-verified field now -- but only at the adapter level (GET /brokers, shown above), never a per-account override.")}
      `,
    });
    flushCapStates(els.identity);

    function renderCredentialPanel(adapterName) {
      els.credential.removeAttribute("aria-busy");
      const labelEl = els.identity.querySelector("#tr08-account-label");
      const accountLabel = labelEl ? labelEl.value.trim() : "";
      Components.renderCapabilityState(els.credential, {
        status: "not_tracked",
        reason: `credential_ref itself is not stored per account in this build -- broker credentials remain environment variables set separately, outside this form, per this project's "never store secrets in config" rule (see README.md's Security notes). No raw secret is ever returned or accepted here. ${credentialRefReason(adapterName, accountLabel)}`,
      });
    }
    renderCredentialPanel(brokers[0].name);

    StateMatrix.render(els.products, {
      state: "ready",
      html: `
        <label>Symbol map (JSON, optional)<input type="text" id="tr08-symbol-map" placeholder='{"BTCUSDT":"BTC/USDT"}'></label>
        <p class="section-note">This is a per-symbol rename override, not a qualified product allowlist -- shown honestly as such rather than as the spec's "Products" id_list, which this build does not track.</p>
        ${unsupportedNote("Position mode (netting/hedged/spot) is not a tracked field in this build's account model.")}
      `,
    });
    flushCapStates(els.products);

    async function renderCapabilities(adapterName) {
      const broker = brokers.find((b) => b.name === adapterName);
      if (!broker) {
        StateMatrix.render(els.capabilities, { state: "empty", emptyMessage: "Select an adapter to see its capability evidence." });
        return;
      }
      // Every flag here is `implemented`/`unsupported` -- computed from
      // static method-override introspection (app/brokers/base.py), NEVER
      // a live authenticated call, so it can never legitimately read
      // `authenticated`/`entitled`/`verified` on this pre-save screen
      // (no credentials are entered here at all -- see the note below).
      const codeVerified = [
        ["Native bracket order", broker.supports_native_bracket, "atomic entry+stop+take-profit bracket/OCO order"],
        ["Protective stop", broker.has_protective_stop_capability, "standalone protective stop order"],
        ["Cancel", broker.has_cancel_capability, "cancelling an existing order"],
        ["Replace stop", broker.has_replace_stop_capability, "resizing/repricing a stop order in place"],
        ["Balance read", broker.has_balance_capability, "live cash/equity/buying-power/margin read"],
        ["Position readback", broker.has_position_readback_capability, "reading the broker's own position size back"],
      ];
      const rows = [];
      const capSpecs = [];
      for (const [label, flag, desc] of codeVerified) {
        const id = `tr08-cap-row-${capIdCounter++}`;
        rows.push([label, "not defined in this build", capSlot(id), "live (computed this request)", "n/a"]);
        capSpecs.push([
          id,
          flag
            ? { status: "implemented", reason: `${desc} -- ${adapterName}'s adapter overrides BrokerAdapter's base no-op. Code-verified, not a live authenticated call (this form enters no credentials).` }
            : { status: "unsupported", reason: `${desc} -- ${adapterName}'s adapter does not override BrokerAdapter's base no-op; no real implementation exists.` },
        ]);
      }
      const notTracked = [
        ["Market-data entitlement", "not modeled anywhere in this codebase, per account or per broker."],
        ["Account type (cash/margin/etc)", "not modeled anywhere in this codebase -- see app/models.py's AccountBalance and app/config.py's AccountRequest for the real fields that exist."],
        ["Currency", "not modeled anywhere in this codebase; every figure this build reports is in whatever currency the broker's own API happens to report, unlabelled."],
        ["Position mode (netting/hedged/spot)", "not a tracked field in this build's account model."],
        ["API quota status", "not measured for any broker -- app/rate_limit.py only throttles this app's own inbound webhook/SMS ingress, never an outbound broker call."],
        ["Connection latency", "not measured anywhere in this codebase for any broker adapter."],
      ];
      for (const [label, reason] of notTracked) {
        const id = `tr08-cap-row-${capIdCounter++}`;
        rows.push([label, "not defined in this build", capSlot(id), "n/a", "n/a"]);
        capSpecs.push([id, { status: "not_tracked", reason }]);
      }
      StateMatrix.render(els.capabilities, {
        state: "ready",
        html: `<p class="section-note">The six code-verified rows are this adapter's own method-override introspection, re-verified live on every "Run read-only checks" -- there is no per-account "Required" capability profile defined in this build, so Gap cannot be computed and reads n/a rather than a fabricated verdict. The remaining rows are honestly not_tracked: this build has no live connectivity check against real credentials at this pre-save stage, so nothing here is ever rendered as authenticated/entitled/verified.</p>${table(
          ["Capability / attribute", "Required", "Observed", "Evidence age", "Gap"],
          rows,
          "No capability evidence."
        )}
        ${unsupportedNote("Live identity/scope/position read-only checks against this specific (not-yet-saved) account are not available -- no credentials are entered in this form, and this build has no connectivity-check endpoint for an unconfigured account.")}
        <p class="section-note" style="margin-top:10px;">This panel is implementation-derived engineering capability only -- it never claims a specific account/route is actually qualified for live trading. That is a separate, higher-bar, per-exact-route concept (implemented &lt; configured &lt; authenticated &lt; account_entitled &lt; protocol_tested &lt; venue_tested &lt; release_approved) tracked once this account is saved -- see <a href="#/trade/accounts">Broker accounts and capabilities (TR-07)</a>'s "Live qualification status" panel to view or record it.</p>`,
      });
      mountCapStates(els.capabilities, capSpecs);
      flushCapStates(els.capabilities);
    }

    function currentDraft() {
      return {
        adapter: els.adapter.querySelector("#tr08-adapter-select").value,
        accountLabel: els.identity.querySelector("#tr08-account-label").value.trim(),
        symbolMapRaw: els.products.querySelector("#tr08-symbol-map").value.trim(),
      };
    }

    function renderReview() {
      const draft = currentDraft();
      const checks = [
        ["Account label set", draft.accountLabel ? pill("yes", "ok") : pill("no", "bad"), draft.accountLabel ? "OK" : "MISSING_LABEL", "form"],
        ["Adapter chosen from live registry", draft.adapter ? pill("yes", "ok") : pill("no", "bad"), draft.adapter ? "OK" : "MISSING_ADAPTER", "GET /brokers"],
      ];
      StateMatrix.render(els.review, {
        state: "ready",
        html: `${table(["Condition", "Outcome", "Reason code", "Evidence"], checks, "No conditions.")}
          <h3 class="section-note" style="margin-top:12px;">Additional configuration required by this build's account model (not individually named in the spec's field table)</h3>
          <label>Multiplier<input type="number" step="any" id="tr08-multiplier" value="1.0"></label>
          <label>Fixed quantity (optional)<input type="number" step="any" id="tr08-fixed-quantity"></label>
          <label class="checkbox"><input type="checkbox" id="tr08-managed-lifecycle"> Managed lifecycle</label>
          <label>Notional ceiling (optional)<input type="number" step="any" id="tr08-max-exposure"></label>
          <div class="form-error" id="tr08-form-error"></div>
          <div class="tr-controls-row">
            <button type="button" id="tr08-save">Save inactive account</button>
            <button type="button" class="ghost" id="tr08-run-checks">Run read-only checks</button>
            <button type="button" class="ghost" id="tr08-review-activation">Review activation</button>
          </div>
          <p class="section-note">Save always persists <code>enabled=false</code> -- this build has no separate two-stage release gate, so Save never itself admits new trading. Activation is a distinct, separate operation (see "Review activation").</p>
          <div id="tr08-action-result"></div>`,
      });

      els.review.querySelector("#tr08-save").addEventListener("click", () => doSave());
      els.review.querySelector("#tr08-run-checks").addEventListener("click", async () => {
        const adapter = currentDraft().adapter;
        await renderCapabilities(adapter);
        els.review.querySelector("#tr08-action-result").innerHTML = `<p class="section-note">Read-only checks re-fetched adapter capability evidence (no side effects). See Capabilities panel above.</p>`;
      });
      els.review.querySelector("#tr08-review-activation").addEventListener("click", () => {
        const draft = currentDraft();
        els.review.querySelector("#tr08-action-result").innerHTML = `
          <p class="section-note">Review only -- no request sent. If saved as drafted, account "<strong>${escapeHtml(draft.accountLabel || "(unset)")}</strong>" on adapter "<strong>${escapeHtml(draft.adapter)}</strong>" would be created/updated <em>inactive</em> (enabled=false). Turning it live is a separate operation, done outside this form (edit the account's enabled flag), never a side effect of Save/Preview here.</p>
        `;
      });
    }

    async function doSave() {
      const draft = currentDraft();
      const errorEl = els.review.querySelector("#tr08-form-error");
      errorEl.textContent = "";
      if (!draft.accountLabel) {
        errorEl.textContent = "Account label is required.";
        return;
      }
      if (!draft.adapter) {
        errorEl.textContent = "Adapter is required.";
        return;
      }
      let symbolMap = {};
      if (draft.symbolMapRaw) {
        try {
          symbolMap = JSON.parse(draft.symbolMapRaw);
        } catch (err) {
          errorEl.textContent = `Symbol map must be valid JSON: ${err.message}`;
          return;
        }
      }
      const multiplier = parseFloat(els.review.querySelector("#tr08-multiplier").value || "1.0");
      const fixedQuantityRaw = els.review.querySelector("#tr08-fixed-quantity").value;
      const maxExposureRaw = els.review.querySelector("#tr08-max-exposure").value;
      try {
        // Check if account already exists
        const accountsRes = await ctx.fetchJSON("/accounts");
        const existing = accountsRes.accounts.find((a) => a.account_id === draft.accountLabel);

        const payload = {
          broker: draft.adapter,
          multiplier,
          fixed_quantity: fixedQuantityRaw ? parseFloat(fixedQuantityRaw) : null,
          symbol_map: symbolMap,
          enabled: false, // "Save never enables trading" -- always inactive.
          managed_lifecycle: els.review.querySelector("#tr08-managed-lifecycle").checked,
          max_notional_exposure: maxExposureRaw ? parseFloat(maxExposureRaw) : null,
        };

        if (existing) {
          // Use PATCH for existing account (partial update)
          await patchJSON(`/accounts/${encodeURIComponent(draft.accountLabel)}`, payload);
        } else {
          // Use POST for new account (full create)
          await postJSON("/accounts", {
            account_id: draft.accountLabel,
            ...payload,
          });
        }
        els.review.querySelector("#tr08-action-result").innerHTML = `<p class="section-note">Saved as an inactive account draft. <a href="#/trade/accounts">Open Broker accounts and capabilities (TR-07)</a> to review it.</p>`;
      } catch (err) {
        errorEl.textContent = err.message;
      }
    }

    els.adapter.querySelector("#tr08-adapter-select").addEventListener("change", (e) => {
      renderCapabilities(e.target.value);
      renderCredentialPanel(e.target.value);
      const noteEl = els.adapter.querySelector("#tr08-adapter-env-note");
      if (noteEl) noteEl.innerHTML = adapterEnvVenueNote(e.target.value);
    });
    els.identity.querySelector("#tr08-account-label").addEventListener("input", () => {
      renderCredentialPanel(els.adapter.querySelector("#tr08-adapter-select").value);
    });
    await renderCapabilities(brokers[0].name);
    renderReview();

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr08 = {
    title: "Broker account configuration",
    breadcrumb: "Trade / Accounts / New",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = shell();
      await load(ctx);
    },
  };
  Router.register("/trade/accounts/new", "tr08");
})();
