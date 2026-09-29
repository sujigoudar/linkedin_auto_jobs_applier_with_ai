/* TR-07: Broker accounts and capabilities (`#/trade/accounts`).
 *
 * Real backing data: GET /accounts (config_accounts -- every live-managed
 * destination account) joined by broker name to GET /brokers (every
 * registered adapter's code-verified capability flags, plus its real
 * environment/venue -- see that route's own docstring: computed from
 * whether the adapter overrides the base no-op, not a name/imported-SDK
 * claim). Balance/permission state: GET /accounts/{id}/balance per
 * configured account, same call TR-01 already makes for its own risk
 * cards. Open-order count and reconciliation status: GET /orders and
 * GET /positions, scoped per account -- the exact same fields/derivation
 * TR-13 (Reconciliation and trading incidents) already uses, reused here
 * rather than re-implemented.
 *
 * Granular capability status (design review, 2026-09): every capability
 * on this screen is rendered through Components.renderCapabilityState
 * with the CORRECT rung of implemented < configured < authenticated <
 * entitled < verified -- never collapsed to a flat yes/no, and never
 * upgraded past what's actually been checked:
 *   - The six GET /brokers flags (native bracket, protective stop,
 *     cancel, replace stop, balance read, position readback) are
 *     `implemented` when true, `unsupported` when false -- they are
 *     computed from static method-override introspection
 *     (app/brokers/base.py), NOT from a live authenticated call, so
 *     they can never legitimately read `authenticated`/`verified` here.
 *   - "Last successful authenticated read" is the one place a real live
 *     authenticated call actually happens (GET /accounts/{id}/balance).
 *     When it just returned real (non-null) data, that specific check
 *     reads `authenticated` with a real `lastVerified` timestamp (this
 *     load). When the adapter has the capability in code but the live
 *     call returned nothing (missing/bad credentials, or the call
 *     failed), it stays at `implemented` -- never bumped to
 *     `authenticated` on a guess.
 *
 * Honest gaps, disclosed rather than worked around (still real per this
 * exact build, re-checked against app/brokers/*.py and app/config.py --
 * see the credential-reference/account-attributes notes below): Writer
 * site has no field anywhere in this schema. Venue/API variant and
 * Environment ARE real, adapter-level fields now (GET /brokers), but are
 * NOT overridable per account -- shown as the adapter's own value with
 * that caveat, not invented per-account state. Credential reference is
 * never a stored field (this project never puts secrets in config) but
 * this build's own per-broker env-var NAMING CONVENTION is real,
 * source-verified information -- surfaced as such, never a secret value,
 * never claimed as proof the account is actually configured. Market-data
 * entitlement, account type, currency and position mode are not modeled
 * anywhere in this codebase (per account OR per broker) -- honestly
 * not_tracked, cross-referencing the one real, adjacent figure this
 * build does have (maintenance_margin, in the Balance panel). Connection
 * latency and API quota status are not measured anywhere either
 * (app/rate_limit.py only throttles inbound webhook/SMS ingress, never
 * an outbound broker call) -- not_tracked, not fabricated. Products is
 * approximated from `symbol_map` (a rename map, not a product
 * allowlist) and labelled accordingly, never presented as a real
 * restriction list.
 *
 * Live qualification status (design review, 2026-09, audit follow-up):
 * a SEPARATE panel below the Capability matrix renders GET /qualifications
 * -- app/qualification.py's per-EXACT-ROUTE ladder (implemented <
 * configured < authenticated < account_entitled < protocol_tested <
 * venue_tested < release_approved). This is deliberately NOT the same
 * concept as the Capability matrix above it: that matrix is
 * implementation-derived (does the adapter CLASS override a base no-op),
 * while this panel is venue-qualified evidence for one exact
 * (adapter_type, route_key, asset_class, product_type) tuple -- e.g. a
 * CCXT spot exchange and a CCXT perpetual exchange are different routes
 * here even though both run the identical CCXTBroker class and show
 * identical rows in the Capability matrix. Recording a new state
 * (POST /qualifications, owner-gated) is rejected by the server itself
 * (never just this screen) if a ladder prerequisite is missing, or if the
 * adapter structurally cannot provide the account/order/position feedback
 * `account_entitled` and above require (see
 * BrokerAdapter.has_account_order_position_feedback) -- SignalStack is the
 * concrete case that can never pass that gate (see its own module
 * docstring: a webhook accept is not a fill/position/balance
 * confirmation).
 *
 * TR-07-A03 "Pause new entries" is a REAL action, not a new financial
 * capability: `account.enabled=False` is an existing, already-tested
 * entry pause (see app/routing.py's `destinations_for` and
 * app/engine.py's `handle_signal`, both documented "EXE-10" inline) that
 * stops new admissions on this exact account while explicitly preserving
 * its ability to exit/flatten an already-open position (CLOSE signals
 * pass `include_disabled=True`). This screen calls the exact same,
 * already-tested `POST /accounts` upsert the legacy dashboard's Accounts
 * form and TR-08 both use -- it changes no other field on the account.
 */
