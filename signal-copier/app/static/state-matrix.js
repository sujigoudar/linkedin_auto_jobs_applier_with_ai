/* Shared state-matrix rendering helper for the TR-0x (and later screen)
 * hash-routed views (see app/static/router.js).
 *
 * Every screen spec (TR-01..TR-16) defines the same 11-state UI matrix:
 * loading / ready / empty / denied / stale / partial / timeout / error /
 * unsupported / conflict / session_expired / maintenance. Rather than each
 * view file reinventing this, StateMatrix.render(container, view) is the
 * ONE place that renders the non-"ready" states consistently. A view's own
 * render function is responsible only for the "ready" markup (its actual
 * panels) -- everything else funnels through here.
 *
 * This file intentionally renders every state defined by the spec, even
 * though this batch (TR-01..TR-04) only actually reaches a subset of them
 * from a real API call:
 *   - loading, ready, empty, denied (401/403/404), error: reached directly
 *     from this batch's own fetch calls.
 *   - unsupported: used for panels/actions with no real backing capability
 *     in this codebase (see each view file's own comments for exactly
 *     which ones and why -- e.g. TR-03's partial-reduce/stop-change/
 *     reconciliation-request actions have no API to call).
 *   - session_expired: shares the existing dashboard.html session-expiry
 *     path (checkSession/showLogin) -- routed views call the same
 *     `onSessionExpired` hook.
 *   - stale, partial, timeout, conflict, maintenance: not naturally
 *     reachable from this codebase's current plain-JSON, no-SSE, no-
 *     draft-versioning APIs (no snapshot staleness signal, no partial-
 *     panel-status envelope, no command-status-by-operation-id read, no
 *     optimistic-concurrency version field, no maintenance-mode flag are
 *     exposed anywhere in app/main.py today). The renderers exist so a
 *     later batch (or a later backend change that adds one of those
 *     signals) can call StateMatrix.render(el, {state: "stale", ...})
 *     immediately without inventing its own markup -- but nothing in this
 *     batch fabricates a fake stale/partial/timeout/conflict/maintenance
 *     condition just to exercise them.
 */
