/* Components.renderAttentionQueue -- vertically stacked, most-severe-first
 * list of things that need a human's attention (an unprotected position, a
 * stale broker connection, a stale signal). Color-coded by --crit/--warn/
 * --accent. Vanilla JS, no dependencies.
 *
 * Usage:
 *   Components.renderAttentionQueue(container, {
 *     items: [
 *       { severity: "critical", text: "AAPL on paper_main is unprotected",
 *         ageSeconds: 340, correlationId: "evt_123" },
 *       { severity: "warning", text: "..." },
 *       { severity: "info", text: "..." },
 *     ],
 *   });
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
