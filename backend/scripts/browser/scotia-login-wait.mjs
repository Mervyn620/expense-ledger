// A single deadline bounds user interaction; readiness is driven only by page events.
export function readScotiaReadiness() {
  const visible = element => Boolean(element && element.getClientRects().length
    && getComputedStyle(element).visibility !== "hidden"
    && getComputedStyle(element).display !== "none");
  const text = String(document.body?.innerText || "").slice(0, 5000);
  return {
    secure: location.protocol === "https:" && location.hostname === "secure.scotiabank.com",
    account: [...document.querySelectorAll(
      '#summaryAccountTitle-basic, [id*="summaryAccountTitle"], #transaction-filter__search-filter'
    )].some(visible),
    login: [...document.querySelectorAll('input[type="password"], input[autocomplete="username"]')].some(visible),
    verification: /one[- ]time|verification code|security code|two[- ]step|two[- ]factor|authenticator|验证码|二次验证|安全码/i.test(text)
      || /\/(?:verify|verification|mfa|challenge)(?:[/?#]|$)/i.test(location.pathname),
    blocked: /captcha|verify (?:that )?you are human|unusual traffic|access denied|robot check/i.test(text),
  };
}

export function scotiaAccountReady(state) {
  return state?.secure === true && state?.account === true
    && state?.login === false && state?.verification === false && state?.blocked === false;
}

export const READINESS_EXPRESSION = `(${readScotiaReadiness.toString()})()`;
const BINDING = "__expenseScotiaChanged";
const OBSERVER_KEY = "__expenseScotiaObserver";
export const OBSERVER_SOURCE = `(() => {
  if (window !== window.top || window.${OBSERVER_KEY}) return;
  let last = "", queued = false;
  const check = () => {
    queued = false;
    const state = JSON.stringify(${READINESS_EXPRESSION});
    if (state === last) return;
    last = state;
    if (typeof window.${BINDING} === "function") window.${BINDING}("changed");
  };
  const changed = () => {
    if (queued) return;
    queued = true;
    queueMicrotask(check);
  };
  const observer = new MutationObserver(changed);
  observer.observe(document, {subtree: true, childList: true, attributes: true, characterData: true});
  document.addEventListener("input", changed, true);
  window.addEventListener("pageshow", changed);
  window.${OBSERVER_KEY} = () => {
    observer.disconnect();
    document.removeEventListener("input", changed, true);
    window.removeEventListener("pageshow", changed);
    delete window.${OBSERVER_KEY};
  };
  check();
})()`;

export function createScotiaLoginWaiter(cdp, { timeoutMs = 600000, onState = () => {} } = {}) {
  let finished = false, started = false, checking = false, dirty = false;
  let scriptId, timer, resolve, reject;
  const result = new Promise((yes, no) => { resolve = yes; reject = no; });
  // Setup may still be awaiting CDP when a closed tab/deadline rejects the result.
  result.catch(() => {});
  const finish = error => {
    if (finished) return;
    finished = true;
    clearTimeout(timer);
    if (error) reject(error); else resolve("logged_in");
  };
  const check = async () => {
    dirty = true;
    if (!started || checking || finished) return;
    checking = true;
    try {
      while (dirty && !finished) {
        dirty = false;
        try {
          const response = await cdp.call("Runtime.evaluate", {
            expression: READINESS_EXPRESSION, returnByValue: true,
          });
          if (response.exceptionDetails) throw new Error("Could not inspect Scotia login state.");
          const state = response.result?.value;
          if (scotiaAccountReady(state)) finish();
          else onState(state?.verification || state?.blocked ? "verification_required" : "manual_login_required");
        } catch (error) {
          // A redirect destroys the old context; the next navigation/DOM event resumes inspection.
          if (!/context.*(destroyed|not found)|Cannot find context|Inspected target navigated/i.test(error.message)) throw error;
        }
      }
    } catch (error) { finish(error); }
    finally { checking = false; }
  };
  return {
    onMessage(message) {
      if ((message.method === "Runtime.bindingCalled" && message.params?.name === BINDING)
          || ["Page.frameNavigated", "Page.navigatedWithinDocument", "Page.domContentEventFired"].includes(message.method)) {
        void check();
      }
    },
    onClose() { finish(new Error("Scotia browser page closed. Reopen login and try again.")); },
    async wait() {
      timer = setTimeout(() => finish(new Error("Scotia login was not completed within 10 minutes. Start again when ready.")), timeoutMs);
      try {
        await cdp.call("Page.enable");
        await cdp.call("Runtime.enable");
        await cdp.call("Runtime.addBinding", {name: BINDING});
        const script = await cdp.call("Page.addScriptToEvaluateOnNewDocument", {source: OBSERVER_SOURCE});
        scriptId = script.identifier;
        started = true;
        try {
          await cdp.call("Runtime.evaluate", {expression: OBSERVER_SOURCE});
        } catch (error) {
          if (!/context.*(destroyed|not found)|Cannot find context|Inspected target navigated/i.test(error.message)) throw error;
        }
        void check();
        return await result;
      } finally {
        finished = true;
        clearTimeout(timer);
        // Remove hooks so another run cannot accumulate observers or trigger duplicate work.
        await cdp.call("Runtime.evaluate", {expression: `window.${OBSERVER_KEY}?.()`}).catch(() => {});
        if (scriptId) await cdp.call("Page.removeScriptToEvaluateOnNewDocument", {identifier: scriptId}).catch(() => {});
        await cdp.call("Runtime.removeBinding", {name: BINDING}).catch(() => {});
      }
    },
  };
}
