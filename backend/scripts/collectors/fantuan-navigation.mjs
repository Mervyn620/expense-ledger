export function isFantuanBaseState(state) {
  try {
    const url = new URL(String(state?.url || ""));
    return url.protocol === "https:"
      && url.hostname === "www.fantuanorder.com"
      && /^\/zh-CN(?:[/?#]|$)/.test(url.pathname);
  } catch {
    return false;
  }
}

export function isFantuanDocumentReady(state) {
  return isFantuanBaseState(state)
    && /^\/zh-CN\/?$/.test(new URL(state.url).pathname)
    && ["interactive", "complete"].includes(String(state?.readyState || ""));
}

export function canRecoverFantuanNavigation(error, state) {
  return /Timed out calling Chrome DevTools method Page\.navigate/i.test(
    String(error?.message || error || ""),
  ) && isFantuanBaseState(state);
}
