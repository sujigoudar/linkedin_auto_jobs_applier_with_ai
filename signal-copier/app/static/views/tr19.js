/* TR-19: Provider catalog (`#/trade/provider-catalog`).
 *
 * Track 14 (app/provider_catalog.py, app/connections.py) shipped a real,
 * NEW provider/source/connection data model -- genuinely different from
 * the older app/providers.py ProviderConfig model TR-09 ("Signal
 * providers and collectors") reads -- and Track 21's onboarding wizard
 * (`#/trade/providers/add`, tr17.js) writes to it via POST /providers,
 * POST /connections, POST /sources. A live test confirmed there was
 * nowhere in the dashboard to see what that wizard created: TR-09 reads
 * the older model and shows nothing for a row created here. This view is
 * that missing screen, built only against the GET routes Track 14/21
 * actually expose:
 *   - GET /provider-catalog/providers (optionally ?status=...)
 *   - GET /provider-catalog/providers/{provider_id}
 *   - GET /provider-catalog/providers/{provider_id}/sources
 *   - GET /provider-catalog/sources/{source_id}
 *   - GET /provider-catalog/connections/{connection_id}
 *
 * status/execution_eligibility/certification_state/health_state/role are
 * shown EXACTLY as the API returns them (app/provider_catalog.py's
 * ProviderStatus/ExecutionEligibility/CertificationState/SourceHealth/
 * SourceRole enums) -- never renamed to a friendlier label that would
 * hide what they actually mean (e.g. "onboarding" stays "onboarding", not
 * "pending"; "disabled" execution_eligibility stays "disabled", not
 * silently implied to be "paused").
 *
 * This is a read-only catalog browser -- Track 14 shipped this section
 * GET-only by its own stated design ("enough to see it working, not a
 * dashboard page"), and the only write routes Track 21 added
 * (POST /providers / POST /sources / POST /connections) are already
 * exactly tr17.js's own creation wizard; this screen does not duplicate
 * that form, it is the place to see what it created and drill into each
 * provider's own sources/connection.
 */
