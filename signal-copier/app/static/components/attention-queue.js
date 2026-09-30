/* Components.renderAttentionQueue -- vertically stacked, most-severe-first
 * list of things that need a human's attention (an unprotected position, a
 * stale broker connection, a stale signal). Color-coded by --crit/--warn/
 * --accent. Vanilla JS, no dependencies.
 *
 * Usage:
 *   Components.renderAttentionQueue(container, {
 *     items: [
 *       { severity: "critical", category: "protection_deficit",
 *         text: "AAPL on paper_main is unprotected",
 *         ageSeconds: 340, correlationId: "evt_123" },
 *       { severity: "warning", category: "stale_price", text: "..." },
 *       { severity: "info", text: "..." },
 *     ],
 *   });
 *
 * 2026-09 design review ("attention queue ordered by severity: Protection
 * deficit -> unknown order -> broker disconnect -> stale price -> capital
 * breach -> ordinary warning"): `item.category`, when one of the 6 names
 * below, governs ordering directly (that exact sequence), taking priority
 * over `severity`. `severity` still drives the item's color (sev-critical/
 * sev-warning/sev-info) and is the sole ordering key for an item with no
 * `category` (or an unrecognized one) -- unchanged, back-compatible
 * behavior for any caller that hasn't adopted categories yet. A
 * categorized item always sorts before an uncategorized one.
 *
 * Renders an explicit empty state ("No open attention items") when
 * `items` is empty/absent, so a confirmed-empty queue is visibly
 * confirmed-empty rather than looking broken/blank.
 */
(function (global) {
  "use strict";

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  const SEVERITY_ORDER = { critical: 0, warning: 1, info: 2 };
  const VALID_SEVERITIES = new Set(["critical", "warning", "info"]);

  // Exact sequence the 2026-09 design review asked for. Ordinal position
  // in this object IS the rank -- never re-derived from severity, so a
  // caller's severity choice (which only drives color) can't silently
  // reorder these relative to each other.
  const CATEGORY_ORDER = {
    protection_deficit: 0,
    unknown_order: 1,
    broker_disconnect: 2,
    stale_price: 3,
    capital_breach: 4,
    ordinary_warning: 5,
  };

  function fmtAge(seconds) {
    if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return null;
    const s = Math.max(0, Math.floor(seconds));
    if (s < 60) return `${s}s ago`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    const d = Math.floor(h / 24);
    return `${d}d ago`;
  }

  function renderAttentionQueue(container, opts) {
    if (!container) return;
    const rawItems = (opts && opts.items) || [];
    if (!rawItems.length) {
      container.innerHTML = `<div class="attention-queue-empty">No open attention items.</div>`;
      return;
    }

    const items = rawItems
      .map((item, idx) => ({ ...item, _idx: idx }))
      .sort((a, b) => {
        const ca = CATEGORY_ORDER[a.category];
        const cb = CATEGORY_ORDER[b.category];
        if (ca !== undefined && cb !== undefined) {
          if (ca !== cb) return ca - cb;
          return a._idx - b._idx; // stable within same category
        }
        if (ca !== undefined) return -1; // categorized always ranks above uncategorized
        if (cb !== undefined) return 1;
        const sa = SEVERITY_ORDER[a.severity] ?? 3;
        const sb = SEVERITY_ORDER[b.severity] ?? 3;
        if (sa !== sb) return sa - sb;
        return a._idx - b._idx; // stable within same severity
      });

    const rows = items
      .map((item) => {
        const severity = VALID_SEVERITIES.has(item.severity) ? item.severity : "info";
        const age = fmtAge(item.ageSeconds);
        const ageBadge = age ? `<span class="attention-age-badge">${escapeHtml(age)}</span>` : "";
        const meta = item.correlationId
          ? `<div class="attention-item-meta"><span>${escapeHtml(item.correlationId)}</span></div>`
          : "";
        return `
          <div class="attention-item sev-${severity}" role="listitem">
            <div class="attention-item-body">
              <div class="attention-item-text">${escapeHtml(item.text ?? "")}</div>
              ${meta}
            </div>
            ${ageBadge}
          </div>`;
      })
      .join("");

    container.innerHTML = `<div class="attention-queue" role="list" aria-label="Attention items">${rows}</div>`;
  }

  global.Components = global.Components || {};
  global.Components.renderAttentionQueue = renderAttentionQueue;
})(window);
