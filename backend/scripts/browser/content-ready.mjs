import { randomUUID } from "node:crypto";
import { sleep } from "./cdp-client.mjs";

// Two matching observations avoid reading partially hydrated content. The
// caller supplies an accounting-content predicate, not just document.readyState.
export async function waitForContent({
  read, ready, inspect, label, timeoutMs = 30000,
  sleepFn = sleep, now = Date.now, signature = JSON.stringify,
}) {
  const started = now();
  let previous;
  while (now() - started < timeoutMs) {
    let value;
    try {
      value = await read();
      await inspect(value); // Login/challenge failures keep their policy status.
    } catch (error) {
      // Navigation can destroy the old execution context between CDP calls.
      if (!/execution context was destroyed|cannot find context with specified id/i.test(error.message || "")) throw error;
      previous = undefined;
      await sleepFn(250);
      continue;
    }
    if (ready(value)) {
      const current = signature(value);
      if (current === previous) return value;
      previous = current;
    } else {
      previous = undefined;
    }
    await sleepFn(250);
  }
  throw new Error(`Timed out waiting for ${label}.`);
}

export async function navigateToContent(cdp, policy, url, {
  expression, ready, label, timeoutMs = 30000,
}) {
  await policy.pace();
  // Page.navigate returns before the old document is replaced. A token on the
  // old document prevents its already-populated DOM from passing readiness.
  const token = randomUUID();
  await cdp.call("Runtime.evaluate", {
    expression: `document.__expenseNavigationToken = ${JSON.stringify(token)}`,
    returnByValue: true,
  });
  const navigation = await policy.retry(`navigate ${label}`, () => cdp.call("Page.navigate", { url }));
  if (navigation.errorText) throw new Error(`Navigation failed: ${navigation.errorText}`);
  return waitForContent({
    label, timeoutMs,
    inspect: state => state.fresh ? policy.inspectPage(cdp) : undefined,
    read: async () => {
      const result = await cdp.call("Runtime.evaluate", {
        expression: `JSON.stringify({ fresh: document.__expenseNavigationToken !== ${JSON.stringify(token)},
          readyState: document.readyState, url: location.href, content: (${expression}) })`,
        returnByValue: true,
      });
      if (result.exceptionDetails) throw new Error(`Unable to inspect ${label} content.`);
      return JSON.parse(result.result?.value || "{}");
    },
    ready: state => state.fresh && ["interactive", "complete"].includes(state.readyState) && ready(state.content),
  });
}
