import {
  findOrdinaryOAuthOrdersTab,
  isOrdinaryLoginTabUrl,
  isOrdinaryOrdersTabUrl,
} from "./the-ordinary-tab-selection.mjs";

const ORDINARY_HOSTS = new Set(["theordinary.com", "www.theordinary.com"]);

export function classifyOrdinaryAuthFacts(facts) {
  if (!facts || !facts.host_allowed || !facts.login_page) return "not_login";
  if (facts.challenge_visible) return "manual_login_required";
  if (facts.google_button_count !== 1 || !facts.google_url_allowed) {
    return "manual_login_required";
  }
  return "auto_google_once";
}

export function classifyOrdinaryEntryState(authFacts, pageFacts) {
  if (authFacts?.login_page) {
    if (authFacts.challenge_visible) return "login_ready";
    if (authFacts.google_button_count === 0) return "waiting";
    return "login_ready";
  }
  if (
    isOrdinaryOrdersLocation(authFacts)
    && ((pageFacts?.orders || []).length > 0 || pageFacts?.empty_signal)
  ) {
    return "orders_ready";
  }
  return "waiting";
}

export function ordinaryAuthFactsExpression() {
  return `JSON.stringify((() => {
    const visible = element => {
      if (!element) return false;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none"
        && style.visibility !== "hidden"
        && Number(style.opacity || 1) > 0
        && rect.width > 0
        && rect.height > 0;
    };
    const host = location.hostname.toLowerCase();
    const path = location.pathname;
    const hostAllowed = ["theordinary.com", "www.theordinary.com"].includes(host);
    const loginPage = hostAllowed && (
      /^\\/en-ca\\/login(?:[/?#]|$)/i.test(path)
      || /\\/Account-(?:Show|Login)(?:[/?#]|$)/i.test(path)
    );
    const buttons = [...document.querySelectorAll("a.oauth-google[data-url]")]
      .filter(visible);
    const allowed = buttons.length === 1 && buttons.every(button => {
      try {
        const target = new URL(button.getAttribute("data-url") || "", location.href);
        return ["theordinary.com", "www.theordinary.com"].includes(target.hostname.toLowerCase())
          && /\\/SocialLogin-OAuthLogin$/i.test(target.pathname)
          && target.searchParams.get("oauthProvider") === "Google";
      } catch {
        return false;
      }
    });
    const challenge = [...document.querySelectorAll(
      'input[autocomplete="one-time-code"], input[name*="otp" i], '
      + 'iframe[src*="recaptcha" i], iframe[src*="challenges.cloudflare.com" i]'
    )].some(visible);
    return {
      host_allowed: hostAllowed,
      login_page: loginPage,
      path,
      google_button_count: buttons.length,
      google_url_allowed: allowed,
      challenge_visible: challenge
    };
  })())`;
}

export function ordinaryGoogleClickExpression() {
  return `JSON.stringify((() => {
    const visible = element => {
      if (!element) return false;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none"
        && style.visibility !== "hidden"
        && Number(style.opacity || 1) > 0
        && rect.width > 0
        && rect.height > 0;
    };
    const buttons = [...document.querySelectorAll("a.oauth-google[data-url]")]
      .filter(visible)
      .filter(button => {
        try {
          const target = new URL(button.getAttribute("data-url") || "", location.href);
          return ["theordinary.com", "www.theordinary.com"].includes(target.hostname.toLowerCase())
            && /\\/SocialLogin-OAuthLogin$/i.test(target.pathname)
            && target.searchParams.get("oauthProvider") === "Google";
        } catch {
          return false;
        }
      });
    if (buttons.length !== 1) return { clicked: false };
    buttons[0].click();
    return { clicked: true };
  })())`;
}

