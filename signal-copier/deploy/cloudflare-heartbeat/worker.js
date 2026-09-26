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
 * State (consecutive failure count) lives in the KV binding HEARTBEAT_KV.
 * Workers Free's request/CPU budget is easily enough for a once-a-minute
 * cron poll; the KV *write* budget is much tighter (Cloudflare currently
 * documents 1,000 free KV writes/day -- a naive write on every poll at
 * once-a-minute is 1,440/day, already over budget before any manual
 * checks). See the change-only KV write below for how this stays under it.
 *
 * The manual HTTP trigger (`fetch`) runs the exact same check as the cron
 * (`scheduled`) -- there is deliberately no separate "read-only" mode that
 * silently disagrees with what the cron would report. The residual risk
 * this accepts: a caller who can reach this Worker's public URL can cause
 * an extra check to run (same as an early cron tick would) and can
 * therefore advance the failure counter or an alert slightly sooner than
 * it otherwise would have -- but they cannot fabricate a health result,
 * since every check is a REAL fetch against ACTIVE_HEALTH_URL, never
 * caller-supplied data. That is a materially smaller concern than
 * spoofing arbitrary monitoring state, which this design does not permit.
 */

const ALERT_ATTEMPTS = 3;
const ALERT_RETRY_BACKOFF_MS = 200;

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(checkOnce(env));
  },

  async fetch(request, env) {
    const result = await checkOnce(env);
    return new Response(JSON.stringify(result), {
      status: result.http_status || 200,
      headers: { "content-type": "application/json" },
    });
  },
};

function resolveThreshold(env) {
  const parsed = Number(env.FAILURE_THRESHOLD);
  if (!Number.isFinite(parsed) || parsed <= 0) return null;
  return parsed;
}

function isHealthyBody(body) {
  // A malformed/unparseable response body (network error, a non-JSON
  // error page, a truncated response) must not be treated as healthy just
  // because none of its fields are strictly `false`. Every expected field
  // must be explicitly present with the correct type -- undefined/missing
  // is exactly the "can't prove this is healthy" case, not a pass.
  if (typeof body !== "object" || body === null) return false;
  return (
    body.status === "ok" &&
    body.database_ok === true &&
    body.price_monitor_ok === true &&
    body.reconciler_ok === true
  );
}

async function checkOnce(env) {
  const threshold = resolveThreshold(env);
  if (threshold === null) {
    // An invalid/non-numeric/non-positive threshold must not silently make
    // this monitor inert (a NaN or negative comparison is always false, so
    // an alert would simply never fire) -- surface it as a config error
    // rather than quietly defaulting and hiding the misconfiguration.
    return { http_status: 400, error: "FAILURE_THRESHOLD is not a valid positive number" };
  }

  let healthy = false;
  let detail = "";
  let sawValidResponse = false;

  try {
    const response = await fetch(env.ACTIVE_HEALTH_URL, {
      cf: { cacheTtl: 0 },
      signal: AbortSignal.timeout(10_000),
    });
    let body = null;
    try {
      body = await response.json();
      sawValidResponse = true;
    } catch {
      body = null; // non-JSON/malformed body -- sawValidResponse stays false
    }
    healthy = response.ok && sawValidResponse && isHealthyBody(body);
    detail = sawValidResponse ? JSON.stringify(body) : `malformed response body (status ${response.status})`;
  } catch (err) {
    detail = `fetch failed: ${err}`;
  }

  const key = "consecutive_failures";
  const previous = Number((await env.HEARTBEAT_KV.get(key)) || "0");
  const current = healthy ? 0 : previous + 1;

  // Only write when the counter actually changes -- during a healthy
  // steady state (the overwhelming majority of polls) current stays 0 and
  // matches previous, so no write happens at all. This is what keeps a
  // once-a-minute cron (1,440 polls/day) well under the free KV write
  // allowance instead of writing on every single poll.
  if (current !== previous) {
    await env.HEARTBEAT_KV.put(key, String(current));
  }

  const alertConfigured = Boolean(env.ALERT_WEBHOOK_URL);
  let alertDelivery = alertConfigured ? "not_due" : "unconfigured";

  if (!healthy && current === threshold) {
    // Alert exactly once per incident (at the threshold), not on every
    // poll after it -- avoids spamming the same "site is down" alert
    // every minute for a multi-hour outage.
    alertDelivery = await sendAlertWithRetry(
      env, `Signal Copier active site unhealthy for ${current} consecutive checks: ${detail}`
    );
  } else if (healthy && previous >= threshold) {
    alertDelivery = await sendAlertWithRetry(env, "Signal Copier active site recovered.");
  }

  return {
    healthy,
    consecutive_failures: current,
    detail,
    alert_configured: alertConfigured,
    alert_delivery: alertDelivery,
  };
}

/** Returns "unconfigured", "sent", or "failed" -- always visible in the
 * response, never silently swallowed. */
async function sendAlertWithRetry(env, message, attempts = ALERT_ATTEMPTS) {
  if (!env.ALERT_WEBHOOK_URL) return "unconfigured";
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      const response = await fetch(env.ALERT_WEBHOOK_URL, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text: message }),
        signal: AbortSignal.timeout(10_000),
      });
      if (response.ok) return "sent";
    } catch {
      // fall through to retry
    }
    if (attempt < attempts) {
      await new Promise((resolve) => setTimeout(resolve, attempt * ALERT_RETRY_BACKOFF_MS));
    }
  }
  // All attempts failed -- there is no further fallback channel from inside
  // a Worker. This is a known, disclosed limit (see deploy/README.md): a
  // failed alert delivery is visible in the response ("failed") but not
  // recoverable past this point.
  return "failed";
}
