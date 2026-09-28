/* TR-09: Signal providers and collectors (`#/trade/sources`).
 *
 * Real backing data: GET /providers (every configured ProviderConfig +
 * its AnalystConfigs + the already-computed effective settings per
 * destination account -- app/providers.py), GET /routing-rules (whether a
 * provider's signals are actually admitted to any destination -- a
 * provider with no routing rule receives/records signals but never routes
 * them, see app/routing.py), GET /signals (the immutable, already-received
 * signal history, used honestly as "History jobs" -- this build has no
 * separate async "import job" concept, a received signal IS the durable
 * record), and the public GET /health worker-liveness read (no per-source
 * telemetry exists, so this is the closest real evidence for "Transport
 * health").
 *
 * Honest gaps, disclosed rather than invented: this build has no
 * transport_instance_id/channel_product_id/parser_version_id/cursor
 * registry and no rights_grant model anywhere (see app/providers.py,
 * app/db.py) -- those columns/panels render "not tracked in this build" /
 * StateMatrix "unsupported" rather than fabricated values. "Transport"
 * and "Parser" per source are inferred from a hand-maintained mirror of
 * this repo's app/sources/*.py module list (SOURCE_MODULES below, kept in
 * comments in sync with that directory) since no API exposes it live --
 * disclosed in the panel note. "Lag" is approximated as time-since-last-
 * received-signal (from GET /signals), explicitly labelled as NOT a real
 * collector/transport lag metric (no such telemetry is tracked).
 *
 * TR-09-A03 "Pause new admissions" reuses the exact same, already-tested
 * POST /providers/{provider_id} upsert TR-12 and the legacy dashboard's
 * Providers form both use (app/main.py's create_or_update_provider) --
 * sets this exact provider's enabled=false (SettingsOverride.enabled),
 * muting it at the provider level without touching any other provider,
 * analyst, or account setting. It's the provider-level analogue of TR-07's
 * account-level "Pause new entries" (same POST /accounts pattern).
 */
