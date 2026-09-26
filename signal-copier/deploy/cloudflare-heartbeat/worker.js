/**
 * Independent monitor -- polls the active site's GET /health on a schedule
 * and alerts on sustained failure. This Worker never holds a broker
 * credential, never talks to a broker, and never starts, stops, or
 * reconfigures anything: it can only observe and notify. Promotion is a
 * human decision made by following deploy/RUNBOOK.md, not an action this
 * script takes.
 *
 * Config (set via `wrangler secret put` / wrangler.toml vars, never
 * hardcoded): ACTIVE_HEALTH_URL, ALERT_WEBHOOK_URL, FAILURE_THRESHOLD.
 * State (consecutive failure count) lives in the KV binding HEARTBEAT_KV --
 * Workers Free's request/CPU budget is easily enough for a once-a-minute
 * cron poll; do not add a busy loop or anything that approximates a
 * continuous process here.
 */

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(checkOnce(env));
  },

  // Manual trigger for testing -- GET this Worker's own URL. Never wired
  // to anything that mutates state beyond the failure counter.
  async fetch(request, env) {
    const result = await checkOnce(env);
    return new Response(JSON.stringify(result), {
      headers: { "content-type": "application/json" },
    });
  },
};

async function checkOnce(env) {
  const threshold = Number(env.FAILURE_THRESHOLD || "3");
  let healthy = false;
  let detail = "";

  try {
    const response = await fetch(env.ACTIVE_HEALTH_URL, {
      cf: { cacheTtl: 0 },
      signal: AbortSignal.timeout(10_000),
    });
    const body = await response.json().catch(() => ({}));
    // Liveness (status 200) is not the same as actually making progress --
    // mirror app/main.py's own health contract rather than treating any
    // 200 as fully healthy.
    healthy =
      response.ok &&
      body.status === "ok" &&
      body.database_ok !== false &&
      body.price_monitor_ok !== false &&
      body.reconciler_ok !== false;
    detail = JSON.stringify(body);
  } catch (err) {
    detail = `fetch failed: ${err}`;
  }

  const key = "consecutive_failures";
  const previous = Number((await env.HEARTBEAT_KV.get(key)) || "0");
  const current = healthy ? 0 : previous + 1;
  await env.HEARTBEAT_KV.put(key, String(current));

  if (!healthy && current === threshold) {
    // Alert exactly once per incident (at the threshold), not on every
    // poll after it -- avoids spamming the same "site is down" alert
    // every minute for a multi-hour outage.
    await sendAlert(env, `Signal Copier active site unhealthy for ${current} consecutive checks: ${detail}`);
  } else if (healthy && previous >= threshold) {
    await sendAlert(env, "Signal Copier active site recovered.");
  }

  return { healthy, consecutive_failures: current, detail };
}

async function sendAlert(env, message) {
  if (!env.ALERT_WEBHOOK_URL) return;
  await fetch(env.ALERT_WEBHOOK_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ text: message }),
  }).catch(() => {});
}
