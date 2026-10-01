/* TR-17: "+Add Signal Provider" onboarding wizard (`#/trade/providers/add`).
 *
 * This is the Track 21 capstone view: it drives the three new, thin
 * catalog-creation primitives Track 21 added to app/main.py -- POST
 * /providers, POST /connections, POST /sources (each a direct wrapper
 * over app/db.py's SignalStore.register_provider/register_connection/
 * register_source, no new business logic) -- plus the real, code-verified
 * catalog Track 19 already shipped (GET /connections/catalog) and the
 * field-discovery route Track 21 added alongside the three creation
 * routes (GET /connections/catalog/{connection_type}/setup-fields,
 * app/connection_catalog.py's get_connection_setup_fields).
 *
 * Two entry points, both ending in the same three-call sequence
 * (POST /providers -> POST /connections -> POST /sources):
 *
 * - Quick Add: a short, HONEST list of connection types this codebase
 *   has a real, `status: "implemented"` adapter for (per GET /connections/
 *   catalog), pre-filled with sensible defaults, asking for only the
 *   1-2 fields that type's own real setup-fields response marks
 *   `required: true`. There is no dedicated Whop adapter/module in this
 *   codebase (checked: no app/whop.py or similar exists) -- Whop reaches
 *   this system today only via the Android Notification Bridge
 *   (app/notification_bridge.py, connection_type=android_notification,
 *   see tests/test_track12_whop_notification_completeness.py), which
 *   requires an ALREADY-PAIRED device (a separate, multi-step Track 12/20
 *   flow this wizard does not replace) -- not a one-field "paste your
 *   webhook secret" quick add. Rather than fabricate a Whop-specific
 *   quick-add shortcut this build cannot actually back, Quick Add offers
 *   the two connection types that genuinely ARE a 1-2-field form backed
 *   by a real, already-working adapter: the generic Webhook (push,
 *   zero required connection fields) and a Telegram Bot (bot token env
 *   var + chat id). Whop is reachable through Advanced Add's
 *   android_notification type once a device is already paired.
 *
 * - Advanced Add: a single flat form covering 4 concerns -- pick a
 *   connection_type from the real catalog (implemented types selectable,
 *   not_implemented types shown, disabled, with their real `notes` as the
 *   "not yet supported" reason -- never hidden, never silently omitted),
 *   fill in exactly the fields that type's real setup-fields response asks
 *   for, name the provider, and review before submit. This is NOT a
 *   paginated/stepped wizard (no per-step focus management) -- all four
 *   concerns render at once with a live-updating review paragraph.
 *
 * Every step reads real capability data (GET /connections/catalog, GET
 * /connections/catalog/{type}/setup-fields) -- no fabricated provider
 * list, no invented field beyond what those two endpoints return.
 */
