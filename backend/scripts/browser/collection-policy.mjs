import { readFile } from "node:fs/promises";
import path from "node:path";
import { atomicWriteFile } from "../lib/atomic-file.mjs";
import { withFileLock } from "../lib/file-lock.mjs";
import { sleep } from "./cdp-client.mjs";


export function observedDates(rows) {
  const dates = Array.from(rows, row => row.date).filter(date => /^\d{4}-\d{2}-\d{2}$/.test(date)).sort();
  return dates.length ? { first: dates[0], last: dates.at(-1) } : null;
}

const DEFAULTS = Object.freeze({
  min_action_delay_ms: 700,
  max_action_delay_ms: 1400,
  settle_min_ms: 900,
  settle_max_ms: 1700,
  max_retries: 2,
  backoff_base_ms: 1200,
  backoff_max_ms: 10000,
  block_cooldown_minutes: 120,
  failure_cooldown_minutes: 30,
  max_consecutive_failures: 3,
  cdp_call_timeout_ms: 15000,
});

export class CollectionBlockedError extends Error {
  constructor(message) {
    super(message);
    this.name = "CollectionBlockedError";
  }
}

export class CollectionSessionError extends Error {
  constructor(message) {
    super(message);
    this.name = "CollectionSessionError";
  }
}

function boundedNumber(value, fallback, minimum, maximum) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(maximum, Math.max(minimum, parsed));
}

function normalizeConfig(value = {}) {
  return {
    min_action_delay_ms: boundedNumber(value.min_action_delay_ms, DEFAULTS.min_action_delay_ms, 0, 30000),
    max_action_delay_ms: boundedNumber(value.max_action_delay_ms, DEFAULTS.max_action_delay_ms, 0, 30000),
    settle_min_ms: boundedNumber(value.settle_min_ms, DEFAULTS.settle_min_ms, 0, 30000),
    settle_max_ms: boundedNumber(value.settle_max_ms, DEFAULTS.settle_max_ms, 0, 30000),
    max_retries: boundedNumber(value.max_retries, DEFAULTS.max_retries, 0, 5),
    backoff_base_ms: boundedNumber(value.backoff_base_ms, DEFAULTS.backoff_base_ms, 100, 60000),
    backoff_max_ms: boundedNumber(value.backoff_max_ms, DEFAULTS.backoff_max_ms, 100, 120000),
    block_cooldown_minutes: boundedNumber(value.block_cooldown_minutes, DEFAULTS.block_cooldown_minutes, 5, 1440),
    failure_cooldown_minutes: boundedNumber(value.failure_cooldown_minutes, DEFAULTS.failure_cooldown_minutes, 5, 1440),
    max_consecutive_failures: boundedNumber(value.max_consecutive_failures, DEFAULTS.max_consecutive_failures, 1, 10),
    cdp_call_timeout_ms: boundedNumber(value.cdp_call_timeout_ms, DEFAULTS.cdp_call_timeout_ms, 1000, 120000),
  };
}

async function readJson(file, fallback) {
  try {
    return JSON.parse(await readFile(file, "utf8"));
  } catch {
    return fallback;
  }
}

function cleanReason(value) {
  return String(value || "unknown")
    .replace(/https?:\/\/\S+/gi, "[url]")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 180);
}

function randomBetween(minimum, maximum, randomFn) {
  const low = Math.min(minimum, maximum);
  const high = Math.max(minimum, maximum);
  return Math.round(low + (high - low) * randomFn());
}

