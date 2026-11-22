export function isOrdinaryOrdersTabUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return url.protocol === "https:"
      && ["theordinary.com", "www.theordinary.com"].includes(
        url.hostname.toLowerCase(),
      )
      && /^\/en-ca\/orders(?:[/?#]|$)/i.test(url.pathname);
  } catch {
    return false;
  }
}

export function isOrdinaryLoginTabUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:"
      && ["theordinary.com", "www.theordinary.com"].includes(url.hostname.toLowerCase())
      && /^\/en-ca\/login(?:[/?#]|$)/i.test(url.pathname);
  } catch {
    return false;
  }
}

export function findOrdinaryOAuthOrdersTab(tabs, initialTabIds = []) {
  const initial = new Set(
    [...initialTabIds].map((value) => String(value || "")),
  );
  return (Array.isArray(tabs) ? tabs : []).find((tab) => (
    tab
    && tab.type === "page"
    && tab.webSocketDebuggerUrl
    && !initial.has(String(tab.id || ""))
    && isOrdinaryOrdersTabUrl(tab.url)
  )) || null;
}

export function ordinaryOrdersTabScore(facts) {
  if (!facts || !isOrdinaryOrdersTabUrl(facts.url)) return -1;
  return (
    Math.max(0, Number(facts.valid_card_count) || 0) * 100
    + Math.min(20, Math.max(0, Number(facts.child_count) || 0) * 5)
    + (facts.empty_signal ? 80 : 0)
    + (facts.root_found ? 5 : 0)
    + (facts.visibility_state === "visible" ? 3 : 0)
    + (["interactive", "complete"].includes(facts.ready_state) ? 2 : 0)
  );
}