(function () {
  "use strict";

  const QUICK_ADD_PRESETS = [
    {
      key: "webhook",
      title: "Generic Webhook",
      blurb: "Push-based JSON ingestion (app/sources/webhook.py) -- the one fully working, provider-agnostic path. No per-connection secret is generated; auth is your deployment's existing WEBHOOK_SHARED_SECRET.",
      connection_type: "webhook",
    },
    {
      key: "telegram_bot",
      title: "Telegram Bot",
      blurb: "A BotFather bot already added to your channel (app/sources/telegram.py).",
      connection_type: "telegram_bot",
    },
  ];

  function slugify(s) {
    return String(s || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  }

  function shell() {
    return `
      <section class="tr-panel" id="tr17-p-intro"><h2>Add a signal provider</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr17-p-quick"><h2>Quick Add</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr17-p-advanced"><h2>Advanced Add</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr17-p-result"><h2>Result</h2><div class="tr-panel-body"></div></section>
    `;
  }

  function fieldInputHtml(f, idPrefix) {
    const id = `${idPrefix}-${f.name}`;
    const req = f.required ? "required" : "";
    if (f.target === null) {
      // Informational-only field -- this track's creation endpoints have
      // no column for it; never rendered as a submittable input that
      // would silently be dropped.
      return `<p class="section-note"><strong>${escapeHtml(f.label)}</strong>: ${escapeHtml(f.help || "")}</p>`;
    }
    const type = f.type === "number" ? "number" : "text";
    const placeholder = f.default !== undefined ? String(f.default) : "";
    return `
      <label>${escapeHtml(f.label)}${f.required ? " *" : " (optional)"}
        <input type="${type}" id="${id}" data-field="${escapeAttr(f.name)}" data-target="${escapeAttr(f.target || "")}" placeholder="${escapeAttr(placeholder)}" ${req}>
      </label>
      <p class="section-note" style="margin-top:-6px;">${escapeHtml(f.help || "")}</p>
    `;
  }

  function setTargetValue(payloads, target, rawValue, fieldType) {
    if (!target) return;
    const value = fieldType === "number" && rawValue !== "" ? Number(rawValue) : rawValue;
    const [bucket, ...rest] = target.split(".");
    if (bucket === "connection") {
      payloads.connection[rest.join(".")] = value;
    } else if (bucket === "source") {
      if (rest.length === 1) {
        payloads.source[rest[0]] = value;
      } else if (rest[0] === "freshness_policy") {
        payloads.source.freshness_policy = payloads.source.freshness_policy || {};
        payloads.source.freshness_policy[rest[1]] = value;
      }
    }
  }

  async function submitWizard(ctx, resultEl, { providerId, displayName, connectionType, connectionId, sourceId, fieldEls }, submitBtn) {
    if (submitBtn && submitBtn.disabled) return; // already submitting -- ignore a double-click/duplicate event
    if (submitBtn) submitBtn.disabled = true;
    resultEl.innerHTML = `<p class="section-note">Submitting...</p>`;
    const payloads = { connection: {}, source: {} };
    for (const el of fieldEls) {
      const target = el.dataset.target;
      if (!target) continue;
      if (el.value === "" && !el.required) continue;
      setTargetValue(payloads, target, el.value, el.type);
    }

    let provider = null;
    let connection = null;
    try {
      provider = await postJSON("/providers", {
        provider_id: providerId,
        display_name: displayName,
        status: "onboarding",
        execution_eligibility: "disabled",
      });

      connection = await postJSON("/connections", {
        connection_id: connectionId,
        connection_type: connectionType,
        display_name: displayName,
        connection_state: "unconfigured",
        authorization_state: "unauthorized",
        ...payloads.connection,
      });

      const source = await postJSON("/sources", {
        source_id: sourceId,
        provider_id: provider.id,
        platform: connectionType,
        connection_id: connection.id,
        enabled: true,
        execution_eligibility: "disabled",
        health_state: "unqualified",
        ...payloads.source,
      });

      resultEl.innerHTML = `
        <p class="section-note">Created provider <span class="mono">${escapeHtml(provider.id)}</span>, connection <span class="mono">${escapeHtml(connection.id)}</span>, source <span class="mono">${escapeHtml(source.id)}</span>.</p>
        <p class="section-note">This registered the catalog rows only -- <code>execution_eligibility="disabled"</code> on both the provider and source, and <code>certification_state="uncertified"</code> on the provider (no live routing is possible yet). Real transport/credentials still need the env var(s) named above set in your deployment config; live eligibility is a separate, later step.</p>
        <p class="section-note"><strong>Note:</strong> this screen writes to the newer provider/source/connection catalog (<span class="mono">GET /provider-catalog/providers</span>). <a href="#/trade/sources">Signal providers and collectors (TR-09)</a> reads an older, separate <span class="mono">ProviderConfig</span> model and will NOT show what you just created -- that is not a failure, the two are simply different data models today. See it on <a href="#/trade/provider-catalog">Provider catalog (TR-19)</a>, which reads this exact new model.</p>
      `;
    } catch (err) {
      // Disclose exactly how far the 3-call sequence got, since a retry
      // re-derives the same provider_id slug and will otherwise collide
      // with a row this same failed attempt already created.
      const partial = [];
      if (provider) partial.push(`provider <span class="mono">${escapeHtml(provider.id)}</span>`);
      if (connection) partial.push(`connection <span class="mono">${escapeHtml(connection.id)}</span>`);
      const partialNote = partial.length
        ? `<p class="section-note">Already created before this failure: ${partial.join(", ")} (left in place, not rolled back). A retry with the same name will reuse/collide with these rather than creating fresh rows -- check <a href="#/trade/provider-catalog">Provider catalog (TR-19)</a> before retrying.</p>`
        : "";
      resultEl.innerHTML = `<div class="form-error">${escapeHtml(err.message)}</div>${partialNote}`;
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  function renderQuickAdd(ctx, els, catalogByType) {
    const availablePresets = QUICK_ADD_PRESETS.filter((p) => catalogByType[p.connection_type] && catalogByType[p.connection_type].status === "implemented");
    if (!availablePresets.length) {
      StateMatrix.render(els.quick, {
        state: "empty",
        emptyMessage: "No Quick Add presets are backed by an implemented connection type in this build right now -- use Advanced Add below.",
      });
      return;
    }
    const cardsHtml = availablePresets
      .map(
        (p) => `
        <div class="tr-panel" style="margin:8px 0;">
          <h3 style="margin-top:0;">${escapeHtml(p.title)}</h3>
          <p class="section-note">${escapeHtml(p.blurb)}</p>
          <div id="tr17-quick-${escapeAttr(p.key)}-fields"></div>
          <label>Provider display name <input type="text" id="tr17-quick-${escapeAttr(p.key)}-name" placeholder="${escapeAttr(p.title)}"></label>
          <button type="button" data-quick-add="${escapeAttr(p.key)}">Add with ${escapeHtml(p.title)}</button>
          <div id="tr17-quick-${escapeAttr(p.key)}-result" role="status" aria-live="polite"></div>
        </div>
      `
      )
      .join("");
    StateMatrix.render(els.quick, {
      state: "ready",
      html: `<p class="section-note">A short, honest list of already-implemented connection types with a 1-2-field setup, from the real setup-fields discovery endpoint below -- not an invented "top providers" list.</p>${cardsHtml}`,
    });

    availablePresets.forEach((p) => {
      const fieldsEl = els.quick.querySelector(`#tr17-quick-${p.key}-fields`);
      const setup = catalogByType[p.connection_type].setup;
      const requiredFields = (setup.fields || []).filter((f) => f.required || f.target === null);
      fieldsEl.innerHTML = requiredFields.map((f) => fieldInputHtml(f, `tr17-quick-${p.key}`)).join("") || `<p class="section-note">No fields required.</p>`;

      const quickAddBtn = els.quick.querySelector(`[data-quick-add="${p.key}"]`);
      quickAddBtn.addEventListener("click", async () => {
        if (quickAddBtn.disabled) return;
        const nameEl = els.quick.querySelector(`#tr17-quick-${p.key}-name`);
        const displayName = nameEl.value.trim() || p.title;
        const slug = slugify(displayName) || p.key;
        const resultEl = els.quick.querySelector(`#tr17-quick-${p.key}-result`);
        const fieldEls = Array.from(fieldsEl.querySelectorAll("input[data-field]"));
        const missing = fieldEls.filter((el) => el.required && !el.value.trim());
        if (missing.length) {
          resultEl.innerHTML = `<div class="form-error">Fill in: ${missing.map((el) => el.dataset.field).join(", ")}</div>`;
          return;
        }
        await submitWizard(
          ctx,
          resultEl,
          {
            providerId: slug,
            displayName,
            connectionType: p.connection_type,
            connectionId: `${slug}_${p.connection_type}`,
            sourceId: `${slug}_${p.connection_type}_source`,
            fieldEls,
          },
          quickAddBtn
        );
      });
    });
  }

  function renderAdvanced(ctx, els, catalogTypes, catalogByType) {
    const implemented = catalogTypes.filter((t) => t.status === "implemented");
    const notImplemented = catalogTypes.filter((t) => t.status !== "implemented");
    const optionsHtml = [
      ...implemented.map((t) => `<option value="${escapeAttr(t.connection_type)}">${escapeHtml(t.display_name)} (${escapeHtml(t.category)})</option>`),
    ].join("");
    const disabledListHtml = notImplemented
      .map(
        (t) => `<li><span class="mono">${escapeHtml(t.connection_type)}</span> -- ${escapeHtml(t.display_name)}: <span class="pill bad">not yet supported</span><br><span class="section-note">${escapeHtml(t.notes)}</span></li>`
      )
      .join("");

    StateMatrix.render(els.advanced, {
      state: "ready",
      html: `
        <ol class="section-note" style="padding-left:18px;">
          <li>Pick a connection type (implemented types only -- see the full catalog and its not-yet-supported entries below).</li>
          <li>Fill in the fields that type's own adapter needs (GET /connections/catalog/{type}/setup-fields, real evidence, never guessed).</li>
          <li>Name the provider.</li>
          <li>Review and submit -- creates the provider, connection, and source in sequence.</li>
        </ol>
        <label>Connection type
          <select id="tr17-adv-type">${optionsHtml}</select>
        </label>
        <div id="tr17-adv-fields"></div>
        <label>Provider display name <input type="text" id="tr17-adv-name" placeholder="New provider"></label>
        <label>Provider ID (slug, auto-derived)<input type="text" id="tr17-adv-provider-id" readonly></label>
        <div id="tr17-adv-review"></div>
        <div class="tr-controls-row">
          <button type="button" id="tr17-adv-submit">Create provider / connection / source</button>
        </div>
        <div id="tr17-adv-result" role="status" aria-live="polite"></div>
        <h3 class="section-note" style="margin-top:16px;">Not yet supported (shown honestly, never hidden)</h3>
        <ul class="section-note">${disabledListHtml || "<li>None -- every catalog entry is implemented.</li>"}</ul>
      `,
    });

    const typeSelect = els.advanced.querySelector("#tr17-adv-type");
    const fieldsEl = els.advanced.querySelector("#tr17-adv-fields");
    const nameEl = els.advanced.querySelector("#tr17-adv-name");
    const providerIdEl = els.advanced.querySelector("#tr17-adv-provider-id");
    const reviewEl = els.advanced.querySelector("#tr17-adv-review");
    const resultEl = els.advanced.querySelector("#tr17-adv-result");

    function renderFieldsForType(connectionType) {
      const entry = catalogByType[connectionType];
      if (!entry || !entry.setup || !entry.setup.fields) {
        fieldsEl.innerHTML = `<p class="section-note">No fields declared for this type yet.</p>`;
        return;
      }
      fieldsEl.innerHTML = entry.setup.fields.map((f) => fieldInputHtml(f, "tr17-adv")).join("");
    }

    function updateReview() {
      const type = typeSelect.value;
      const displayName = nameEl.value.trim();
      const slug = slugify(displayName) || slugify(type) || "new_provider";
      providerIdEl.value = slug;
      reviewEl.innerHTML = `<p class="section-note">Will create: provider <span class="mono">${escapeHtml(slug)}</span>, connection <span class="mono">${escapeHtml(slug + "_" + type)}</span> (type <span class="mono">${escapeHtml(type)}</span>), source <span class="mono">${escapeHtml(slug + "_" + type + "_source")}</span>.</p>`;
    }

    typeSelect.addEventListener("change", () => {
      renderFieldsForType(typeSelect.value);
      updateReview();
    });
    nameEl.addEventListener("input", updateReview);

    if (implemented.length) {
      renderFieldsForType(typeSelect.value);
      updateReview();
    } else {
      fieldsEl.innerHTML = `<p class="section-note">No implemented connection types are available in this build.</p>`;
    }

    const advSubmitBtn = els.advanced.querySelector("#tr17-adv-submit");
    advSubmitBtn.addEventListener("click", async () => {
      if (advSubmitBtn.disabled) return;
      if (!implemented.length) {
        resultEl.innerHTML = `<div class="form-error">No implemented connection type to submit.</div>`;
        return;
      }
      const type = typeSelect.value;
      const displayName = nameEl.value.trim();
      if (!displayName) {
        resultEl.innerHTML = `<div class="form-error">Provider display name is required.</div>`;
        return;
      }
      const slug = providerIdEl.value;
      const fieldEls = Array.from(fieldsEl.querySelectorAll("input[data-field]"));
      const missing = fieldEls.filter((el) => el.required && !el.value.trim());
      if (missing.length) {
        resultEl.innerHTML = `<div class="form-error">Fill in: ${missing.map((el) => el.dataset.field).join(", ")}</div>`;
        return;
      }
      await submitWizard(
        ctx,
        resultEl,
        {
          providerId: slug,
          displayName,
          connectionType: type,
          connectionId: `${slug}_${type}`,
          sourceId: `${slug}_${type}_source`,
          fieldEls,
        },
        advSubmitBtn
      );
    });
  }

  async function load(ctx) {
    const els = {
      intro: ctx.container.querySelector("#tr17-p-intro .tr-panel-body"),
      quick: ctx.container.querySelector("#tr17-p-quick .tr-panel-body"),
      advanced: ctx.container.querySelector("#tr17-p-advanced .tr-panel-body"),
      result: ctx.container.querySelector("#tr17-p-result .tr-panel-body"),
    };
    for (const el of [els.quick, els.advanced]) StateMatrix.render(el, { state: "loading" });

    StateMatrix.render(els.intro, {
      state: "ready",
      html: `<p class="section-note">Ties together Track 14's Provider/Source/Connection data model, Track 19's real connection-type catalog, and this track's own field-discovery endpoint into one guided flow. Every provider is created inactive (<code>execution_eligibility="disabled"</code>, <code>certification_state="uncertified"</code>) -- turning it live is always a separate, later step.</p>`,
    });
    StateMatrix.render(els.result, { state: "ready", html: `<p class="section-note">Submission results appear here and inline under whichever form you used.</p>` });

    const catalogRes = await ctx.fetchJSON("/connections/catalog");
    if (catalogRes.status === 401 || catalogRes.status === 403) {
      for (const el of [els.quick, els.advanced]) StateMatrix.render(el, { state: "denied", deniedCode: catalogRes.status });
      return;
    }
    if (!catalogRes.ok) {
      for (const el of [els.quick, els.advanced]) StateMatrix.render(el, { state: "error", message: "Could not load the connection-type catalog." });
      return;
    }
    const catalogTypes = (catalogRes.data && catalogRes.data.types) || [];
    if (!catalogTypes.length) {
      for (const el of [els.quick, els.advanced]) StateMatrix.render(el, { state: "empty", emptyMessage: "No connection types in this build's catalog." });
      return;
    }

    // Fetch setup-fields for every IMPLEMENTED type up front (small, fixed
    // list -- see app/connection_catalog.py) so both Quick Add and
    // Advanced Add can render synchronously off one shared map.
    const catalogByType = {};
    for (const t of catalogTypes) catalogByType[t.connection_type] = { ...t };
    const implementedTypes = catalogTypes.filter((t) => t.status === "implemented");
    await Promise.all(
      implementedTypes.map(async (t) => {
        const res = await ctx.fetchJSON(`/connections/catalog/${encodeURIComponent(t.connection_type)}/setup-fields`);
        if (res.ok) catalogByType[t.connection_type].setup = res.data;
      })
    );

    renderQuickAdd(ctx, els, catalogByType);
    renderAdvanced(ctx, els, catalogTypes, catalogByType);

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr17 = {
    title: "Add signal provider",
    breadcrumb: "Providers / Add",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = shell();
      await load(ctx);
    },
  };
  Router.register("/trade/providers/add", "tr17");
})();
