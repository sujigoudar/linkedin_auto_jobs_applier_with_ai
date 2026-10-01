/* TR-18: Mobile devices (`#/trade/mobile-devices`).
 *
 * Track 20 shipped a real backend for this screen -- GET /mobile-devices,
 * GET /mobile-devices/{device_id}, PATCH /mobile-devices/{device_id},
 * GET /mobile-devices/{device_id}/apps, GET /mobile-devices/{device_id}/
 * apps/{package_name}, POST /mobile-devices/{device_id}/apps/{package_name},
 * POST /mobile-devices/{device_id}/apps/{package_name}/test (app/main.py,
 * backed by app/notification_bridge.py's `NotificationBridgeDevice`/
 * `MobileAppConfig`) -- but a live browser test confirmed there was no
 * dashboard screen anywhere that registered a route for it (every guessed
 * hash route rendered this app's own "Not found" page). This view is that
 * screen, built ONLY against what that backend actually exposes.
 *
 * Real backing data, all read-only GETs except the two owner-gated writes
 * named below:
 *   - GET /mobile-devices: every registered device, each field (device_
 *     name/platform/model/os_version/agent_version/network_status/
 *     battery_level/is_charging/notification_permission_granted/
 *     accessibility_permission_granted/screen_control_capability/
 *     ai_agent_capability) honestly `null` until that exact device has
 *     reported it via its own heartbeat -- see
 *     tests/test_t20_mobile_devices.py's own
 *     `test_fresh_device_has_all_track20_metadata_fields_null`. This view
 *     never fills a null field with a guessed value or a fabricated "ok".
 *   - GET /mobile-devices/{device_id}/apps: this device's own per-app
 *     configuration rows (display_name/capture_notifications/active_
 *     retrieval_allowed/retrieval_mode/screenshot_retention/provider_
 *     mapping), fetched on drill-down only (one call per device the
 *     operator actually opens, never all devices' apps up front).
 *
 * Actions actually backed by this API, and nothing beyond them:
 *   - PATCH /mobile-devices/{device_id}: rename the device (device_name)
 *     and/or edit its allowed_apps/blocked_apps lists. A 422 from the
 *     global broker/banking deny-list (validate_device_app_lists) is
 *     surfaced verbatim, never silently swallowed.
 *   - POST /mobile-devices/{device_id}/apps/{package_name}/test: the
 *     "Test App" action. This build has no physical Android device/ADB
 *     connection (app/phone_escalation.py's AdbPhoneControlAdapter raises
 *     NotImplementedError for every method) -- the route itself always
 *     returns a structured status="unavailable" response, per
 *     tests/test_t20_mobile_devices.py's own
 *     `test_test_app_route_returns_honest_unavailable_never_a_fabricated_success`.
 *     This screen renders that response verbatim (never a fabricated
 *     "here's what it saw").
 *
 * Explicitly NOT built, because the backend genuinely doesn't support it:
 *   - No unpair/revoke/delete-device action anywhere in app/main.py --
 *     this screen offers none. Re-pairing is a device-side flow (a new
 *     POST /notification-bridge/devices call with the same device_id
 *     re-registers it), not something this dashboard can trigger.
 *   - No create-mobile-app-config FORM beyond the few fields PATCH/POST
 *     already cover here is added speculatively -- navigation_recipe/
 *     content_extraction_schema are shown read-only (forward-declared
 *     config with no real interpreter behind them yet, per
 *     MobileAppConfigRequest's own docstring), never editable from a form
 *     this build can't actually act on.
 */
