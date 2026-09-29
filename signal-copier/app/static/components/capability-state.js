/* Components.renderCapabilityState -- Level 3 (evidence/technical detail)
 * shared component.
 *
 * REPLACES the old bare-italic-sentence pattern (.tr-not-tracked /
 * .tr-not-exposed -- "color: var(--muted); font-style: italic") with a
 * small structured badge + an optional collapsed <details> for the
 * reason/remediation prose. Same underlying honesty (never claims
 * something works that doesn't) -- just rendered so it doesn't compete
 * visually with Level 1/2 content. Vanilla JS, no dependencies.
 *
 * Usage:
 *   Components.renderCapabilityState(container, {
 *     status: "unsupported",       // required
 *     reason: "Broker X has no protective-stop API.",       // optional
 *     remediation: "Use a managed-lifecycle account instead.", // optional
 *     lastVerified: "2026-09-12T10:00:00Z",                 // optional
 *   });
 *
 * `status` is one of: implemented | configured | authenticated |
 * entitled | verified | unsupported | not_tracked.
 * Degrades gracefully: with only `status` given, still renders a
 * sensible badge and no <details> block.
 */
(function (global) {
  "use strict";

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  const VALID_STATUSES = new Set([
    "implemented", "configured", "authenticated", "entitled",
    "verified", "unsupported", "not_tracked",
  ]);

  const STATUS_LABELS = {
    implemented: "Implemented",
    configured: "Configured",
    authenticated: "Authenticated",
    entitled: "Entitled",
    verified: "Verified",
    unsupported: "Unsupported",
    not_tracked: "Not tracked",
  };

  function renderCapabilityState(container, opts) {
    if (!container) return;
    const raw = opts || {};
    const status = VALID_STATUSES.has(raw.status) ? raw.status : "not_tracked";
    const label = STATUS_LABELS[status];

    const hasDetail = Boolean(raw.reason || raw.remediation || raw.lastVerified);
    let detailHtml = "";
    if (hasDetail) {
      const parts = [];
      if (raw.reason) {
        parts.push(`<dt>Reason</dt><dd>${escapeHtml(raw.reason)}</dd>`);
      }
      if (raw.remediation) {
        parts.push(`<dt>Remediation</dt><dd>${escapeHtml(raw.remediation)}</dd>`);
      }
      if (raw.lastVerified) {
        parts.push(`<dt>Last verified</dt><dd>${escapeHtml(raw.lastVerified)}</dd>`);
      }
      detailHtml = `
        <details>
          <summary>Why / details</summary>
          <dl class="capability-detail-body">${parts.join("")}</dl>
        </details>`;
    }

    container.innerHTML = `
      <div class="capability-state">
        <span class="capability-badge status-${status}">${escapeHtml(label)}</span>
        ${detailHtml}
      </div>`;
  }

  global.Components = global.Components || {};
  global.Components.renderCapabilityState = renderCapabilityState;
})(window);