(function () {
  "use strict";

  // Hand-maintained mirror of app/sources/*.py -- see this file's module
  // docstring above for why (no live registry endpoint exists to query
  // this from). free_text: true means this module route its inbound
  // message through app/sources/text_parser.py's shared grammar (see that
  // module's own grep-verified import list); false means a structured
  // JSON/callback payload with no text grammar involved.
  const SOURCE_MODULES = {
    telegram: { transport: "Telegram bot (pull; needs TELEGRAM_BOT_TOKEN + restart)", free_text: true },
    discord: { transport: "Discord bot (pull; needs DISCORD_BOT_TOKEN + restart)", free_text: true },
    slack: { transport: "Slack app (pull; needs SLACK_BOT_TOKEN + restart)", free_text: true },
    whatsapp: { transport: "WhatsApp Cloud API webhook (push; POST /whatsapp/webhook)", free_text: true },
    sms_twilio: { transport: "Twilio SMS webhook (push; POST /sms/twilio)", free_text: true },
    twitter: { transport: "Twitter/X polling adapter (pull; needs credentials + restart)", free_text: true },
    ninjatrader: { transport: "NinjaTrader webhook (push; POST /ninjatrader/webhook)", free_text: false },
    mt4_mt5: { transport: "MT4/MT5 bridge (pull/polling)", free_text: false },
    rithmic: { transport: "Rithmic API adapter (pull)", free_text: false },
    webhook: { transport: "Generic webhook (push; POST /webhook/{source})", free_text: false },
  };

  function transportFor(providerId) {
    return SOURCE_MODULES[providerId] || null;
  }

  function shell() {
    return `
      <section class="tr-panel" id="tr09-p01"><h2>Sources</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr09-p02"><h2>Transport health</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr09-p03"><h2>Parser coverage</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr09-p04"><h2>Rights</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr09-p05"><h2>History jobs</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr09-actions"><h2>Actions</h2><div class="tr-panel-body"></div></section>
    `;
  }

  async function load(ctx) {
    const els = {
      sources: ctx.container.querySelector("#tr09-p01 .tr-panel-body"),
      transport: ctx.container.querySelector("#tr09-p02 .tr-panel-body"),
      parser: ctx.container.querySelector("#tr09-p03 .tr-panel-body"),
      rights: ctx.container.querySelector("#tr09-p04 .tr-panel-body"),
      history: ctx.container.querySelector("#tr09-p05 .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const [providersRes, routingRes, signalsRes, healthRes] = await Promise.all([
      ctx.fetchJSON("/providers"),
      ctx.fetchJSON("/routing-rules"),
      ctx.fetchJSON("/signals?limit=100"),
      ctx.fetchJSON("/health"),
    ]);
    if (providersRes.status === 401 || providersRes.status === 403) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "denied", deniedCode: providersRes.status });
      return;
    }
    if (!providersRes.ok) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "error", message: "Could not load providers." });
      return;
    }

    const providers = (providersRes.data && providersRes.data.providers) || [];
    const routingRules = (routingRes.ok && routingRes.data && routingRes.data.routing_rules) || [];
    const signals = (signalsRes.ok && signalsRes.data && signalsRes.data.signals) || [];

    if (!providers.length) {
      StateMatrix.render(els.sources, {
        state: "empty",
        emptyMessage: "No authorized source is configured.",
        nextRoute: "/trade/sources/new",
        nextLabel: "Source onboarding and parser laboratory (TR-10)",
      });
      for (const key of ["transport", "parser", "rights", "history"]) {
        StateMatrix.render(els[key], { state: "empty", emptyMessage: "No authorized source is configured." });
      }
      StateMatrix.render(ctx.container.querySelector("#tr09-actions .tr-panel-body"), {
        state: "empty",
        emptyMessage: "No authorized source is configured.",
        nextRoute: "/trade/sources/new",
        nextLabel: "Source onboarding and parser laboratory (TR-10)",
      });
      ctx.setChrome({ asOf: new Date().toISOString() });
      return;
    }

    const lastSeenBySource = new Map();
    for (const s of signals) {
      if (!lastSeenBySource.has(s.source)) lastSeenBySource.set(s.source, s.received_at);
    }

    // --- Sources table ---
    const sourceRows = providers.map((p) => {
      const mod = transportFor(p.provider_id);
      const hasRule = routingRules.some((r) => r.source === p.provider_id);
      const enabled = p.settings && p.settings.enabled !== false; // null/undefined inherits -> treated as enabled
      const lastSeen = lastSeenBySource.get(p.provider_id);
      const lag = lastSeen ? `${escapeHtml(lastSeen)} (time since last received signal, not a transport lag metric)` : pill("no signal received yet", "muted");
      let status;
      if (!enabled) status = pill("muted (provider-level)", "warn");
      else if (!hasRule) status = pill("admitting but no routing rule -- signals recorded, never routed", "warn");
      else status = pill("admitting and routed", "ok");
      return [
        `<span class="mono" id="tr09-source-${escapeAttr(p.provider_id)}">${escapeHtml(p.provider_id)}</span>`,
        mod ? escapeHtml(mod.transport) : `<span class="tr-not-tracked">not statically mapped</span>`,
        `<span class="tr-not-tracked">not tracked in this build</span>`,
        p.analysts.length ? `${p.analysts.length} analyst(s): ${escapeHtml(p.analysts.map((a) => a.analyst_id).join(", "))}` : pill("no analyst scope (source-wide)", "muted"),
        mod ? (mod.free_text ? "app/sources/text_parser.py (shared grammar)" : "structured payload (no text grammar)") : `<span class="tr-not-tracked">unknown</span>`,
        `<span class="tr-not-tracked">not tracked in this build</span>`,
        lag,
        status,
      ];
    });
    StateMatrix.render(els.sources, {
      state: "ready",
      html: `<p class="section-note">Transport/Parser are inferred from a hand-maintained mirror of this repo's app/sources/*.py list, not a live registry (see this file's own comment) -- Channel/product and Cursor are not tracked per source anywhere in this build.</p>${table(
        ["Source", "Transport", "Channel/product", "Analyst scope", "Parser", "Cursor", "Lag", "Status"],
        sourceRows,
        "No sources."
      )}`,
    });

    // --- Transport health ---
    if (healthRes.ok && healthRes.data) {
      const h = healthRes.data;
      StateMatrix.render(els.transport, {
        state: "unsupported",
        reason:
          `No per-source/transport connectivity telemetry is tracked in this build (no bot heartbeat, no per-collector cursor). ` +
          `The closest real evidence is this service's own worker liveness (public GET /health, service-wide, not per source): ` +
          `status=${escapeHtml(h.status)}, price_monitor_ok=${escapeHtml(String(h.price_monitor_ok))}, reconciler_ok=${escapeHtml(String(h.reconciler_ok))}.`,
      });
    } else {
      StateMatrix.render(els.transport, { state: "unsupported", reason: "No per-source/transport connectivity telemetry is tracked in this build, and the service-wide liveness check could not be reached either." });
    }

    // --- Parser coverage ---
    const freeText = providers.filter((p) => { const m = transportFor(p.provider_id); return m && m.free_text; });
    const structured = providers.filter((p) => { const m = transportFor(p.provider_id); return m && !m.free_text; });
    const unknown = providers.filter((p) => !transportFor(p.provider_id));
    StateMatrix.render(els.parser, {
      state: "ready",
      html: `
        <p class="section-note">Real, code-derived split of configured sources by ingestion parser (see app/sources/*.py). Use "Run parser validation" (TR-10) against real or pasted message text to see actual dispositions -- this panel only shows which grammar family applies, never a fabricated coverage percentage.</p>
        <ul>
          <li>Shared free-text grammar (app/sources/text_parser.py): ${freeText.length ? escapeHtml(freeText.map((p) => p.provider_id).join(", ")) : pill("none configured", "muted")}</li>
          <li>Structured payload sources (no text grammar involved): ${structured.length ? escapeHtml(structured.map((p) => p.provider_id).join(", ")) : pill("none configured", "muted")}</li>
          <li>Not statically mapped (unknown transport): ${unknown.length ? escapeHtml(unknown.map((p) => p.provider_id).join(", ")) : pill("none", "muted")}</li>
        </ul>
        <div class="tr-controls-row"><a href="#/trade/sources/new">Open parser laboratory (TR-10)</a></div>
      `,
    });

    // --- Rights ---
    StateMatrix.render(els.rights, {
      state: "unsupported",
      reason: "No rights/resale-grant model exists anywhere in this codebase (no rights_grant_id field, no commercial-use tracking table) -- there is nothing real to display here.",
    });

    // --- History jobs (real: GET /signals, immutable row ids) ---
    if (!signals.length) {
      StateMatrix.render(els.history, { state: "empty", emptyMessage: "No signal has been received from any source yet." });
    } else {
      const rows = signals.slice(0, 25).map((s) => [
        `<span class="mono">${escapeHtml(String(s.id))}</span>`,
        `<a href="#tr09-source-${escapeAttr(s.source)}">${escapeHtml(s.source)}</a>`,
        escapeHtml(s.symbol),
        escapeHtml(s.side),
        escapeHtml(s.received_at),
        pill("received (immutable record)", "ok"),
      ]);
      StateMatrix.render(els.history, {
        state: "ready",
        html: `<p class="section-note">This build has no separate async "import job" concept -- a received signal is itself the durable, immutable record. Showing the ${Math.min(25, signals.length)} most recent of ${signals.length} loaded (server allows up to 500 via GET /signals?limit=).</p>${table(
          ["Signal ID", "Source", "Symbol", "Side", "Received at", "Status"],
          rows,
          "No signals."
        )}`,
      });
    }

    // --- Actions row (TR-09-A01..A03) ---
    const actionsEl = ctx.container.querySelector("#tr09-actions .tr-panel-body");
    const actionRows = providers.map((p) => {
      const enabled = p.settings && p.settings.enabled !== false;
      return `
        <div class="tr-controls-row">
          <span class="mono">${escapeHtml(p.provider_id)}</span>
          <button type="button" class="danger" data-pause-source="${escapeAttr(p.provider_id)}" ${enabled ? "" : "disabled"}>Pause new admissions</button>
          ${enabled ? "" : `<span class="section-note">Already paused.</span>`}
        </div>
      `;
    }).join("");
    StateMatrix.render(actionsEl, {
      state: "ready",
      html: `
        <div class="tr-controls-row"><a href="#/trade/sources/new">Add source (TR-10)</a></div>
        <h3 class="section-note" style="margin-top:12px;">Pause new admissions (TR-09-A03)</h3>
        <p class="section-note">Sets this exact provider's own enabled=false via the same, already-tested POST /providers/{provider_id} upsert -- no other provider, analyst, or account setting is changed. Existing routing/exit behavior for open positions is unaffected.</p>
        ${actionRows}
      `,
    });
    actionsEl.querySelectorAll("[data-pause-source]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const providerId = btn.getAttribute("data-pause-source");
        const provider = providers.find((p) => p.provider_id === providerId);
        if (!provider) return;
        if (!confirm(`Pause new admissions for source "${providerId}"? This does not affect any existing position or order.`)) return;
        try {
          await postJSON(`/providers/${encodeURIComponent(providerId)}`, {
            display_name: provider.display_name || "",
            multiplier: provider.settings.multiplier,
            fixed_quantity: provider.settings.fixed_quantity,
            managed_lifecycle: provider.settings.managed_lifecycle,
            enabled: false,
          });
        } catch (err) {
          alert(`Could not pause source: ${err.message}`);
        }
        await load(ctx);
      });
    });

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr09 = {
    title: "Signal providers and collectors",
    breadcrumb: "Trade / Sources",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = shell();
      await load(ctx);
      ctx.registerPoll("tr09", 10000, () => load(ctx));
    },
  };
  Router.register("/trade/sources", "tr09");
})();
