const MONTHS = {
  jan: "01", feb: "02", mar: "03", apr: "04", may: "05", jun: "06",
  jul: "07", aug: "08", sep: "09", oct: "10", nov: "11", dec: "12",
};

function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

export function isWalmartDocumentReady({ readyState = "", bodyLength = 0 } = {}) {
  return ["interactive", "complete"].includes(String(readyState)) &&
    Number(bodyLength) > 100;
}

export function walmartPageSignature(page) {
  return JSON.stringify((page?.candidates || []).map(candidate => [
    walmartCandidateKey(candidate), candidate.date, candidate.total,
  ]));
}

export function walmartFullScanDue(lastFullScan, now = Date.now()) {
  const age = now - Date.parse(lastFullScan);
  return !Number.isFinite(age) || age < 0 || age >= 7 * 86400000;
}

export function createWalmartHistoryBoundary({ minDate, knownOrders = [], backfillIds = [], fullHistory = false }) {
  const outstanding = new Set([
    ...knownOrders.filter(order => !order.date || order.date >= minDate).map(order => order.orderId),
    ...backfillIds,
  ]);
  const seen = new Set();
  let lastDate = "9999-12-31";
  let uncertain = fullHistory;
  let oldPages = 0;
  return page => {
    let allOld = Boolean(page?.candidates?.length);
    for (const candidate of page?.candidates || []) {
      if (!candidate.orderId || !/^\d{4}-\d{2}-\d{2}$/.test(candidate.date || "")) uncertain = true;
      outstanding.delete(candidate.orderId);
      if (candidate.date >= minDate || !candidate.date) allOld = false;
      const key = walmartCandidateKey(candidate);
      if (seen.has(key)) continue;
      seen.add(key);
      // Delivery dates can differ from order dates. Any observed disorder
      // disables the shortcut for this run; periodic full scans cover unseen
      // changes to the site's ordering.
      if (candidate.date > lastDate) uncertain = true;
      lastDate = candidate.date;
    }
    oldPages = allOld ? oldPages + 1 : 0;
    return !uncertain && outstanding.size === 0 && oldPages >= 2;
  };
}

export function canonicalizeWalmartProductUrl(value) {
  try {
    const parsed = new URL(String(value || ""), "https://www.walmart.ca/");
    const hostname = parsed.hostname.toLowerCase();
    if (!["walmart.ca", "www.walmart.ca"].includes(hostname)) return "";
    if (!parsed.pathname.toLowerCase().includes("/ip/")) return "";
    parsed.protocol = "https:";
    parsed.hostname = "www.walmart.ca";
    parsed.port = "";
    parsed.username = "";
    parsed.password = "";
    parsed.search = "";
    parsed.hash = "";
    parsed.pathname = parsed.pathname.replace(/\/{2,}/g, "/").replace(/\/$/, "");
    return parsed.toString();
  } catch {
    return "";
  }
}

export function parseWalmartOrderDate(value, now = new Date()) {
  const match = clean(value).match(
    /(?:Delivered on|arriving(?: late)?(?: on)?)\s+([A-Za-z]{3})\s+(\d{1,2})/i,
  );
  if (!match) return "";
  const month = MONTHS[match[1].toLowerCase()];
  if (!month) return "";
  let year = now.getFullYear();
  const candidate = new Date(year, Number(month) - 1, Number(match[2]));
  if (candidate.getTime() > now.getTime() + 35 * 86400000) year -= 1;
  return `${year}-${month}-${String(Number(match[2])).padStart(2, "0")}`;
}

export function parseWalmartOrderCandidate({ text = "", hrefs = [], aria = "" }, now = new Date()) {
  const normalized = clean(text);
  const orderId = hrefs
    .map((href) => String(href || "").match(/\/orders\/(\d{8,})/i)?.[1])
    .find(Boolean) || "";
  const total = normalized.match(/Order total\s*\$([0-9]+(?:\.[0-9]{2})?)/i)?.[1] || "";
  return {
    orderId,
    aria: clean(aria),
    date: parseWalmartOrderDate(`${aria} ${normalized}`, now),
    total,
  };
}

export function walmartCandidateKey(candidate) {
  if (candidate?.orderId) return `order:${candidate.orderId}`;
  return [
    "card",
    clean(candidate?.date),
    clean(candidate?.total),
    clean(candidate?.aria),
  ].join(":");
}

