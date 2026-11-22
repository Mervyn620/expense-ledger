import { createHash } from "node:crypto";

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
  janvier: 1,
  fevrier: 2,
  mars: 3,
  avril: 4,
  mai: 5,
  juin: 6,
  juillet: 7,
  aout: 8,
  septembre: 9,
  octobre: 10,
  novembre: 11,
  decembre: 12,
});

export function cleanText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

export function parseMoney(value) {
  const text = String(value ?? "").replace(/\u00a0/g, " ");
  const match = text.match(/-?\s*(?:CAD|C\$|\$)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/i);
  if (!match) return 0;
  const amount = Number(match[1].replace(/,/g, ""));
  if (!Number.isFinite(amount)) return 0;
  return /-\s*(?:CAD|C\$|\$)?\s*[0-9]/i.test(text) ? -amount : amount;
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

export function parseAmazonDate(value) {
  const text = cleanText(value)
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase();

  const monthFirst = text.match(
    /\b([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b/i,
  );
  if (monthFirst) {
    const month = MONTHS[monthFirst[1]];
    if (month) return isoDate(Number(monthFirst[3]), month, Number(monthFirst[2]));
  }

  const dayFirst = text.match(
    /\b(\d{1,2})(?:er|st|nd|rd|th)?\s+([a-z]+)\s+(20\d{2})\b/i,
  );
  if (dayFirst) {
    const month = MONTHS[dayFirst[2]];
    if (month) return isoDate(Number(dayFirst[3]), month, Number(dayFirst[1]));
  }

  const numeric = text.match(/\b(20\d{2})-(\d{2})-(\d{2})\b/);
  return numeric
    ? isoDate(Number(numeric[1]), Number(numeric[2]), Number(numeric[3]))
    : "";
}

export function extractAmazonOrderId(value) {
  return String(value || "").match(/\b\d{3}-\d{7}-\d{7}\b/)?.[0] || "";
}

export function normalizeFulfillment(status) {
  const value = cleanText(status).toLowerCase();
  if (/cancelled|canceled|annulee?|annule/.test(value)) return "cancelled";
  if (/delivered|livree?|livre/.test(value)) return "delivered";
  if (/not yet shipped|arriv|preparing|processing/.test(value)) return "pending";
  if (/shipped|expediee?|expedie/.test(value)) return "shipped";
  return "";
}

export function reconcileAmounts({
  total,
  subtotal,
  tax,
  shipping,
  promotions,
} = {}) {
  const normalizedTotal = Math.max(0, Number(total) || 0);
  const normalizedTax = Math.max(0, Number(tax) || 0);
  const normalizedShipping = Math.max(0, Number(shipping) || 0);
  const normalizedPromotions = Math.max(0, Number(promotions) || 0);
  let normalizedSubtotal = Math.max(0, Number(subtotal) || 0);

  if (!normalizedSubtotal && normalizedTotal) {
    normalizedSubtotal = Math.max(
      0,
      normalizedTotal - normalizedTax - normalizedShipping + normalizedPromotions,
    );
  }

  const fees = normalizedTotal - normalizedSubtotal - normalizedTax;
  return {
    total: Number(normalizedTotal.toFixed(2)),
    subtotal: Number(normalizedSubtotal.toFixed(2)),
    tax: Number(normalizedTax.toFixed(2)),
    fees: Number(fees.toFixed(2)),
  };
}

export function mergeAmazonOrders(existing, incoming) {
  const byId = new Map();
  for (const order of [...(existing || []), ...(incoming || [])]) {
    const orderId = extractAmazonOrderId(order?.order_id);
    if (!orderId) continue;
    byId.set(orderId, { ...order, order_id: orderId });
  }
  return [...byId.values()].sort((left, right) => (
    `${right.date || ""}|${right.order_id}`.localeCompare(
      `${left.date || ""}|${left.order_id}`,
    )
  ));
}

export function amazonCardFingerprint(card) {
  return createHash("sha256").update(JSON.stringify({
    id: card.order_id, date: card.date, total: Number(card.total),
    status: cleanText(card.status),
    items: (card.items || []).map(item => [item.asin, cleanText(item.title), Number(item.quantity)])
      .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))),
  })).digest("hex");
}

export function canReuseAmazonDetail(card, cached, { now = Date.now(), refreshAll = false } = {}) {
  if (refreshAll || !cached || cached.detail_cache?.version !== 1) return false;
  const age = now - Date.parse(cached.detail_cache.checked_at);
  // Recheck at least daily, including delivered orders whose list card does
  // not expose a later refund. Never extend this deadline on a cache hit.
  if (!Number.isFinite(age) || age < 0 || age >= 86400000) return false;
  const status = cleanText(card.status).normalize("NFD").replace(/\p{Diacritic}/gu, "");
  if (!/delivered|livre/i.test(status) || /refund|return|cancel|annul|rembours|retour|arriv|pending|not yet|shipped|expedi/i.test(status)) return false;
  if (!card.date || !(Number(card.total) > 0) || !card.items?.length) return false;
  if (!cached.items?.length || !/^\d{4}$/.test(cached.payment?.card_last4 || "")) return false;
  if (!cached.items.every(item => item.asin && item.title && Number(item.line_total) > 0)) return false;
  const sum = cached.items.reduce((total, item) => total + Number(item.line_total), 0);
  if (Math.abs(sum - Number(cached.item_total)) > 0.02 || !(Number(cached.item_total) > 0)) return false;
  return cached.detail_cache.fingerprint === amazonCardFingerprint(card);
}

export async function collectAmazonDetails(cards, existing, collect, options = {}) {
  const byId = new Map(existing.map(order => [order.order_id, order]));
  const collected = [];
  let reused = 0;
  for (const card of cards) {
    if (canReuseAmazonDetail(card, byId.get(card.order_id), options)) {
      reused += 1;
      continue;
    }
    const order = await collect(card);
    if (order) collected.push(order);
  }
  return { collected, reused };
}