(function () {
  "use strict";

  const STATUS_TONE = {
    live: "ok",
    certified: "ok",
    shadow: "warn",
    onboarding: "muted",
    paused: "warn",
    degraded: "bad",
    disabled: "bad",
  };
  const EXEC_ELIGIBILITY_TONE = { live: "ok", paper: "warn", shadow: "warn", disabled: "muted" };
  const CERT_TONE = { certified: "ok", shadow: "warn", tested: "warn", draft: "muted", uncertified: "muted" };
  const HEALTH_TONE = { healthy: "ok", degraded: "warn", unqualified: "muted", error: "bad", disabled: "muted" };

  function enumPill(value, toneMap) {
    return pill(value === null || value === undefined ? "unset" : value, toneMap[value] || "muted");
  }

  function shell() {
    return `
      <section class="tr-panel" id="tr19-p01"><h2>Providers</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr19-p02"><h2>Provider detail</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr19-p03"><h2>Sources for selected provider</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr19-p04"><h2>Source detail / connection</h2><div class="tr-panel-body"></div></section>
    `;
  }

  let selectedProviderId = null;
  let selectedSourceId = null;

  function renderProvidersTable(el, providers, ctx) {
    if (!providers.length) {
      StateMatrix.render(el, {
        state: "empty",
        emptyMessage: "No provider is registered in the provider-catalog data model (GET /provider-catalog/providers) yet.",
        nextRoute: "/trade/providers/add",
        nextLabel: "+Add Signal Provider wizard (TR-17)",
      });
      return;
    }
    const rows = providers.map((p) => [
      `<a href="#" class="tr19-provider-link" data-provider-id="${escapeAttr(p.id)}"><span class="mono">${escapeHtml(p.id)}</span></a>`,
      escapeHtml(p.display_name),
      enumPill(p.status, STATUS_TONE),
      enumPill(p.execution_eligibility, EXEC_ELIGIBILITY_TONE),
      enumPill(p.certification_state, CERT_TONE),
      p.classification ? escapeHtml(p.classification) : pill("unset", "muted"),
      p.account_ownership ? escapeHtml(p.account_ownership) : pill("unset", "muted"),
      escapeHtml(p.created_at || ""),
    ]);
    StateMatrix.render(el, {
      state: "ready",
      html: `<p class="section-note">Real rows from GET /provider-catalog/providers -- the data model TR-17's onboarding wizard writes to. Status/execution eligibility/certification state are shown exactly as the API returns them (app/provider_catalog.py's own enums), never relabeled. Click a provider to see its sources.</p>${table(
        ["Provider ID", "Display name", "Status", "Execution eligibility", "Certification state", "Classification", "Account ownership", "Created at"],
        rows,
        "No providers."
      )}`,
    });
    el.querySelectorAll(".tr19-provider-link").forEach((a) => {
      a.addEventListener("click", (e) => {
        e.preventDefault();
        selectedProviderId = a.dataset.providerId;
        selectedSourceId = null;
        loadProviderDetail(ctx);
      });
    });
  }

  function renderProviderDetail(el, provider) {
    StateMatrix.render(el, {
      state: "ready",
      html: `
        <ul>
          <li>ID: <span class="mono">${escapeHtml(provider.id)}</span></li>
          <li>Display name: ${escapeHtml(provider.display_name)}</li>
          <li>Status: ${enumPill(provider.status, STATUS_TONE)}</li>
          <li>Execution eligibility: ${enumPill(provider.execution_eligibility, EXEC_ELIGIBILITY_TONE)}</li>
          <li>Certification state: ${enumPill(provider.certification_state, CERT_TONE)}${provider.certification_version ? ` (version ${escapeHtml(provider.certification_version)})` : ""}</li>
          <li>Classification: ${provider.classification ? escapeHtml(provider.classification) : pill("unset", "muted")}</li>
          <li>Account ownership: ${provider.account_ownership ? escapeHtml(provider.account_ownership) : pill("unset", "muted")}</li>
          <li>Subscription status: ${provider.subscription_status ? escapeHtml(provider.subscription_status) : pill("unset", "muted")}</li>
          <li>Asset classes: ${provider.asset_classes.length ? provider.asset_classes.map(escapeHtml).join(", ") : pill("none set", "muted")}</li>
          <li>Strategy types: ${provider.strategy_types.length ? provider.strategy_types.map(escapeHtml).join(", ") : pill("none set", "muted")}</li>
          <li>Default parser profile: ${provider.default_parser_profile ? escapeHtml(provider.default_parser_profile) : pill("unset", "muted")}</li>
          <li>Risk policy ref: ${provider.risk_policy_ref ? escapeHtml(provider.risk_policy_ref) : pill("unset", "muted")}</li>
          <li>Operator notes: ${provider.operator_notes ? escapeHtml(provider.operator_notes) : pill("none", "muted")}</li>
          <li>Created / updated: <span class="mono">${escapeHtml(provider.created_at || "")}</span> / <span class="mono">${escapeHtml(provider.updated_at || "")}</span></li>
        </ul>
        <p class="section-note">This is GET /provider-catalog/providers/${escapeHtml(provider.id)} verbatim -- no field renamed or hidden. execution_eligibility="disabled" or certification_state="uncertified" means exactly that: this provider cannot route live signals yet, not a cosmetic "pending" label.</p>
      `,
    });
  }

  function renderSourcesTable(el, sources, ctx) {
    if (!sources.length) {
      StateMatrix.render(el, {
        state: "empty",
        emptyMessage: "This provider has no source registered yet (GET /provider-catalog/providers/{id}/sources).",
      });
      return;
    }
    const rows = sources.map((s) => [
      `<a href="#" class="tr19-source-link" data-source-id="${escapeAttr(s.id)}"><span class="mono">${escapeHtml(s.id)}</span></a>`,
      escapeHtml(s.platform),
      s.role ? escapeHtml(s.role) : pill("unset", "muted"),
      enumPill(s.execution_eligibility, EXEC_ELIGIBILITY_TONE),
      enumPill(s.health_state, HEALTH_TONE),
      boolPill(s.enabled),
      s.connection_id ? `<span class="mono">${escapeHtml(s.connection_id)}</span>` : pill("no connection linked", "muted"),
      s.last_event_at ? escapeHtml(s.last_event_at) : pill("none yet", "muted"),
    ]);
    StateMatrix.render(el, {
      state: "ready",
      html: `<p class="section-note">Real rows from GET /provider-catalog/providers/{id}/sources. health_state starts "unqualified" and is never shown as "healthy" until this build's own backend actually marks it so (app/provider_catalog.py's SourceHealth). Click a source_id to see its own detail and linked connection.</p>${table(
        ["Source ID", "Platform", "Role", "Execution eligibility", "Health state", "Enabled", "Connection", "Last event"],
        rows,
        "No sources."
      )}`,
    });
    el.querySelectorAll(".tr19-source-link").forEach((a) => {
      a.addEventListener("click", (e) => {
        e.preventDefault();
        selectedSourceId = a.dataset.sourceId;
        loadSourceDetail(ctx);
      });
    });
  }

  async function loadSourceDetail(ctx) {
    const el = ctx.container.querySelector("#tr19-p04 .tr-panel-body");
    if (!selectedSourceId) {
      StateMatrix.render(el, { state: "empty", emptyMessage: "Select a source above to see its own detail and linked connection." });
      return;
    }
    StateMatrix.render(el, { state: "loading" });
    const sourceRes = await ctx.fetchJSON(`/provider-catalog/sources/${encodeURIComponent(selectedSourceId)}`);
    if (!sourceRes.ok) {
      StateMatrix.render(el, { state: "error", message: `Could not load /provider-catalog/sources/${selectedSourceId}.` });
      return;
    }
    const source = sourceRes.data;
    let connectionHtml = `<p class="section-note">${pill("no connection linked", "muted")} -- this source has no connection_id set.</p>`;
    if (source.connection_id) {
      const connRes = await ctx.fetchJSON(`/provider-catalog/connections/${encodeURIComponent(source.connection_id)}`);
      if (connRes.ok) {
        const c = connRes.data;
        connectionHtml = `
          <h3 class="section-note">Linked connection <span class="mono">${escapeHtml(c.id)}</span></h3>
          <ul>
            <li>Connection type: <span class="mono">${escapeHtml(c.connection_type)}</span></li>
            <li>Connection state: ${escapeHtml(c.connection_state || "unset")}</li>
            <li>Authorization state: ${escapeHtml(c.authorization_state || "unset")}</li>
            <li>Credential reference (env var name only, never a raw secret): ${c.credential_reference ? `<span class="mono">${escapeHtml(c.credential_reference)}</span>` : pill("unset", "muted")}</li>
            <li>Health score: ${c.health_score === null || c.health_score === undefined ? pill("not tracked yet", "muted") : fmtNum(c.health_score)}</li>
            <li>Last heartbeat: ${c.last_heartbeat_at ? escapeHtml(c.last_heartbeat_at) : pill("none yet", "muted")}</li>
            <li>Last successful event: ${c.last_successful_event_at ? escapeHtml(c.last_successful_event_at) : pill("none yet", "muted")}</li>
            <li>Last error: ${c.last_error_at ? `${escapeHtml(c.last_error_at)}${c.last_error_detail ? ` -- ${escapeHtml(c.last_error_detail)}` : ""}` : pill("none", "muted")}</li>
          </ul>
        `;
      } else {
        connectionHtml = `<p class="section-note">source.connection_id is <span class="mono">${escapeHtml(source.connection_id)}</span> but GET /provider-catalog/connections/${escapeHtml(source.connection_id)} could not be loaded.</p>`;
      }
    }
    StateMatrix.render(el, {
      state: "ready",
      html: `
        <h3 class="section-note">Source <span class="mono">${escapeHtml(source.id)}</span></h3>
        <ul>
          <li>Provider: <span class="mono">${escapeHtml(source.provider_id)}</span></li>
          <li>Platform: ${escapeHtml(source.platform)}</li>
          <li>Role: ${source.role ? escapeHtml(source.role) : pill("unset", "muted")}</li>
          <li>Capture method: ${source.capture_method ? escapeHtml(source.capture_method) : pill("unset", "muted")}</li>
          <li>Parser profile: ${source.parser_profile ? escapeHtml(source.parser_profile) : pill("unset", "muted")}</li>
          <li>Execution eligibility: ${enumPill(source.execution_eligibility, EXEC_ELIGIBILITY_TONE)}</li>
          <li>Health state: ${enumPill(source.health_state, HEALTH_TONE)}</li>
          <li>Enabled: ${boolPill(source.enabled)}</li>
          <li>Priority: ${fmtNum(source.priority)}</li>
          <li>Last event / success / error: ${source.last_event_at ? escapeHtml(source.last_event_at) : pill("none", "muted")} / ${source.last_success_at ? escapeHtml(source.last_success_at) : pill("none", "muted")} / ${source.last_error_at ? escapeHtml(source.last_error_at) : pill("none", "muted")}</li>
        </ul>
        ${connectionHtml}
      `,
    });
  }

  async function loadProviderDetail(ctx) {
    const detailEl = ctx.container.querySelector("#tr19-p02 .tr-panel-body");
    const sourcesEl = ctx.container.querySelector("#tr19-p03 .tr-panel-body");
    const sourceDetailEl = ctx.container.querySelector("#tr19-p04 .tr-panel-body");
    if (!selectedProviderId) {
      StateMatrix.render(detailEl, { state: "empty", emptyMessage: "Select a provider above to see its detail." });
      StateMatrix.render(sourcesEl, { state: "empty", emptyMessage: "Select a provider above to see its sources." });
      StateMatrix.render(sourceDetailEl, { state: "empty", emptyMessage: "Select a source to see its own detail and linked connection." });
      return;
    }
    StateMatrix.render(detailEl, { state: "loading" });
    StateMatrix.render(sourcesEl, { state: "loading" });
    const [providerRes, sourcesRes] = await Promise.all([
      ctx.fetchJSON(`/provider-catalog/providers/${encodeURIComponent(selectedProviderId)}`),
      ctx.fetchJSON(`/provider-catalog/providers/${encodeURIComponent(selectedProviderId)}/sources`),
    ]);
    if (providerRes.status === 401 || providerRes.status === 403) {
      StateMatrix.render(detailEl, { state: "denied", deniedCode: providerRes.status });
      return;
    }
    if (!providerRes.ok) {
      StateMatrix.render(detailEl, { state: "error", message: `Could not load /provider-catalog/providers/${selectedProviderId}.` });
      return;
    }
    renderProviderDetail(detailEl, providerRes.data);
    const sources = (sourcesRes.ok && sourcesRes.data && sourcesRes.data.sources) || [];
    if (selectedSourceId && !sources.some((s) => s.id === selectedSourceId)) selectedSourceId = null;
    renderSourcesTable(sourcesEl, sources, ctx);
    await loadSourceDetail(ctx);
  }

  async function load(ctx) {
    const els = {
      providers: ctx.container.querySelector("#tr19-p01 .tr-panel-body"),
      detail: ctx.container.querySelector("#tr19-p02 .tr-panel-body"),
      sources: ctx.container.querySelector("#tr19-p03 .tr-panel-body"),
      sourceDetail: ctx.container.querySelector("#tr19-p04 .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const res = await ctx.fetchJSON("/provider-catalog/providers");
    if (res.status === 401 || res.status === 403) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "denied", deniedCode: res.status });
      return;
    }
    if (!res.ok) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "error", message: "Could not load /provider-catalog/providers." });
      return;
    }
    const providers = (res.data && res.data.providers) || [];
    renderProvidersTable(els.providers, providers, ctx);
    if (selectedProviderId && !providers.some((p) => p.id === selectedProviderId)) {
      selectedProviderId = null;
      selectedSourceId = null;
    }
    await loadProviderDetail(ctx);

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr19 = {
    title: "Provider catalog",
    breadcrumb: "Trade / Provider catalog",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      selectedProviderId = null;
      selectedSourceId = null;
      ctx.container.innerHTML = shell();
      await load(ctx);
      ctx.registerPoll("tr19", 30000, () => load(ctx));
    },
  };
  Router.register("/trade/provider-catalog", "tr19");
})();