(function (global) {
  "use strict";

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function skeletonRows(n) {
    let out = "";
    for (let i = 0; i < n; i++) out += '<div class="sm-skeleton-row"></div>';
    return out;
  }

  function wrap(kind, inner) {
    return `<div class="sm-state sm-state-${escapeHtml(kind)}">${inner}</div>`;
  }

  /**
   * Render one of the 11 states into `container` (a DOM element).
   * `view` is: {
   *   state: one of "loading"|"ready"|"empty"|"denied"|"stale"|"partial"|
   *          "timeout"|"error"|"unsupported"|"conflict"|"session_expired"|
   *          "maintenance",
   *   // ready:
   *   html: string of the panel's real content (state === "ready" only),
   *   // empty:
   *   emptyMessage, nextRoute, nextLabel,
   *   // denied:
   *   deniedCode (401|403|404),
   *   // stale:
   *   asOf, staleHtml (last-good snapshot content to still show),
   *   // partial:
   *   partialHtml, missingFields (string[]),
   *   // timeout:
   *   onRetry (function) -- GET timeout only, per spec ("GET can offer
   *     bounded retry; command timeout shows UNKNOWN plus operation ID"),
   *   operationId,
   *   // error:
   *   message, correlationId, onRetry,
   *   // unsupported:
   *   reason,
   *   // conflict:
   *   oldValue, newValue, field,
   *   // maintenance:
   *   statusMessage, contact,
   * }
   */
  function render(container, view) {
    if (!container) return;
    const state = view && view.state;
    switch (state) {
      case "loading": {
        container.setAttribute("aria-busy", "true");
        container.innerHTML = wrap(
          "loading",
          `<div class="sm-skeleton" role="status" aria-live="polite">${skeletonRows(3)}<span class="sr-only">Loading…</span></div>`
        );
        return;
      }
      case "ready": {
        container.removeAttribute("aria-busy");
        container.innerHTML = view.html || "";
        return;
      }
      case "empty": {
        container.removeAttribute("aria-busy");
        const next = view.nextRoute
          ? `<a class="sm-next-link" href="#${escapeHtml(view.nextRoute)}">${escapeHtml(view.nextLabel || "Continue")}</a>`
          : "";
        container.innerHTML = wrap(
          "empty",
          `<p class="sm-empty-message">${escapeHtml(view.emptyMessage || "Nothing to show in this scope.")}</p>${next}`
        );
        return;
      }
      case "denied": {
        container.removeAttribute("aria-busy");
        const code = view.deniedCode || 403;
        let text;
        if (code === 401) text = "Sign in required to view this.";
        else if (code === 404) text = "Not found.";
        else text = "You do not have permission to view this.";
        // Do not include object count/title or existence leakage.
        container.innerHTML = wrap("denied", `<p class="sm-denied-message">${escapeHtml(text)}</p>`);
        return;
      }
      case "stale": {
        container.removeAttribute("aria-busy");
        const asOf = view.asOf ? `<div class="sm-stale-banner">Showing the last known good snapshot as of ${escapeHtml(view.asOf)}. New risk actions are disabled until this refreshes.</div>` : "";
        container.innerHTML = wrap("stale", `${asOf}${view.staleHtml || ""}`);
        return;
      }
      case "partial": {
        container.removeAttribute("aria-busy");
        const missing = (view.missingFields || []).map((f) => `<li>${escapeHtml(f)}</li>`).join("");
        container.innerHTML = wrap(
          "partial",
          `<div class="sm-partial-banner">Some fields could not be loaded. Totals below exclude them rather than treating them as zero.${missing ? `<ul>${missing}</ul>` : ""}</div>${view.partialHtml || ""}`
        );
        return;
      }
      case "timeout": {
        container.removeAttribute("aria-busy");
        const opId = view.operationId ? `<div class="sm-op-id">Operation ID: ${escapeHtml(view.operationId)}</div>` : "";
        const retryBtn = view.onRetry ? `<button type="button" class="sm-retry">Retry</button>` : "";
        container.innerHTML = wrap(
          "timeout",
          `<p class="sm-timeout-message">Request timed out. Status is UNKNOWN, not failed -- do not resubmit blindly.</p>${opId}${retryBtn}`
        );
        if (view.onRetry) container.querySelector(".sm-retry").addEventListener("click", view.onRetry);
        return;
      }
      case "error": {
        container.removeAttribute("aria-busy");
        const corr = view.correlationId ? `<div class="sm-correlation-id">Correlation ID: ${escapeHtml(view.correlationId)}</div>` : "";
        const retryBtn = view.onRetry ? `<button type="button" class="sm-retry">Retry</button>` : "";
        container.innerHTML = wrap(
          "error",
          `<p class="sm-error-message">${escapeHtml(view.message || "Something went wrong loading this.")}</p>${corr}${retryBtn}`
        );
        if (view.onRetry) container.querySelector(".sm-retry").addEventListener("click", view.onRetry);
        return;
      }
      case "unsupported": {
        container.removeAttribute("aria-busy");
        container.innerHTML = wrap(
          "unsupported",
          `<p class="sm-unsupported-message">${escapeHtml(view.reason || "Not available in this build.")}</p>`
        );
        return;
      }
      case "conflict": {
        container.removeAttribute("aria-busy");
        container.innerHTML = wrap(
          "conflict",
          `<p class="sm-conflict-message">This ${escapeHtml(view.field || "value")} changed since you loaded it.</p>
           <div class="sm-conflict-diff"><div><span class="muted">Was</span> <span class="mono">${escapeHtml(view.oldValue)}</span></div>
           <div><span class="muted">Now</span> <span class="mono">${escapeHtml(view.newValue)}</span></div></div>
           <p class="sm-conflict-message">Your draft is preserved. Reconcile and request a fresh preview before confirming.</p>`
        );
        return;
      }
      case "session_expired": {
        container.removeAttribute("aria-busy");
        container.innerHTML = wrap(
          "session_expired",
          `<p class="sm-session-expired-message">Your session expired. Sign in again to continue -- nothing was resubmitted automatically.</p>`
        );
        return;
      }
      case "maintenance": {
        container.removeAttribute("aria-busy");
        container.innerHTML = wrap(
          "maintenance",
          `<p class="sm-maintenance-message">${escapeHtml(view.statusMessage || "This view is temporarily paused for maintenance.")}</p>
           <p class="sm-maintenance-message">This does not mean any open position was closed.</p>`
        );
        return;
      }
      default: {
        container.innerHTML = wrap("error", `<p class="sm-error-message">Unknown UI state.</p>`);
      }
    }
  }

  global.StateMatrix = { render, escapeHtml };
})(window);