export function classifyPageState({ url = "", title = "", text = "" } = {}) {
  const combined = `${title}\n${text}`.toLowerCase();
  const normalizedUrl = String(url || "").toLowerCase();
  const blockedPatterns = [
    /captcha/,
    /verify (?:that )?you are human/,
    /unusual traffic/,
    /access denied/,
    /too many requests/,
    /temporarily blocked/,
    /robot check/,
    /security challenge/,
    /验证码/,
    /访问过于频繁/,
    /请求过于频繁/,
    /访问被拒绝/,
  ];
  if (blockedPatterns.some((pattern) => pattern.test(combined))) {
    return { status: "blocked", reason: "A challenge or rate-limit page was detected." };
  }
  if (
    /\/\/auth\./.test(normalizedUrl)
    || /\/(?:login|signin|sign-in)(?:[/?#]|$)/.test(normalizedUrl)
    || /sign in to (?:continue|view|your account)/.test(combined)
    || /请先登录|登录后查看/.test(combined)
  ) {
    return { status: "login_required", reason: "The dedicated browser session requires login." };
  }
  return { status: "ok", reason: "" };
}

export function classifyCollectionError(error) {
  if (error instanceof CollectionBlockedError || error instanceof CollectionSessionError) {
    return { category: "session", transient: false };
  }
  const message = String(error?.message || error);
  const rules = [
    ["certificate_error", /net::ERR_CERT_|certificate (?:verify|verification|date|authority|name)|CERT_[A-Z_]+/i, false],
    ["tls_handshake_failed", /net::ERR_SSL_|SSL_PROTOCOL_ERROR|TLS (?:handshake|protocol)|ERR_BAD_SSL_CLIENT_AUTH_CERT/i, false],
    ["proxy_unavailable", /net::ERR_PROXY_CONNECTION_FAILED|net::ERR_TUNNEL_CONNECTION_FAILED|proxy (?:connection|tunnel).*(?:failed|closed|unavailable)/i, true],
    ["dns_failed", /net::ERR_NAME_NOT_RESOLVED|EAI_AGAIN|ENOTFOUND|DNS (?:lookup|resolution).*(?:failed|timed out)/i, true],
    ["connection_reset", /net::ERR_CONNECTION_(?:RESET|CLOSED|ABORTED)|ECONNRESET|connection closed/i, true],
    ["connection_refused", /net::ERR_CONNECTION_REFUSED|ECONNREFUSED|connection is not open/i, true],
    ["timeout", /net::ERR_(?:CONNECTION_)?TIMED_OUT|timed out|timeout/i, true],
    ["target_closed", /target closed|Chrome DevTools connection closed/i, true],
    ["service_unavailable", /HTTP 5\d\d|temporarily unavailable/i, true],
    ["response_missing", /no .* response captured/i, true],
  ];
  for (const [category, pattern, transient] of rules) {
    if (pattern.test(message)) return { category, transient };
  }
  return { category: "ordinary_failure", transient: false };
}

export function isTransientCollectionError(error) {
  return classifyCollectionError(error).transient;
}

export async function createCollectionPolicy(platform, options = {}) {
  const root = path.resolve(options.root || process.cwd());
  const workflowPath = path.join(root, "config", "workflow.json");
  const statePath = path.resolve(options.statePath || path.join(root, ".codex", "collector-health.json"));
  const workflow = options.config
    ? { collector_policy: options.config }
    : await readJson(workflowPath, {});
  const common = workflow.collector_policy || {};
  const perPlatform = common.platforms?.[platform] || {};
  const config = normalizeConfig({ ...common, ...perPlatform });
  const sleepFn = options.sleepFn || sleep;
  const randomFn = options.randomFn || Math.random;
  const nowFn = options.nowFn || Date.now;

  async function readState() {
    return readJson(statePath, { version: 1, platforms: {} });
  }

  async function writePlatformState(next) {
    // Collectors for different stores can run at the same time; the lock keeps one
    // platform's update from overwriting another's (for example a block cooldown).
    await withFileLock(statePath, async () => {
      const state = await readState();
      state.version = 1;
      state.updated_at = new Date(nowFn()).toISOString();
      state.platforms = state.platforms || {};
      state.platforms[platform] = {
        ...(state.platforms[platform] || {}),
        ...next,
      };
      await atomicWriteFile(statePath, `${JSON.stringify(state, null, 2)}\n`);
    });
  }

  async function assertAvailable() {
    const state = await readState();
    const current = state.platforms?.[platform] || {};
    if (current.blocked_until && Date.parse(current.blocked_until) > nowFn()) {
      throw new CollectionBlockedError(
        `${platform} collection is cooling down until ${current.blocked_until}: ${current.reason || "previous block"}`,
      );
    }
  }

  async function pace(multiplier = 1) {
    const milliseconds = randomBetween(
      config.min_action_delay_ms * multiplier,
      config.max_action_delay_ms * multiplier,
      randomFn,
    );
    if (milliseconds > 0) await sleepFn(milliseconds);
    return milliseconds;
  }

  async function settle(multiplier = 1) {
    const milliseconds = randomBetween(
      config.settle_min_ms * multiplier,
      config.settle_max_ms * multiplier,
      randomFn,
    );
    if (milliseconds > 0) await sleepFn(milliseconds);
    return milliseconds;
  }

  async function recordBlock(reason, minutes = config.block_cooldown_minutes) {
    const clean = cleanReason(reason);
    await writePlatformState({
      status: "blocked",
      reason: clean,
      last_failure: new Date(nowFn()).toISOString(),
      blocked_until: new Date(nowFn() + minutes * 60000).toISOString(),
    });
  }

  async function recordLoginRequired(reason) {
    await writePlatformState({
      status: "login_required",
      reason: cleanReason(reason),
      last_failure: new Date(nowFn()).toISOString(),
      blocked_until: null,
    });
  }

  async function recordFailure(reason) {
    const state = await readState();
    const current = state.platforms?.[platform] || {};
    const failures = Number(current.consecutive_failures || 0) + 1;
    const next = {
      status: failures >= config.max_consecutive_failures ? "cooldown" : "failed",
      reason: cleanReason(reason),
      last_failure: new Date(nowFn()).toISOString(),
      consecutive_failures: failures,
      blocked_until: null,
    };
    if (failures >= config.max_consecutive_failures) {
      next.blocked_until = new Date(
        nowFn() + config.failure_cooldown_minutes * 60000,
      ).toISOString();
    }
    await writePlatformState(next);
  }

  async function recordSuccess(summary = {}) {
    await writePlatformState({
      status: "healthy",
      reason: "",
      blocked_until: null,
      consecutive_failures: 0,
      last_success: new Date(nowFn()).toISOString(),
      summary,
    });
  }

  async function retry(label, operation, retryOptions = {}) {
    const retries = retryOptions.retries ?? config.max_retries;
    const shouldRetry = retryOptions.shouldRetry || isTransientCollectionError;
    let lastError;
    for (let attempt = 0; attempt <= retries; attempt += 1) {
      try {
        return await operation(attempt);
      } catch (error) {
        lastError = error;
        if (attempt >= retries || !shouldRetry(error)) throw error;
        const exponential = Math.min(
          config.backoff_max_ms,
          config.backoff_base_ms * (2 ** attempt),
        );
        const waitMs = randomBetween(exponential * 0.75, exponential * 1.25, randomFn);
        await sleepFn(waitMs);
      }
    }
    throw new Error(`${label} failed: ${lastError?.message || lastError}`);
  }

  async function inspectPage(cdp) {
    const result = await cdp.call("Runtime.evaluate", {
      expression: `JSON.stringify(({
        url: location.href,
        title: document.title,
        text: String(document.body?.innerText || "").slice(0, 3000)
      }))`,
      returnByValue: true,
    });
    const state = JSON.parse(result.result?.value || "{}");
    const classification = classifyPageState(state);
    if (classification.status === "blocked") {
      await recordBlock(classification.reason);
      throw new CollectionBlockedError(`${platform}: ${classification.reason}`);
    }
    if (classification.status === "login_required") {
      await recordLoginRequired(classification.reason);
      throw new CollectionSessionError(`${platform}: ${classification.reason}`);
    }
    return state;
  }

  async function handleHttpStatus(status, context = "request") {
    const code = Number(status);
    if (code === 401) {
      await recordLoginRequired(`${context} returned HTTP 401.`);
      throw new CollectionSessionError(`${platform}: login is required (${context}).`);
    }
    if (code === 403 || code === 429) {
      await recordBlock(`${context} returned HTTP ${code}.`);
      throw new CollectionBlockedError(`${platform}: ${context} returned HTTP ${code}.`);
    }
    if (code >= 500) {
      throw new Error(`HTTP ${code} from ${context}`);
    }
  }

  await assertAvailable();
  return {
    platform,
    config,
    pace,
    settle,
    retry,
    inspectPage,
    handleHttpStatus,
    recordBlock,
    recordLoginRequired,
    recordFailure,
    recordSuccess,
  };
}

export async function runWithCollectionPolicy(platform, operation, options = {}) {
  const policy = await createCollectionPolicy(platform, options);
  try {
    const result = await operation(policy);
    await policy.recordSuccess(options.summary?.(result) || {});
    return result;
  } catch (error) {
    if (!(error instanceof CollectionBlockedError) && !(error instanceof CollectionSessionError)) {
      const classification = classifyCollectionError(error);
      const reason = classification.category === "ordinary_failure"
        ? (error?.message || error)
        : `${classification.category}: ${error?.message || error}`;
      await policy.recordFailure(reason);
    }
    throw error;
  }
}