(function () {
  "use strict";

  function capSlot(id) {
    return `<span class="cap-state-slot" id="${id}"></span>`;
  }
  function mountCapStates(root, specs) {
    for (const [id, opts] of specs) {
      const el = root.querySelector(`#${id}`);
      if (el) Components.renderCapabilityState(el, opts);
    }
  }

  const HEALTH_TONE = {
    healthy_qualified: "ok",
    no_notifications_observed: "warn",
    content_completeness_degraded: "warn",
    no_heartbeat_recently: "bad",
    unauthorized_app_package: "bad",
    never_paired: "muted",
  };

  function healthPill(state) {
    return pill(state || "unknown", HEALTH_TONE[state] || "muted");
  }

  function fmtField(v, suffix) {
    if (v === null || v === undefined) return pill("not reported yet", "muted");
    return suffix ? `${escapeHtml(String(v))}${suffix}` : escapeHtml(String(v));
  }

  function shell() {
    return `
      <section class="tr-panel" id="tr18-p01"><h2>Paired devices</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr18-p02"><h2>Device detail</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr18-p03"><h2>App configuration</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr18-actions"><h2>Actions</h2><div class="tr-panel-body"></div></section>
    `;
  }

  let selectedDeviceId = null;

  function renderDevicesTable(el, devices, ctx) {
    if (!devices.length) {
      StateMatrix.render(el, {
        state: "empty",
        emptyMessage: "No device is paired yet. Pairing is a device-side flow -- the Android companion app (mobile/notification-bridge/) calls POST /notification-bridge/devices with its own device_id to register, then this screen will list it.",
      });
      return;
    }
    const rows = devices.map((d) => [
      `<a href="#" class="tr18-device-link" data-device-id="${escapeAttr(d.device_id)}"><span class="mono">${escapeHtml(d.device_id)}</span></a>`,
      d.device_name ? escapeHtml(d.device_name) : pill("not named", "muted"),
      healthPill(d.health_state),
      fmtField(d.platform),
      fmtField(d.model),
      fmtField(d.os_version),
      d.battery_level === null || d.battery_level === undefined ? pill("not reported yet", "muted") : `${fmtNum(d.battery_level)}%${d.is_charging ? " (charging)" : d.is_charging === false ? "" : ""}`,
      d.last_heartbeat_at ? escapeHtml(d.last_heartbeat_at) : pill("none yet", "muted"),
      `${d.app_packages.length} authorized package(s)`,
    ]);
    StateMatrix.render(el, {
      state: "ready",
      html: `<p class="section-note">Every field below is this exact device's own real report via POST /ingest/notification-bridge/{device_id} -- "not reported yet" means the device has never sent that field, never a fabricated default. Click a device_id to see its full detail and per-app configuration.</p>${table(
        ["Device ID", "Name", "Health", "Platform", "Model", "OS version", "Battery", "Last heartbeat", "Authorized apps"],
        rows,
        "No devices."
      )}`,
    });
    el.querySelectorAll(".tr18-device-link").forEach((a) => {
      a.addEventListener("click", (e) => {
        e.preventDefault();
        selectedDeviceId = a.dataset.deviceId;
        loadDeviceDetail(ctx);
      });
    });
  }

  function renderDeviceEditForm(container, device, ctx) {
    const formId = `tr18-edit-${escapeAttr(device.device_id)}`;
    container.insertAdjacentHTML(
      "beforeend",
      `
      <div class="tr-panel" style="margin-top:12px;">
        <h3 style="margin-top:0;">Edit device (PATCH /mobile-devices/${escapeHtml(device.device_id)})</h3>
        <form id="${formId}">
          <label>Device name <input type="text" name="device_name" value="${escapeAttr(device.device_name || "")}" placeholder="(unset)"></label>
          <label>Allowed apps (comma-separated package names) <input type="text" name="allowed_apps" value="${escapeAttr((device.allowed_apps || []).join(", "))}"></label>
          <label>Blocked apps (comma-separated package names) <input type="text" name="blocked_apps" value="${escapeAttr((device.blocked_apps || []).join(", "))}"></label>
          <div class="actions"><button type="submit">Save</button></div>
        </form>
        <div class="form-error" id="${formId}-error"></div>
      </div>
      `
    );
    const form = container.querySelector(`#${formId}`);
    const errorEl = container.querySelector(`#${formId}-error`);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      errorEl.textContent = "";
      const fd = new FormData(form);
      const splitList = (s) => (s || "").split(",").map((x) => x.trim()).filter(Boolean);
      const payload = {
        device_name: (fd.get("device_name") || "").trim() || null,
        allowed_apps: splitList(fd.get("allowed_apps")),
        blocked_apps: splitList(fd.get("blocked_apps")),
      };
      try {
        await patchJSON(`/mobile-devices/${encodeURIComponent(device.device_id)}`, payload);
      } catch (err) {
        errorEl.textContent = err.message;
        return;
      }
      await loadDeviceDetail(ctx);
    });
  }

  async function renderAppConfig(el, device, ctx) {
    StateMatrix.render(el, { state: "loading" });
    const appsRes = await ctx.fetchJSON(`/mobile-devices/${encodeURIComponent(device.device_id)}/apps`);
    if (!appsRes.ok) {
      StateMatrix.render(el, { state: "error", message: `Could not load /mobile-devices/${device.device_id}/apps.` });
      return;
    }
    const apps = (appsRes.data && appsRes.data.apps) || [];
    if (!device.app_packages.length) {
      StateMatrix.render(el, { state: "empty", emptyMessage: "This device has no authorized app_packages yet." });
      return;
    }
    const configuredByPackage = new Map(apps.map((a) => [a.package_name, a]));
    const rows = device.app_packages.map((pkg) => {
      const cfg = configuredByPackage.get(pkg);
      const testSlot = `tr18-test-${escapeAttr(device.device_id)}-${escapeAttr(pkg)}`;
      return {
        pkg,
        cfg,
        cells: [
          `<span class="mono">${escapeHtml(pkg)}</span>`,
          cfg ? (cfg.display_name ? escapeHtml(cfg.display_name) : pill("unnamed", "muted")) : pill("not configured", "muted"),
          cfg ? boolPill(cfg.capture_notifications) : pill("not configured", "muted"),
          cfg ? boolPill(cfg.active_retrieval_allowed) : pill("not configured", "muted"),
          cfg ? escapeHtml(cfg.retrieval_mode) : "—",
          cfg ? escapeHtml(cfg.screenshot_retention) : "—",
          cfg && cfg.provider_mapping && Object.keys(cfg.provider_mapping).length
            ? `<span class="mono">${escapeHtml(cfg.provider_mapping.provider_name || JSON.stringify(cfg.provider_mapping))}</span>`
            : pill("no mapping (falls back to bare package name)", "muted"),
          cfg ? capSlot(testSlot) : pill("create a config first", "muted"),
        ],
        testSlot,
      };
    });
    el.innerHTML = `
      <p class="section-note">Per-app configuration from GET /mobile-devices/${escapeHtml(device.device_id)}/apps, composed with this device's own provider_mapping (app/main.py's _mobile_app_config_response) -- "not configured" means POST /mobile-devices/{device_id}/apps/{package_name} has never been called for that package, never a fabricated default row.</p>
      ${table(
        ["Package", "Display name", "Capture notifications", "Active retrieval allowed", "Retrieval mode", "Screenshot retention", "Provider mapping", "Test App"],
        rows.map((r) => r.cells),
        "No authorized packages."
      )}
    `;
    for (const r of rows) {
      if (!r.cfg) continue;
      mountCapStates(el, [
        [
          r.testSlot,
          {
            status: "unsupported",
            reason: "This build has no physical Android device/ADB connection -- POST /mobile-devices/{device_id}/apps/{package_name}/test always returns a structured status=\"unavailable\" response here, never a fabricated success.",
          },
        ],
      ]);
    }
    const testButtonsHtml = rows
      .filter((r) => r.cfg)
      .map((r) => `<button type="button" class="tr18-test-btn" data-pkg="${escapeAttr(r.pkg)}">Run Test App for ${escapeHtml(r.pkg)}</button>`)
      .join(" ");
    if (testButtonsHtml) {
      el.insertAdjacentHTML(
        "beforeend",
        `<div class="tr-controls-row" style="margin-top:12px;">${testButtonsHtml}</div><div id="tr18-test-result" role="status" aria-live="polite"></div>`
      );
      el.querySelectorAll(".tr18-test-btn").forEach((btn) => {
        btn.addEventListener("click", async () => {
          btn.disabled = true;
          const resultEl = el.querySelector("#tr18-test-result");
          resultEl.innerHTML = `<div class="empty">Calling POST /mobile-devices/${escapeHtml(device.device_id)}/apps/${escapeHtml(btn.dataset.pkg)}/test…</div>`;
          let body;
          try {
            body = await postJSON(
              `/mobile-devices/${encodeURIComponent(device.device_id)}/apps/${encodeURIComponent(btn.dataset.pkg)}/test`,
              {}
            );
          } catch (err) {
            btn.disabled = false;
            resultEl.innerHTML = `<div class="form-error">${escapeHtml(err.message)}</div>`;
            return;
          }
          btn.disabled = false;
          resultEl.innerHTML = `
            <p class="section-note"><strong>status:</strong> ${escapeHtml(body.status)} -- <strong>reason:</strong> ${escapeHtml(body.reason || "")}</p>
            <p class="section-note">${escapeHtml(body.detail || "")}</p>
            <p class="section-note">what_it_saw: ${body.what_it_saw === null ? pill("null (never fabricated)", "muted") : escapeHtml(JSON.stringify(body.what_it_saw))} -- extracted_content: ${body.extracted_content === null ? pill("null (never fabricated)", "muted") : escapeHtml(JSON.stringify(body.extracted_content))}</p>
          `;
        });
      });
    }
  }

  async function loadDeviceDetail(ctx) {
    const detailEl = ctx.container.querySelector("#tr18-p02 .tr-panel-body");
    const appsEl = ctx.container.querySelector("#tr18-p03 .tr-panel-body");
    if (!selectedDeviceId) {
      StateMatrix.render(detailEl, { state: "empty", emptyMessage: "Select a device above to see its full detail." });
      StateMatrix.render(appsEl, { state: "empty", emptyMessage: "Select a device above to see its per-app configuration." });
      return;
    }
    StateMatrix.render(detailEl, { state: "loading" });
    StateMatrix.render(appsEl, { state: "loading" });
    const res = await ctx.fetchJSON(`/mobile-devices/${encodeURIComponent(selectedDeviceId)}`);
    if (res.status === 401 || res.status === 403) {
      StateMatrix.render(detailEl, { state: "denied", deniedCode: res.status });
      return;
    }
    if (!res.ok) {
      StateMatrix.render(detailEl, { state: "error", message: `Could not load /mobile-devices/${selectedDeviceId}.` });
      return;
    }
    const device = res.data;
    StateMatrix.render(detailEl, {
      state: "ready",
      html: `
        <ul>
          <li>Device ID: <span class="mono">${escapeHtml(device.device_id)}</span></li>
          <li>Name: ${device.device_name ? escapeHtml(device.device_name) : pill("not named", "muted")}</li>
          <li>Health: ${healthPill(device.health_state)}${device.health_detail ? ` -- ${escapeHtml(device.health_detail)}` : ""}</li>
          <li>Platform / model / OS: ${fmtField(device.platform)} / ${fmtField(device.model)} / ${fmtField(device.os_version)}</li>
          <li>Agent version: ${fmtField(device.agent_version)}</li>
          <li>Network status: ${fmtField(device.network_status)}</li>
          <li>Battery: ${device.battery_level === null || device.battery_level === undefined ? pill("not reported yet", "muted") : `${fmtNum(device.battery_level)}%`} -- charging: ${device.is_charging === null || device.is_charging === undefined ? pill("not reported yet", "muted") : boolPill(device.is_charging)}</li>
          <li>Notification permission granted: ${device.notification_permission_granted === null || device.notification_permission_granted === undefined ? pill("not reported yet", "muted") : boolPill(device.notification_permission_granted)}</li>
          <li>Accessibility permission granted: ${device.accessibility_permission_granted === null || device.accessibility_permission_granted === undefined ? pill("not reported yet", "muted") : boolPill(device.accessibility_permission_granted)}</li>
          <li>Screen-control capability (device's own self-report, informational only): ${device.screen_control_capability === null || device.screen_control_capability === undefined ? pill("not reported yet", "muted") : boolPill(device.screen_control_capability)}</li>
          <li>AI-agent capability (device's own self-report, informational only): ${device.ai_agent_capability === null || device.ai_agent_capability === undefined ? pill("not reported yet", "muted") : boolPill(device.ai_agent_capability)}</li>
          <li>Last heartbeat: ${device.last_heartbeat_at ? escapeHtml(device.last_heartbeat_at) : pill("none yet", "muted")}</li>
          <li>Authorized app_packages: ${device.app_packages.length ? device.app_packages.map((p) => `<span class="mono">${escapeHtml(p)}</span>`).join(", ") : pill("none", "muted")}</li>
          <li>Allowed apps (operator-configured): ${device.allowed_apps.length ? device.allowed_apps.map((p) => `<span class="mono">${escapeHtml(p)}</span>`).join(", ") : pill("none set", "muted")}</li>
          <li>Blocked apps (operator-configured, additional to the global broker/banking deny-list): ${device.blocked_apps.length ? device.blocked_apps.map((p) => `<span class="mono">${escapeHtml(p)}</span>`).join(", ") : pill("none set", "muted")}</li>
        </ul>
      `,
    });
    renderDeviceEditForm(detailEl, device, ctx);
    await renderAppConfig(appsEl, device, ctx);
  }

  async function load(ctx) {
    const els = {
      devices: ctx.container.querySelector("#tr18-p01 .tr-panel-body"),
      detail: ctx.container.querySelector("#tr18-p02 .tr-panel-body"),
      apps: ctx.container.querySelector("#tr18-p03 .tr-panel-body"),
      actions: ctx.container.querySelector("#tr18-actions .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const res = await ctx.fetchJSON("/mobile-devices");
    if (res.status === 401 || res.status === 403) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "denied", deniedCode: res.status });
      return;
    }
    if (!res.ok) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "error", message: "Could not load /mobile-devices." });
      return;
    }
    const devices = (res.data && res.data.devices) || [];
    renderDevicesTable(els.devices, devices, ctx);
    if (selectedDeviceId && !devices.some((d) => d.device_id === selectedDeviceId)) selectedDeviceId = null;
    await loadDeviceDetail(ctx);

    Components.renderCapabilityState(els.actions, {
      status: "unsupported",
      reason: "No unpair/revoke/delete-device route exists anywhere in app/main.py -- this screen offers none. Pairing and re-pairing are device-side actions (the Android companion app itself calls POST /notification-bridge/devices), not something this dashboard can trigger.",
    });

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr18 = {
    title: "Mobile devices",
    breadcrumb: "Trade / Mobile devices",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      selectedDeviceId = null;
      ctx.container.innerHTML = shell();
      await load(ctx);
      ctx.registerPoll("tr18", 30000, () => load(ctx));
    },
  };
  Router.register("/trade/mobile-devices", "tr18");
})();