(function () {
  "use strict";

  // See tr04.js for why this placeholder-slot pattern exists: capability-
  // state badges render into a real DOM element, but this view builds
  // table rows as one HTML string before insertion.
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
  // `_webhook_url_for`/`_exchange_for`) -- informational only. Never a
  // secret value, and never evidence that an account's env vars are
  // actually SET (this screen makes no attempt to read them) -- see
  // credentialRefFor's own `reason` text, always disclosed alongside it.
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
  function credentialRefState(brokerName, accountId) {
    const upperId = String(accountId || "").toUpperCase();
    const pattern = CREDENTIAL_REF_PATTERNS[brokerName];
    if (pattern) {
      return {
        status: "not_tracked",
        reason: `This build never stores secrets in config; whether these env vars are actually set for this account is not checked by this screen. Real naming convention this adapter's code reads (never the secret value): ${pattern(upperId)}.`,
      };
    }
    if (brokerName === "ibkr") {
      return {
        status: "not_tracked",
        reason: "This adapter connects via process-wide config (IBKR_HOST/IBKR_PORT/IBKR_CLIENT_ID -- a local TWS/Gateway connection), not a per-account API key -- there is no per-account credential reference to show.",
      };
    }
    if (brokerName === "paper") {
      return { status: "not_tracked", reason: "In-process simulator -- no external credential of any kind to reference." };
    }
    return { status: "not_tracked", reason: `No known credential env-var convention for adapter "${brokerName}" in this build.` };
  }

  function venueEnvCell(broker, key) {
    const val = broker ? broker[key] : null;
    if (!val) return null;
    return pill(val, key === "environment" && val === "live" ? "warn" : "ok");
  }

  function lastReadCapState(broker, balanceOk, balanceData) {
    if (!broker || !broker.has_balance_capability) {
      return {
        status: "unsupported",
        reason: "This broker adapter has no real get_account_balance implementation (GET /brokers' has_balance_capability is false) -- there is no live authenticated read to report on for this account.",
      };
    }
    const hasRealData =
      balanceOk && balanceData && (balanceData.cash !== null || balanceData.equity !== null || balanceData.buying_power !== null);
    if (hasRealData) {
      const isPaper = broker.name === "paper";
      return {
        status: "authenticated",
        lastVerified: new Date().toISOString(),
        reason: isPaper
          ? "In-process simulator: this account's real, internally-tracked cash/buying-power ledger was just read successfully -- there is no external credential to authenticate against."
          : "This account's live GET /accounts/{id}/balance call to the real broker API just succeeded and returned real (non-null) cash/equity/buying-power fields -- confirms this account's configured credentials actually authenticate, as of this load.",
      };
    }
    return {
      status: "implemented",
      reason: "This adapter has a real get_account_balance implementation, but this account's live read just now returned no confirmed data (missing/invalid credentials, or the broker call failed) -- never upgraded to \"authenticated\" without a real successful call.",
    };
  }

  const ACCOUNT_ATTR_NOT_TRACKED = {
    status: "not_tracked",
    reason:
      "Market-data entitlement, account type, currency and position mode are not modeled anywhere in this codebase, per account or per broker (see app/models.py's AccountBalance and app/config.py's AccountRequest for the real fields that exist). Margin, where the broker's own live balance read reports it, is already shown for real in the Balance/permission state panel's Maintenance margin column below -- not repeated here as a separate invented flag.",
  };
  const QUOTA_LATENCY_NOT_TRACKED = {
    status: "not_tracked",
    reason:
      "Connection latency and API quota/rate-limit status are not measured or tracked anywhere in this codebase for any broker adapter -- app/rate_limit.py only throttles this app's own inbound webhook/SMS ingress, never an outbound call to a broker. Shown honestly as not tracked rather than a fabricated number.",
  };

  function shell() {
    return `
      <section class="tr-panel" id="tr07-p01"><h2>Accounts</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr07-p02"><h2>Engineering capability (implementation-derived)</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr07-p05"><h2>Live qualification status (per exact route)</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr07-p03"><h2>Balance/permission state</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr07-p04"><h2>Change review</h2><div class="tr-panel-body"></div></section>
    `;
  }

  async function load(ctx) {
    const els = {
      accounts: ctx.container.querySelector("#tr07-p01 .tr-panel-body"),
      capabilities: ctx.container.querySelector("#tr07-p02 .tr-panel-body"),
      qualification: ctx.container.querySelector("#tr07-p05 .tr-panel-body"),
      balance: ctx.container.querySelector("#tr07-p03 .tr-panel-body"),
      review: ctx.container.querySelector("#tr07-p04 .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const [accountsRes, brokersRes, positionsRes, qualificationsRes] = await Promise.all([
      ctx.fetchJSON("/accounts"),
      ctx.fetchJSON("/brokers"),
      ctx.fetchJSON("/positions"),
      ctx.fetchJSON("/qualifications"),
    ]);
    if (accountsRes.status === 401 || accountsRes.status === 403) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "denied", deniedCode: accountsRes.status });
      return;
    }
    if (!accountsRes.ok || !brokersRes.ok) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "error", message: "Could not load accounts/broker capabilities." });
      return;
    }

    const accounts = (accountsRes.data && accountsRes.data.accounts) || [];
    const brokers = (brokersRes.data && brokersRes.data.brokers) || [];
    const brokersByName = new Map(brokers.map((b) => [b.name, b]));
    const lifecycles = (positionsRes.ok && positionsRes.data && positionsRes.data.managed_lifecycles) || [];
    const allPositions = (positionsRes.ok && positionsRes.data && positionsRes.data.positions) || [];
    const hasOpenExposure = new Set(allPositions.filter((p) => p.net_quantity !== 0).map((p) => p.account_id));

    // --- Capability matrix (independent of account count -- always the
    // full registered-broker read model). Every one of the six code-
    // verified flags renders through Components.renderCapabilityState as
    // `implemented`/`unsupported` -- computed from method-override
    // introspection, never a live call, so never rendered as
    // `authenticated`/`verified` here (see this file's own docstring). ---
    if (!brokers.length) {
      StateMatrix.render(els.capabilities, { state: "empty", emptyMessage: "No broker adapters are registered in this build." });
    } else {
      const capColumns = [
        ["supports_native_bracket", "code-verified support for an atomic entry+stop+take-profit bracket/OCO order"],
        ["has_protective_stop_capability", "code-verified support for a standalone protective stop order"],
        ["has_cancel_capability", "code-verified support for cancelling an existing order"],
        ["has_replace_stop_capability", "code-verified support for resizing/repricing a stop order in place"],
        ["has_balance_capability", "code-verified support for a live cash/equity/buying-power/margin read"],
        ["has_position_readback_capability", "code-verified support for reading the broker's own position size back"],
      ];
      const capSpecs = [];
      const rows = brokers.map((b) => {
        const cells = [`<span class="mono" id="tr07-broker-${escapeAttr(b.name)}">${escapeHtml(b.name)}</span>`];
        for (const [flag, desc] of capColumns) {
          const slotId = `tr07-cap-${escapeAttr(b.name)}-${flag}`;
          cells.push(capSlot(slotId));
          capSpecs.push([
            slotId,
            b[flag]
              ? { status: "implemented", reason: `${desc} -- ${b.name}'s adapter overrides BrokerAdapter's base no-op (app/brokers/base.py). Code-verified, not a live authenticated call.` }
              : { status: "unsupported", reason: `${desc} -- ${b.name}'s adapter does not override BrokerAdapter's base no-op; no real implementation exists.` },
          ]);
        }
        cells.push(b.supported_asset_classes ? escapeHtml(b.supported_asset_classes.join(", ")) : pill("undeclared", "muted"));
        const attrSlotId = `tr07-cap-${escapeAttr(b.name)}-attrs`;
        cells.push(capSlot(attrSlotId));
        capSpecs.push([attrSlotId, QUOTA_LATENCY_NOT_TRACKED]);
        return cells;
      });
      StateMatrix.render(els.capabilities, {
        state: "ready",
        html: `<p class="section-note">Every capability flag is code-verified (computed from whether the adapter overrides the base no-op), not a name or imported-SDK claim -- see each badge's own "Why / details" for exactly what was checked. "Connection/quota" covers connection latency and API quota status, neither of which this codebase measures for any broker.</p>${table(
          ["Adapter", "Native bracket", "Protective stop", "Cancel", "Replace stop", "Balance read", "Position readback", "Allowed products (declared subset)", "Connection/quota"],
          rows,
          "No brokers registered."
        )}`,
      });
      mountCapStates(els.capabilities, capSpecs);
    }

    // --- Live qualification status (app/qualification.py) -- a SEPARATE,
    // higher-bar concept from the Capability matrix above: see this file's
    // own docstring. Rendered per exact route, never merged into the
    // capability matrix's rows. ---
    {
      const qualOk = qualificationsRes.ok;
      const ladder = (qualOk && qualificationsRes.data.ladder) || [];
      const routes = (qualOk && qualificationsRes.data.routes) || [];
      const qualSpecs = [];
      const routeRows = routes.map((r, i) => {
        const slotId = `tr07-qual-state-${i}`;
        qualSpecs.push([
          slotId,
          {
            status: r.current_state || "not_started",
            reason: `Highest live-qualification rung actually recorded for this exact route (${r.events.length} event(s) in its history). Ladder: ${ladder.join(" < ")}.`,
          },
        ]);
        return [
          `<span class="mono">${escapeHtml(r.adapter_type)}</span>`,
          `<span class="mono">${escapeHtml(r.route_key)}</span>`,
          escapeHtml(r.asset_class),
          escapeHtml(r.product_type),
          capSlot(slotId),
          fmtNum(r.events.length),
        ];
      });
      const brokerOptions = brokers.map((b) => `<option value="${escapeAttr(b.name)}">${escapeHtml(b.name)}</option>`).join("");
      const stateOptions = ladder.map((s) => `<option value="${escapeAttr(s)}">${escapeHtml(s)}</option>`).join("");
      StateMatrix.render(els.qualification, {
        state: "ready",
        html: `<p class="section-note">Separate from the Capability matrix above: this is real, persisted, per-exact-route evidence (adapter_type + route_key + asset_class + product_type), never inferred from method-override introspection. A CCXT spot exchange and a CCXT perpetual exchange track independently here even though both show identical rows above. The server rejects any state that skips a ladder prerequisite, and rejects account_entitled-or-higher outright for any adapter with no real order-status/position/balance feedback (SignalStack, concretely) -- see each badge's "Why / details".</p>${table(
          ["Adapter type", "Route key", "Asset class", "Product type", "Current state", "Events recorded"],
          routeRows,
          "No qualification records yet -- record the first one below."
        )}
        <h3 class="section-note" style="margin-top:14px;">Record a qualification event (owner-gated)</h3>
        <div class="tr-controls-row">
          <label>Adapter type<select id="tr07-qual-adapter">${brokerOptions}</select></label>
          <label>Route key<input type="text" id="tr07-qual-route" placeholder="e.g. ccxt_binance_spot" maxlength="200"></label>
          <label>Asset class
            <select id="tr07-qual-asset">
              <option value="crypto">crypto</option>
              <option value="forex">forex</option>
              <option value="equity">equity</option>
              <option value="option">option</option>
              <option value="future">future</option>
            </select>
          </label>
          <label>Product type<input type="text" id="tr07-qual-product" placeholder="e.g. spot, perpetual, cash_equity" maxlength="80"></label>
          <label>State<select id="tr07-qual-state">${stateOptions}</select></label>
        </div>
        <label>Notes (optional)<input type="text" id="tr07-qual-notes" maxlength="2000"></label>
        <div class="form-error" id="tr07-qual-error"></div>
        <div class="tr-controls-row"><button type="button" id="tr07-qual-record">Record state</button></div>
        <div id="tr07-qual-result"></div>`,
      });
      mountCapStates(els.qualification, qualSpecs);

      const recordBtn = els.qualification.querySelector("#tr07-qual-record");
      if (recordBtn) {
        recordBtn.addEventListener("click", async () => {
          const errorEl = els.qualification.querySelector("#tr07-qual-error");
          errorEl.textContent = "";
          const routeKey = els.qualification.querySelector("#tr07-qual-route").value.trim();
          if (!routeKey) {
            errorEl.textContent = "Route key is required.";
            return;
          }
          const body = {
            adapter_type: els.qualification.querySelector("#tr07-qual-adapter").value,
            route_key: routeKey,
            asset_class: els.qualification.querySelector("#tr07-qual-asset").value,
            product_type: els.qualification.querySelector("#tr07-qual-product").value.trim() || "unspecified",
            state: els.qualification.querySelector("#tr07-qual-state").value,
            notes: els.qualification.querySelector("#tr07-qual-notes").value.trim() || null,
          };
          try {
            await postJSON("/qualifications", body);
            els.qualification.querySelector("#tr07-qual-result").innerHTML = `<p class="section-note">Recorded.</p>`;
          } catch (err) {
            errorEl.textContent = err.message;
            return;
          }
          await load(ctx);
        });
      }
    }

    if (!accounts.length) {
      StateMatrix.render(els.accounts, {
        state: "empty",
        emptyMessage: "No accounts are configured.",
        nextRoute: "/trade/accounts/new",
        nextLabel: "Broker account configuration (TR-08)",
      });
      StateMatrix.render(els.balance, { state: "empty", emptyMessage: "No accounts are configured." });
      StateMatrix.render(els.review, { state: "empty", emptyMessage: "No accounts are configured." });
      ctx.setChrome({ asOf: new Date().toISOString() });
      return;
    }

    // --- Per-account orders read: real open-order count and reconciliation
    // status, both scoped to this exact account, reusing the same fields/
    // derivation TR-13 (Reconciliation and trading incidents) already
    // relies on rather than re-deriving anything new. ---
    const ordersPerAccount = await Promise.all(
      accounts.map((a) => ctx.fetchJSON(`/orders?limit=100&account_id=${encodeURIComponent(a.account_id)}`))
    );

    const accountRows = accounts.map((a, i) => {
      const broker = brokersByName.get(a.broker);
      const products = Object.keys(a.symbol_map || {}).length
        ? `${Object.keys(a.symbol_map).length} symbol mapping(s)`
        : pill("no product allowlist (not restricted)", "muted");
      const qualification = !broker
        ? pill("broker not registered", "bad")
        : a.managed_lifecycle
        ? boolPill(broker.can_protect_a_managed_position, "qualified for managed lifecycle", "not qualified -- missing protective stop")
        : pill("qualified (plain account, no protection required)", "ok");

      // P0-5: real, persisted per-account fields now (app/models.py's
      // DestinationAccount.management_recipe/qualification_level/
      // exclusive_writer_qualified) -- not re-derived from
      // managed_lifecycle alone. A mismatch between management_recipe and
      // managed_lifecycle is a real misconfiguration surfaced as a
      // warning, not silently resolved.
      const recipeMismatch =
        (a.management_recipe === "full_managed_lifecycle") !== Boolean(a.managed_lifecycle);
      const recipeCell = recipeMismatch
        ? pill(`${escapeHtml(a.management_recipe || "unset")} (disagrees with managed_lifecycle=${a.managed_lifecycle})`, "bad")
        : pill(escapeHtml(a.management_recipe || "unset"), a.management_recipe === "full_managed_lifecycle" ? "ok" : "muted");
      const qualLevelCell = a.qualification_level
        ? pill(escapeHtml(a.qualification_level), "muted")
        : pill("not declared", "muted");

      const ordersRes = ordersPerAccount[i];
      const openOrders = ordersRes.ok ? ordersRes.data.unreconciled_order_count : null;
      const recentOrders = (ordersRes.ok && ordersRes.data.orders) || [];
      const rejectedCount = recentOrders.filter((o) => o.status === "rejected").length;
      const acctHalted = lifecycles.filter((l) => l.account_id === a.account_id && l.halted);
      const acctUncovered = lifecycles.filter((l) => l.account_id === a.account_id && !l.halted && l.uncovered_quantity > 0);
      let reconciliation;
      if (acctHalted.length) reconciliation = pill(`halted (${acctHalted.length})`, "bad");
      else if (acctUncovered.length) reconciliation = pill(`protection deficit (${acctUncovered.length})`, "warn");
      else if (rejectedCount) reconciliation = pill(`${rejectedCount} rejected order(s)`, "warn");
      else reconciliation = pill("clean", "ok");

      const venueCell = venueEnvCell(broker, "venue");
      const envCell = venueEnvCell(broker, "environment");

      return [
        `<a class="mono" href="#tr07-broker-${escapeAttr(a.broker)}" data-open-capability="${escapeAttr(a.broker)}">${escapeHtml(a.account_id)}</a>`,
        `<span class="mono">${escapeHtml(a.broker)}</span>`,
        venueCell !== null ? `${venueCell}<div class="section-note">Adapter-level (GET /brokers) -- not overridable per account in this build.</div>` : capSlot(`tr07-cap-venue-${escapeAttr(a.account_id)}`),
        envCell !== null ? `${envCell}<div class="section-note">Adapter-level (GET /brokers) -- not overridable per account in this build.</div>` : capSlot(`tr07-cap-env-${escapeAttr(a.account_id)}`),
        capSlot(`tr07-cap-cred-${escapeAttr(a.account_id)}`),
        capSlot(`tr07-cap-attrs-${escapeAttr(a.account_id)}`),
        products,
        broker ? pill("registered adapter", "ok") : pill("no adapter registered", "bad"),
        typeof qualification === "string" ? qualification : qualification,
        recipeCell,
        qualLevelCell,
        openOrders === null ? pill("unknown", "muted") : fmtNum(openOrders),
        reconciliation,
        capSlot(`tr07-cap-writer-${escapeAttr(a.account_id)}`),
      ];
    });
    StateMatrix.render(els.accounts, {
      state: "ready",
      html: `<p class="section-note">Products is approximated from each account's symbol_map (a rename map, not a real product allowlist). Open orders and Reconciliation are real, computed from this exact account's own GET /orders / GET /positions state (same derivation TR-13 uses) -- click an account to jump to its adapter's row in the Capability matrix below. Management recipe and Qualification level (P0-5) are this account's own explicit, persisted declaration (app/models.py's DestinationAccount.management_recipe/qualification_level) -- not re-derived from managed_lifecycle each time a screen needs it; a mismatch against managed_lifecycle is flagged, not silently resolved. Close reconciliation (also P0-5) is what a plain account's CLOSE now requires before it's allowed to act on this service's own tracked position -- see that badge's own "Why / details".</p>${table(
        ["Account", "Adapter", "Venue/API", "Environment", "Credential reference", "Account attributes", "Products", "Connection", "Qualification", "Management recipe", "Qualification level", "Open orders (pending)", "Reconciliation", "Close reconciliation"],
        accountRows,
        "No accounts."
      )}`,
    });
    mountCapStates(
      els.accounts,
      accounts.flatMap((a) => {
        const broker = brokersByName.get(a.broker);
        const specs = [
          [`tr07-cap-cred-${escapeAttr(a.account_id)}`, credentialRefState(a.broker, a.account_id)],
          [`tr07-cap-attrs-${escapeAttr(a.account_id)}`, ACCOUNT_ATTR_NOT_TRACKED],
        ];
        if (!venueEnvCell(broker, "venue")) {
          specs.push([`tr07-cap-venue-${escapeAttr(a.account_id)}`, { status: "not_tracked", reason: "This adapter has no declared venue value (GET /brokers' venue is null for it in this build)." }]);
        }
        if (!venueEnvCell(broker, "environment")) {
          specs.push([`tr07-cap-env-${escapeAttr(a.account_id)}`, { status: "not_tracked", reason: "This adapter resolves paper/live per-account from an env var read at call time rather than storing it on the instance -- GET /brokers' environment is null for it, so this screen shows the honest gap rather than a guess." }]);
        }
        // P0-5: real, computed close-reconciliation state for a plain
        // (non-managed_lifecycle) account -- see
        // SignalCopierEngine._reconcile_before_plain_close for the exact
        // contract this describes. Managed_lifecycle accounts don't go
        // through this gate at all (CLOSE resolves against CloseArbiter
        // instead), so this stays not_tracked/not-applicable for them.
        if (a.managed_lifecycle) {
          specs.push([
            `tr07-cap-writer-${escapeAttr(a.account_id)}`,
            { status: "not_tracked", reason: "This account is managed_lifecycle -- its CLOSE resolves against PositionLifecycleManager's CloseArbiter, not the plain-account broker-reconciliation gate this badge describes." },
          ]);
        } else if (broker && broker.has_position_readback_capability) {
          specs.push([
            `tr07-cap-writer-${escapeAttr(a.account_id)}`,
            {
              status: "implemented",
              reason: `Broker '${a.broker}' has a real get_broker_position implementation -- a plain CLOSE on this account is reconciled against a fresh broker readback before it's allowed to proceed (rejected on a mismatch), never against this service's own tracked position alone.`,
            },
          ]);
        } else if (a.exclusive_writer_qualified) {
          specs.push([
            `tr07-cap-writer-${escapeAttr(a.account_id)}`,
            {
              status: "configured",
              reason: `Broker '${a.broker}' has no verified position-readback capability, so this account's own exclusive_writer_qualified=true is what allows a plain CLOSE to proceed -- an explicit operator assertion that nothing else writes to this account's position outside Signal Copier, not a live-verified fact.`,
            },
          ]);
        } else {
          specs.push([
            `tr07-cap-writer-${escapeAttr(a.account_id)}`,
            {
              status: "unsupported",
              reason: `Broker '${a.broker}' has no verified position-readback capability and this account is not exclusive_writer_qualified -- a plain CLOSE on this account is currently BLOCKED (fail-closed) until one of those is true. See README.md's "Exclusive-writer qualification" section.`,
            },
          ]);
        }
        return specs;
      })
    );
    els.accounts.querySelectorAll("[data-open-capability]").forEach((a) => {
      a.addEventListener("click", () => {
        const target = els.capabilities.querySelector(`#tr07-broker-${CSS.escape(a.getAttribute("data-open-capability"))}`);
        if (target) target.scrollIntoView({ behavior: "smooth", block: "center" });
      });
    });

    // --- Balance/permission state ---
    const balances = await Promise.all(accounts.map((a) => ctx.fetchJSON(`/accounts/${encodeURIComponent(a.account_id)}/balance`)));
    const balanceRows = accounts.map((a, i) => [
      `<span class="mono">${escapeHtml(a.account_id)}</span>`,
      balances[i].ok && balances[i].data.equity !== null && balances[i].data.equity !== undefined ? fmtNum(balances[i].data.equity) : pill("unknown", "muted"),
      balances[i].ok && balances[i].data.buying_power !== null && balances[i].data.buying_power !== undefined ? fmtNum(balances[i].data.buying_power) : pill("unknown / not applicable", "muted"),
      balances[i].ok && balances[i].data.maintenance_margin !== null && balances[i].data.maintenance_margin !== undefined ? fmtNum(balances[i].data.maintenance_margin) : pill("unknown / not applicable", "muted"),
      balances[i].ok ? pill("reachable", "ok") : pill("unreachable", "bad"),
      capSlot(`tr07-cap-lastread-${escapeAttr(a.account_id)}`),
      capSlot(`tr07-cap-quota-${escapeAttr(a.account_id)}`),
    ]);
    StateMatrix.render(els.balance, {
      state: "ready",
      html: `<p class="section-note">Buying power is real for the paper broker (a genuinely computed simulated cash ledger) and any other broker whose adapter has a real get_account_balance implementation returning it -- unknown/not applicable elsewhere, never a guessed figure.</p>${table(
        ["Account", "Equity", "Buying power", "Maintenance margin", "Connection", "Last successful authenticated read", "Connection latency / API quota"],
        balanceRows,
        "No accounts."
      )}`,
    });
    mountCapStates(
      els.balance,
      accounts.flatMap((a, i) => {
        const broker = brokersByName.get(a.broker);
        return [
          [`tr07-cap-lastread-${escapeAttr(a.account_id)}`, lastReadCapState(broker, balances[i].ok, balances[i].ok ? balances[i].data : null)],
          [`tr07-cap-quota-${escapeAttr(a.account_id)}`, QUOTA_LATENCY_NOT_TRACKED],
        ];
      })
    );

    // --- Change review checklist ---
    const reviewRows = accounts.flatMap((a) => {
      const broker = brokersByName.get(a.broker);
      const exposed = hasOpenExposure.has(a.account_id) || lifecycles.some((l) => l.account_id === a.account_id);
      return [
        [`${escapeHtml(a.account_id)}: broker registered`, broker ? pill("yes", "ok") : pill("no", "bad"), broker ? "OK" : "NO_ADAPTER", "GET /brokers (live)"],
        [`${escapeHtml(a.account_id)}: new entries admitted`, boolPill(a.enabled, "admitted", "paused"), a.enabled ? "OK" : "PAUSED", "GET /accounts (enabled)"],
        [`${escapeHtml(a.account_id)}: has open exposure`, boolPill(exposed, "yes", "no"), exposed ? "HAS_EXPOSURE" : "FLAT", "GET /positions (live)"],
      ];
    });
    const actionRows = accounts.map((a) => `
      <div class="tr-controls-row">
        <span class="mono">${escapeHtml(a.account_id)}</span>
        <button type="button" class="danger" data-pause-account="${escapeAttr(a.account_id)}" ${a.enabled ? "" : "disabled"}>Pause new entries</button>
        ${a.enabled ? "" : `<span class="section-note">Already paused.</span>`}
      </div>
    `).join("");
    StateMatrix.render(els.review, {
      state: "ready",
      html: `${table(["Condition", "Outcome", "Reason code", "Evidence"], reviewRows, "No conditions.")}
             <h3 class="section-note" style="margin-top:14px;">Pause new entries (TR-07-A03)</h3>
             <p class="section-note">Sets this exact account's own <code>enabled=false</code> via the same, already-tested POST /accounts upsert -- preserves authorized exits/flattens/outstanding commands (EXE-10). No other field on the account is changed.</p>
             ${actionRows}`,
    });
    els.review.querySelectorAll("[data-pause-account]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const accountId = btn.getAttribute("data-pause-account");
        const acct = accounts.find((a) => a.account_id === accountId);
        if (!acct) return;
        if (!confirm(`Pause new entries on account "${accountId}"? This does not close or affect any existing position.`)) return;
        try {
          await postJSON("/accounts", {
            account_id: acct.account_id,
            broker: acct.broker,
            multiplier: acct.multiplier,
            fixed_quantity: acct.fixed_quantity,
            symbol_map: acct.symbol_map,
            enabled: false,
            managed_lifecycle: acct.managed_lifecycle,
            max_notional_exposure: acct.max_notional_exposure,
          });
        } catch (err) {
          alert(`Could not pause account: ${err.message}`);
        }
        await load(ctx);
      });
    });

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr07 = {
    title: "Broker accounts and capabilities",
    breadcrumb: "Trade / Accounts",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = shell();
      await load(ctx);
      ctx.registerPoll("tr07", 10000, () => load(ctx));
    },
  };
  Router.register("/trade/accounts", "tr07");
})();