export function isOrdinaryOrdersLocation(facts) {
  return Boolean(
    facts
    && facts.host_allowed
    && /^\/en-ca\/orders(?:[/?#]|$)/i.test(String(facts.path || "")),
  );
}

export function isAllowedOrdinaryHost(value) {
  return ORDINARY_HOSTS.has(String(value || "").toLowerCase());
}

export async function runOrdinaryDetailWithRecovery({
  cdp,
  fetchDetail,
  recover,
}) {
  const first = await fetchDetail(cdp);
  if (!first?.login_signal) {
    return {
      detail: first,
      cdp,
      recovery: null,
      retried: false,
    };
  }

  const recovery = await recover(cdp);
  const recoveredCdp = recovery?.cdp || cdp;
  if (recovery?.manual) {
    return {
      detail: first,
      cdp: recoveredCdp,
      recovery,
      retried: false,
    };
  }

  return {
    detail: await fetchDetail(recoveredCdp),
    cdp: recoveredCdp,
    recovery,
    retried: true,
  };
}

export async function runOrdinaryOAuthRecovery({
  cdp,
  port,
  currentTabId,
  timeoutMs = 60000,
  now = () => Date.now(),
  listTabs,
  sleep,
  evaluateAuthFacts,
  clickGoogle,
  connectCdp,
  waitForDocument,
}) {
  const initial = await evaluateAuthFacts(cdp);
  const action = classifyOrdinaryAuthFacts(initial);
  if (action === "not_login") {
    return {
      attempted: false,
      restored: false,
      manual: false,
      timed_out: false,
      cdp,
      tabId: currentTabId,
    };
  }
  if (action !== "auto_google_once") {
    return {
      attempted: false,
      restored: false,
      manual: true,
      timed_out: false,
      cdp,
      tabId: currentTabId,
    };
  }

  const initialTabs = await listTabs(port);
  const initialTabIds = new Set(
    initialTabs.map((tab) => String(tab?.id || "")).filter(Boolean),
  );
  if (currentTabId) initialTabIds.add(String(currentTabId));

  const clicked = await clickGoogle(cdp);
  if (!clicked?.clicked) {
    return {
      attempted: true,
      restored: false,
      manual: true,
      timed_out: false,
      cdp,
      tabId: currentTabId,
    };
  }

  const startedAt = now();
  while (now() - startedAt < timeoutMs) {
    const remaining = timeoutMs - (now() - startedAt);
    await sleep(Math.min(500, Math.max(1, remaining)));
    let tabs;
    try {
      tabs = await listTabs(port);
    } catch {
      continue;
    }

    const current = tabs.find(
      (tab) => String(tab?.id || "") === String(currentTabId || ""),
    );
    // Only inspect the original target while CDP reports an exact The Ordinary
    // login/orders URL. An OAuth provider page is observed as target metadata
    // only and is never connected to or evaluated here.
    if (
      current
      && (isOrdinaryOrdersTabUrl(current.url) || isOrdinaryLoginTabUrl(current.url))
    ) {
      try {
        const facts = await evaluateAuthFacts(cdp);
        if (isOrdinaryOrdersLocation(facts)) {
          return {
            attempted: true,
            restored: true,
            manual: false,
            timed_out: false,
            cdp,
            tabId: currentTabId,
          };
        }
      } catch {
        // The original target may be swapping documents; keep polling.
      }
    }

    const callbackTab = findOrdinaryOAuthOrdersTab(tabs, initialTabIds);
    if (!callbackTab) continue;
    let callbackCdp;
    try {
      callbackCdp = await connectCdp(callbackTab.webSocketDebuggerUrl);
      await callbackCdp.call("Page.enable");
      await callbackCdp.call("Runtime.enable");
      await waitForDocument(callbackCdp);
      const callbackFacts = await evaluateAuthFacts(callbackCdp);
      if (isOrdinaryOrdersLocation(callbackFacts)) {
        return {
          attempted: true,
          restored: true,
          manual: false,
          timed_out: false,
          cdp: callbackCdp,
          tabId: callbackTab.id,
        };
      }
    } catch {
      // The same-site callback may still be swapping documents; keep polling.
    }
    if (callbackCdp) callbackCdp.close();
  }
  return {
    attempted: true,
    restored: false,
    manual: true,
    timed_out: true,
    cdp,
    tabId: currentTabId,
  };
}
