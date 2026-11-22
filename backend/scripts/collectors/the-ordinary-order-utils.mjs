const MONTHS = Object.freeze({
  january: 1,
  february: 2,
  march: 3,
  april: 4,
  may: 5,
  june: 6,
  july: 7,
  august: 8,
  september: 9,
  october: 10,
  november: 11,
  december: 12,
});

export function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function isoDate(year, month, day) {
  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    date.getUTCFullYear() !== year
    || date.getUTCMonth() !== month - 1
    || date.getUTCDate() !== day
  ) {
    return "";
  }
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

export function parseOrdinaryDate(value) {
  const text = cleanText(value).toLowerCase();
  const monthFirst = text.match(
    /\b([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b/i,
  );
  if (monthFirst && MONTHS[monthFirst[1]]) {
    return isoDate(
      Number(monthFirst[3]),
      MONTHS[monthFirst[1]],
      Number(monthFirst[2]),
    );
  }

  const numeric = text.match(/\b(20\d{2})[-/]([01]?\d)[-/]([0-3]?\d)\b/);
  if (numeric) {
    return isoDate(Number(numeric[1]), Number(numeric[2]), Number(numeric[3]));
  }

  return "";
}

export function parseMoney(value) {
  const text = String(value ?? "").replace(/\u00a0/g, " ");
  const match = text.match(/(?:CAD|C\$|\$)?\s*(-?)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/i);
  if (!match) return 0;
  const amount = Number(match[2].replace(/,/g, ""));
  if (!Number.isFinite(amount)) return 0;
  return match[1] === "-" || /-\s*(?:CAD|C\$|\$)?\s*[0-9]/i.test(text)
    ? -amount
    : amount;
}

export function extractOrdinaryOrderId(value) {
  const candidate = cleanText(value);
  return /^[A-Za-z0-9][A-Za-z0-9-]{3,39}$/.test(candidate) ? candidate : "";
}

export function normalizeFulfillment(value) {
  const status = cleanText(value).toLowerCase();
  if (/cancelled|canceled/.test(status)) return "cancelled";
  if (/refund|returned/.test(status)) return "refunded";
  if (/delivered/.test(status)) return "delivered";
  if (/shipped|in transit|on the way/.test(status)) return "shipped";
  if (/processing|confirmed|pending|preparing/.test(status)) return "pending";
  return "";
}

export function normalizeProductUrl(value) {
  try {
    const url = new URL(String(value || ""), "https://theordinary.com");
    if (!/(^|\.)theordinary\.com$/i.test(url.hostname)) return "";
    url.protocol = "https:";
    url.hostname = "theordinary.com";
    url.search = "";
    url.hash = "";
    return url.href;
  } catch {
    return "";
  }
}

export function reconcileAmounts({ total, subtotal, tax } = {}) {
  const normalizedTotal = Math.max(0, Number(total) || 0);
  const normalizedTax = Math.max(0, Number(tax) || 0);
  let normalizedSubtotal = Math.max(0, Number(subtotal) || 0);
  if (!normalizedSubtotal && normalizedTotal) {
    normalizedSubtotal = Math.max(0, normalizedTotal - normalizedTax);
  }
  const fees = Number((normalizedTotal - normalizedSubtotal - normalizedTax).toFixed(2));
  return {
    total: Number(normalizedTotal.toFixed(2)),
    subtotal: Number(normalizedSubtotal.toFixed(2)),
    tax: Number(normalizedTax.toFixed(2)),
    fees: Object.is(fees, -0) ? 0 : fees,
  };
}

export function mergeOrdinaryOrders(existing, incoming) {
  const byId = new Map();
  for (const order of [...(existing || []), ...(incoming || [])]) {
    const orderId = extractOrdinaryOrderId(order?.order_id);
    if (!orderId) continue;
    byId.set(orderId, { ...order, order_id: orderId });
  }
  return [...byId.values()].sort((left, right) => (
    `${right.date || ""}|${right.order_id}`.localeCompare(
      `${left.date || ""}|${left.order_id}`,
    )
  ));
}