export function resolveWalmartBackfillOrderId(
  candidate,
  knownOrders,
  excludedOrderIds = new Set(),
) {
  if (candidate?.orderId) return String(candidate.orderId);
  const candidateDate = Date.parse(`${clean(candidate?.date)}T00:00:00Z`);
  const candidateTotal = Number(candidate?.total);
  if (!Number.isFinite(candidateDate) || !Number.isFinite(candidateTotal)) return "";
  const matches = (knownOrders || []).filter((order) => {
    if (!order?.orderId || excludedOrderIds.has(String(order.orderId))) return false;
    const orderDate = Date.parse(`${clean(order.date)}T00:00:00Z`);
    const orderTotal = Number(order.total);
    return Number.isFinite(orderDate) &&
      Number.isFinite(orderTotal) &&
      Math.abs(orderDate - candidateDate) <= 86400000 &&
      Math.abs(orderTotal - candidateTotal) <= 0.01;
  });
  return matches.length === 1 ? String(matches[0].orderId) : "";
}

export function isCancelledWalmartSnapshot(snapshot) {
  return (snapshot?.lines || []).some((line) =>
    /^cancel(?:led|ed)$/i.test(clean(line))
  );
}

export const walmartOrderParserSource = String.raw`
  const walmartMonths = {
    jan: "01", feb: "02", mar: "03", apr: "04", may: "05", jun: "06",
    jul: "07", aug: "08", sep: "09", oct: "10", nov: "11", dec: "12",
  };
  const cleanWalmartText = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const parseWalmartOrderDate = (value, now = new Date()) => {
    const match = cleanWalmartText(value).match(/(?:Delivered on|arriving(?: late)?(?: on)?)\s+([A-Za-z]{3})\s+(\d{1,2})/i);
    if (!match) return "";
    const month = walmartMonths[match[1].toLowerCase()];
    if (!month) return "";
    let year = now.getFullYear();
    const candidate = new Date(year, Number(month) - 1, Number(match[2]));
    if (candidate.getTime() > now.getTime() + 35 * 86400000) year -= 1;
    return year + "-" + month + "-" + String(Number(match[2])).padStart(2, "0");
  };
  const parseWalmartOrderCandidate = ({ text = "", hrefs = [], aria = "" }, now = new Date()) => {
    const normalized = cleanWalmartText(text);
    const orderId = hrefs.map((href) => String(href || "").match(/\/orders\/(\d{8,})/i)?.[1]).find(Boolean) || "";
    const total = normalized.match(/Order total\s*\$([0-9]+(?:\.[0-9]{2})?)/i)?.[1] || "";
    return {
      orderId,
      aria: cleanWalmartText(aria),
      date: parseWalmartOrderDate(aria + " " + normalized, now),
      total,
    };
  };
`;

export const walmartProductParserSource = String.raw`
  const canonicalizeWalmartProductUrl = (value) => {
    try {
      const parsed = new URL(String(value || ""), "https://www.walmart.ca/");
      const hostname = parsed.hostname.toLowerCase();
      if (!["walmart.ca", "www.walmart.ca"].includes(hostname)) return "";
      if (!parsed.pathname.toLowerCase().includes("/ip/")) return "";
      parsed.protocol = "https:";
      parsed.hostname = "www.walmart.ca";
      parsed.port = "";
      parsed.username = "";
      parsed.password = "";
      parsed.search = "";
      parsed.hash = "";
      parsed.pathname = parsed.pathname.replace(/\/{2,}/g, "/").replace(/\/$/, "");
      return parsed.toString();
    } catch {
      return "";
    }
  };
`;

export function walmartOrderNeedsRefresh(candidate, knownOrder) {
  if (!knownOrder) return false;
  if (!/^(delivered|completed|cancelled|canceled|refunded)$/i.test(knownOrder.fulfillment || "")) return true;
  const liveAmount = Number(candidate?.total);
  const previousAmount = Number(knownOrder.total);
  return Number.isFinite(liveAmount) && liveAmount > 0 && Number.isFinite(previousAmount)
    && Math.abs(liveAmount - previousAmount) > 0.02;
}
