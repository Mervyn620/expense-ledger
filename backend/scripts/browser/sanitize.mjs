function cleanText(value) {
  return String(value || "")
    .replace(/\u00a0/g, " ")
    .replace(/[ \t]+/g, " ")
    .trim();
}

export function sanitizeAccountingText(value) {
  return cleanText(value)
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[email]")
    .replace(/\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b/g, "[phone]")
    .replace(/\b(?:\d[ -]*?){13,19}\b/g, "[card]")
    .replace(/\b(ending in|card ending|last four)\s+\d{4}\b/gi, "$1 [last4]")
    .replace(/\b[A-Z]\d[A-Z][ -]?\d[A-Z]\d\b/gi, "[postal-code]")
    .replace(/\b(?:deliver(?:y|ed)? to|shipping address|billing address)\b[^,;\r\n]{0,160}/gi, "[address]")
    .replace(
      /\b\d{1,6}\s+(?:[A-Z0-9.'-]+\s+){1,6}(?:street|st|road|rd|avenue|ave|boulevard|blvd|drive|dr|lane|ln|court|ct|way|crescent|cres|place|pl|terrace|trail|circle)\b\.?/gi,
      "[address]",
    );
}

export function sanitizeAccountingUrl(value, allowedQueryKeys = ["mod", "modctx", "ps", "orderID"]) {
  try {
    const parsed = new URL(String(value || ""));
    if (!/^https?:$/.test(parsed.protocol)) return "";
    const allowed = new Set(allowedQueryKeys);
    for (const key of [...parsed.searchParams.keys()]) {
      if (!allowed.has(key)) parsed.searchParams.delete(key);
    }
    parsed.username = "";
    parsed.password = "";
    parsed.hash = "";
    return parsed.toString();
  } catch {
    return "";
  }
}

export const browserSanitizerSource = String.raw`
  const cleanAccountingText = (value) => String(value || "")
    .replace(/\u00a0/g, " ")
    .replace(/[ \t]+/g, " ")
    .trim();
  const sanitizeAccountingText = (value) => cleanAccountingText(value)
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[email]")
    .replace(/\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b/g, "[phone]")
    .replace(/\b(?:\d[ -]*?){13,19}\b/g, "[card]")
    .replace(/\b(ending in|card ending|last four)\s+\d{4}\b/gi, "$1 [last4]")
    .replace(/\b[A-Z]\d[A-Z][ -]?\d[A-Z]\d\b/gi, "[postal-code]")
    .replace(/\b(?:deliver(?:y|ed)? to|shipping address|billing address)\b[^,;\r\n]{0,160}/gi, "[address]")
    .replace(/\b\d{1,6}\s+(?:[A-Z0-9.'-]+\s+){1,6}(?:street|st|road|rd|avenue|ave|boulevard|blvd|drive|dr|lane|ln|court|ct|way|crescent|cres|place|pl|terrace|trail|circle)\b\.?/gi, "[address]");
  const sanitizeAccountingUrl = (value) => {
    try {
      const parsed = new URL(String(value || ""));
      if (!/^https?:$/.test(parsed.protocol)) return "";
      const allowed = new Set(["mod", "modctx", "ps", "orderID"]);
      for (const key of [...parsed.searchParams.keys()]) {
        if (!allowed.has(key)) parsed.searchParams.delete(key);
      }
      parsed.username = "";
      parsed.password = "";
      parsed.hash = "";
      return parsed.toString();
    } catch {
      return "";
    }
  };
`;
