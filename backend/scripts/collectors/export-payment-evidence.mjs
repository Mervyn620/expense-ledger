import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import {pathToFileURL} from "node:url";
import {canonicalPaymentMethod, isResolvedPaymentEvidence} from "./payment-text-utils.mjs";
import { connectCdp, fetchJson, listTabs, sleep } from "../browser/cdp-client.mjs";
import {
  CollectionSessionError,
  runWithCollectionPolicy,
} from "../browser/collection-policy.mjs";
import { atomicWriteFile } from "../lib/atomic-file.mjs";
import { parseCsv } from "./csv.mjs";

function parseArgs(argv) {
  const args = {
    port: 9223,
    out: "data/payment_evidence.json",
    minDate: "2026-05-01",
    platforms: "walmart_ca,fantuan",
    waitMs: 3500,
    maxOrders: 20,
    refreshAll: false,
    orderIds: [],
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--port") args.port = Number(argv[++i]);
    else if (arg === "--out") args.out = argv[++i] ?? args.out;
    else if (arg === "--min-date") args.minDate = argv[++i] ?? args.minDate;
    else if (arg === "--platforms") args.platforms = argv[++i] ?? args.platforms;
    else if (arg === "--wait-ms") args.waitMs = Number(argv[++i]);
    else if (arg === "--max-orders") args.maxOrders = Number(argv[++i]);
    else if (arg === "--refresh-all") args.refreshAll = true;
    else if (arg === "--order-id") args.orderIds.push(argv[++i]);
  }

  args.platforms = new Set(args.platforms.split(",").map((value) => value.trim()).filter(Boolean));
  return args;
}

function evidenceKey(row) {
  return `${row.platform || ""}|${row.order_id || ""}`;
}

function hasStableEvidence(row) {
    return isResolvedPaymentEvidence(row);
}

async function readExisting(outputPath) {
  try {
    const payload = JSON.parse(await readFile(outputPath, "utf8"));
    return Array.isArray(payload.evidence) ? payload.evidence : [];
  } catch {
    return [];
  }
}

async function readTransactions(minDate, platforms, existing, refreshAll, maxOrders, orderIds = []) {
  const completed = new Set(existing.filter(hasStableEvidence).map(evidenceKey));
  if (platforms.has("tnt_ca")) {
    try {
      const cache = JSON.parse(await readFile("data/tnt_orders.json", "utf8"));
      if (cache.contract !== "expense-tnt-orders/v1" || cache.platform !== "tnt_ca") throw new Error("Invalid T&T payment cache contract.");
      for (const order of cache.orders || []) {
        if (!order.excluded && isResolvedPaymentEvidence(order.payment)) completed.add(`tnt_ca|${order.order_id}`);
      }
    } catch (error) { if (error.code !== "ENOENT") throw error; }
  }
  const text = await readFile("data/transactions.csv", "utf8");
  return parseCsv(text)
    .filter((row) => platforms.has(row.platform) && row.order_id && (!row.date || row.date >= minDate))
    .filter((row) => !orderIds.length || orderIds.includes(row.order_id))
    .filter((row) => refreshAll || !completed.has(evidenceKey(row)))
    .map((row) => ({
      date: row.date || "",
      platform: row.platform || "",
      order_id: row.order_id || "",
      merchant: row.merchant || "",
      total: row.total || "",
      currency: row.currency || "CAD",
      source_url: row.source_url || "",
    }))
    .slice(0, maxOrders);
}

function platformUrl(order) {
  if (order.platform === "walmart_ca") {
    return `https://www.walmart.ca/default/orders/${encodeURIComponent(order.order_id)}`;
  }
  if (order.platform === "fantuan") {
    return order.source_url || `https://www.fantuanorder.com/zh-CN/order-detail/${encodeURIComponent(order.order_id)}`;
  }
  if (order.platform === "tnt_ca") return `https://www.tntsupermarket.com/eng/sales/order/view/order_id/${encodeURIComponent(order.order_id)}`;
  throw new Error("Unsupported payment evidence platform.");
}

function hostPattern(platform) {
  if (platform === "walmart_ca") return /^https:\/\/www\.walmart\.ca\//;
  if (platform === "fantuan") return /^https:\/\/www\.fantuanorder\.com\//;
  if (platform === "tnt_ca") return /^https:\/\/www\.tntsupermarket\.com\//;
  throw new Error("Unsupported payment evidence platform.");
}

async function findOrOpenPage(port, platform, url) {
  const tabs = await listTabs(port);
  const pages = tabs.filter((tab) => tab.type === "page" && tab.webSocketDebuggerUrl);
  const existing = pages.find((tab) => hostPattern(platform).test(tab.url));
  if (existing) return existing;
  return fetchJson(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(url)}`, { method: "PUT" });
}

async function navigate(cdp, policy, url, waitMs) {
  await policy.pace();
  await policy.retry(
    "navigate payment page",
    () => cdp.call("Page.navigate", { url }),
  );
  await policy.settle(Math.max(1, waitMs / 1800));
  for (let i = 0; i < 14; i += 1) {
    const result = await cdp.call("Runtime.evaluate", {
      expression: "JSON.stringify({ readyState: document.readyState, bodyLength: document.body?.innerText?.length || 0 })",
      returnByValue: true,
    });
    const state = JSON.parse(result.result?.value || "{}");
    if (state.readyState === "complete" && state.bodyLength > 100) {
      await policy.inspectPage(cdp);
      return;
    }
    await sleep(500);
  }
}

async function evaluatePayment(cdp, policy, order) {
  if (order.platform === "fantuan") {
    await ensureFantuanPaymentExpanded(cdp, policy);
  }

  const extraction = `JSON.stringify((${pageExtractor.toString()})(${JSON.stringify(order)}, ${canonicalPaymentMethod.toString()}))`;
  const result = await cdp.call("Runtime.evaluate", {
    expression: extraction,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    return {
      ...baseEvidence(order),
      status: "error",
      evidence_snippet: String(result.exceptionDetails.exception?.description || "page extraction error").slice(0, 180),
    };
  }
  const evidence = JSON.parse(result.result?.value || "{}");
  if (evidence.status === "login_required") {
    await policy.recordLoginRequired(`${order.platform} payment page requires login.`);
    throw new CollectionSessionError(
      `${order.platform} login is required in the dedicated browser.`,
    );
  }
  return evidence;
}

async function ensureFantuanPaymentExpanded(cdp, policy) {
  const expression = `JSON.stringify((() => {
    const clean = (value) => String(value || "").replace(/\\s+/g, " ").trim();
    const paymentSelector = "#root > div > div > div._id__orderDetail__jpsUo > div._id__orderDetailContentBox__Uxre3 > div > div > div.aggregate-order-info_deliveryInfo__maBRN > div.aggregate-order-info_deliveryInfoContent__roAaw > div:nth-child(3) > div.aggregate-order-info_nameStr___oeOm > p";
    const arrowSelector = "#root > div > div > div._id__orderDetail__jpsUo > div._id__orderDetailContentBox__Uxre3 > div > div > div.aggregate-order-info_deliveryInfo__maBRN > div.aggregate-order-info_zipArrow__BJoUR";
    const existing = clean(document.querySelector(paymentSelector)?.innerText || document.querySelector(paymentSelector)?.textContent || "");
    if (existing) return { expanded: true, clicked: false, paymentText: existing };
    const arrow = document.querySelector(arrowSelector) || document.querySelector("[class*='aggregate-order-info_zipArrow']");
    if (!arrow) return { expanded: false, clicked: false, reason: "arrow_not_found" };
    arrow.click();
    return { expanded: false, clicked: true };
  })())`;

  await policy.pace();
  const first = await cdp.call("Runtime.evaluate", {
    expression,
    returnByValue: true,
  });
  const state = JSON.parse(first.result?.value || "{}");
  if (state.paymentText || !state.clicked) return state;
  await policy.settle(0.75);
  return state;
}

function baseEvidence(order) {
  return {
    date: order.date,
    platform: order.platform,
    order_id: order.order_id,
    merchant: order.merchant,
    total: order.total,
    currency: order.currency,
  };
}

function pageExtractor(order, parseMethod = canonicalPaymentMethod) {
  const clean = (value) => String(value || "")
    .replace(/\u00a0/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  const redact = (value) => clean(value)
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, "[email]")
    .replace(/\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b/g, "[phone]")
    .replace(/\b(?:\d[ -]*?){13,19}\b/g, "[card]")
    .replace(/\b\d{1,6}\s+[A-Za-z0-9.' -]+\s+(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Cres|Crescent|Blvd|Boulevard|Way|Lane|Ln)\b[^,\n]*/gi, "[address]");

  const networkFromText = (text) => {
    if (/master\s*card|mastercard/i.test(text)) return "Mastercard";
    if (/visa/i.test(text)) return "Visa";
    if (/amex|american express/i.test(text)) return "Amex";
    return "";
  };

  const cardFromText = (text) => {
    const patterns = [
      /ending\s+in\s+(\d{4})/i,
      /(?:last\s*4|last four)[^0-9]{0,30}(\d{4})/i,
      /\u5c3e\u53f7[^0-9]{0,30}(\d{4})/i,
      /(?:\*{2,}|\u2022{2,}|x{2,})[^0-9]{0,20}(\d{4})/i,
    ];
    for (const pattern of patterns) {
      const match = text.match(pattern);
      if (match) return match[1];
    }
    return "";
  };

  const bodyText = clean(document.body?.innerText || "");
  const loginLikely = /sign in|log in|\u767b\u5f55|\u767b\u5165|auth/i.test(document.title + " " + bodyText.slice(0, 600));

  if (order.platform === "walmart_ca") {
    const selectors = [
      ".bill-order-payment-cards",
      ".bill-order-payment-info",
      "[class*='bill-order-payment']",
    ];
    let paymentText = "";
    for (const selector of selectors) {
      const node = document.querySelector(selector);
      const text = clean(node?.innerText || node?.textContent || "");
      if (/payment|ending|card/i.test(text)) {
        paymentText = text;
        break;
      }
    }
    if (!paymentText) {
      const match = bodyText.match(/Payment method\s+(.{0,120}?)(?:Subtotal|Taxes|Total|Temporary hold|$)/i);
      paymentText = match ? match[0] : "";
    }

    const safeText = redact(paymentText);
    const last4 = cardFromText(safeText);
    const method = parseMethod(safeText.replace(/^payment method\s*[:：]?\s*/i, ""));
    return {
      date: order.date,
      platform: order.platform,
      order_id: order.order_id,
      merchant: order.merchant,
      total: order.total,
      currency: order.currency,
      page_url: location.href,
      payment_method: last4 ? "card" : method,
      card_network: networkFromText(safeText),
      card_last4: last4,
      status: last4 ? "confirmed" : method ? "method_only" : (loginLikely ? "login_required" : "not_found"),
      evidence_snippet: safeText.slice(0, 220),
    };
  }

  if (order.platform === "tnt_ca") {
    const expected = `/eng/sales/order/view/order_id/${encodeURIComponent(order.order_id)}`;
    if (location.pathname.replace(/\/$/, "") !== expected) {
      return { ...order, page_url: "", payment_method: "", card_network: "", card_last4: "", status: loginLikely ? "login_required" : "not_found", evidence_snippet: "Order detail identity could not be verified." };
    }
    const lines = String(document.body?.innerText || "").split(/\r?\n/).map(clean).filter(Boolean);
    const index = lines.findIndex(line => /^(?:Payment\s+Method|支付方式|付款方式|付款資訊|付款信息)[:：]?$/i.test(line));
    const paymentText = index < 0 ? "" : clean(lines[index + 1]);
    const safeText = redact(paymentText);
    const last4 = cardFromText(safeText);
    const network = networkFromText(safeText);
    const wallet = /^(?:wechat\s*pay|微信(?:支付)?|微信錢包|微信钱包)$/i.test(safeText) ? "WeChat Pay"
      : /^(?:alipay|支付宝(?:支付)?|支付寶(?:支付)?)$/i.test(safeText) ? "Alipay" : "";
    const method = wallet || parseMethod(safeText) || (last4 || /credit|debit|card|信用|借记|借記|visa|mastercard/i.test(safeText) ? "card" : "");
    return {date:order.date,platform:order.platform,order_id:order.order_id,merchant:order.merchant,total:order.total,currency:order.currency,
      page_url:location.origin+expected,payment_method:method,card_network:network,card_last4:last4,
      status:last4?"confirmed":method?"method_only":loginLikely?"login_required":"not_found",
      evidence_snippet:method?"Payment Method: "+(last4?`${network||'card'} ending ${last4}`:method):"No explicit supported payment method found."};
  }

  if (order.platform === "fantuan") {
    const exactPaymentSelector = "#root > div > div > div._id__orderDetail__jpsUo > div._id__orderDetailContentBox__Uxre3 > div > div > div.aggregate-order-info_deliveryInfo__maBRN > div.aggregate-order-info_deliveryInfoContent__roAaw > div:nth-child(3) > div.aggregate-order-info_nameStr___oeOm > p";
    const exactPayment = clean(
      document.querySelector(exactPaymentSelector)?.innerText
      || document.querySelector(exactPaymentSelector)?.textContent
      || "",
    );
    const lines = String(document.body?.innerText || "")
      .split(/\r?\n/)
      .map(clean)
      .filter(Boolean);
    const paymentLabel = "\u652f\u4ed8\u65b9\u5f0f";
    const labelIndex = lines.findIndex((line) => line === paymentLabel || /^\u652f\u4ed8\u65b9\u5f0f[:\uff1a]?$/.test(line));
    const inlineLine = labelIndex < 0
      ? lines.find((line) => line.startsWith(paymentLabel) && !line.includes("/") && line.length <= 40)
      : "";
    const inlineMethod = inlineLine ? clean(inlineLine.replace(paymentLabel, "").replace(/^[:\uff1a]/, "")) : "";
    const paymentLines = exactPayment
      ? [paymentLabel, exactPayment]
      : (labelIndex >= 0 ? lines.slice(labelIndex, labelIndex + 3) : (inlineLine ? [inlineLine] : []));
    const paymentText = paymentLines.join(" ");
    const safeText = redact(paymentText);
    const last4 = cardFromText(safeText);
    const method = exactPayment || (labelIndex >= 0 ? clean(lines[labelIndex + 1] || "") : inlineMethod);
    return {
      date: order.date,
      platform: order.platform,
      order_id: order.order_id,
      merchant: order.merchant,
      total: order.total,
      currency: order.currency,
      page_url: location.href,
      payment_method: method || (last4 ? "card" : ""),
      card_network: networkFromText(safeText),
      card_last4: last4,
      status: last4 ? "confirmed" : (method ? "method_only" : (loginLikely ? "login_required" : "not_found")),
      evidence_snippet: safeText.slice(0, 220),
    };
  }

  return {
    date: order.date,
    platform: order.platform,
    order_id: order.order_id,
    merchant: order.merchant,
    total: order.total,
    currency: order.currency,
    page_url: location.href,
    payment_method: "",
    card_network: "",
    card_last4: "",
    status: "unsupported_platform",
    evidence_snippet: "",
  };
}

async function collectPlatform(port, platform, orders, waitMs, policy) {
  if (!orders.length) return [];
  const firstUrl = platformUrl(orders[0]);
  const page = await policy.retry(
    `find ${platform} tab`,
    () => findOrOpenPage(port, platform, firstUrl),
  );
  const cdp = await policy.retry(
    `connect ${platform} tab`,
    () => connectCdp(page.webSocketDebuggerUrl, {
      callTimeoutMs: policy.config.cdp_call_timeout_ms,
    }),
  );
  try {
    await cdp.call("Page.enable");

    const results = [];
    for (const order of orders) {
      const url = platformUrl(order);
      await navigate(cdp, policy, url, waitMs);
      results.push(await evaluatePayment(cdp, policy, order));
    }

    return results;
  } finally {
    cdp.close();
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const existing = await readExisting(args.out);
  const transactions = await readTransactions(
    args.minDate,
    args.platforms,
    existing,
    args.refreshAll,
    args.maxOrders,
    args.orderIds,
  );
  if (!transactions.length) {
    console.log(`No missing payment evidence found since ${args.minDate}. cached=${existing.length}`);
    return;
  }
  const byPlatform = new Map();
  for (const tx of transactions) {
    if (!byPlatform.has(tx.platform)) byPlatform.set(tx.platform, []);
    byPlatform.get(tx.platform).push(tx);
  }

  const evidence = [];
  for (const [platform, orders] of byPlatform.entries()) {
    const rows = await runWithCollectionPolicy(
      platform,
      (policy) => collectPlatform(args.port, platform, orders, args.waitMs, policy),
      {
        summary: (result) => ({ requested: orders.length, collected: result.length }),
      },
    );
    evidence.push(...rows);
  }

  const merged = new Map();
  for (const row of existing) merged.set(evidenceKey(row), row);
  for (const row of evidence) {
    const key = evidenceKey(row);
    const previous = merged.get(key);
    if (previous && hasStableEvidence(previous) && !hasStableEvidence(row)) continue;
    merged.set(key, row);
  }
  const allEvidence = [...merged.values()].filter((row) => row.platform && row.order_id);
  const payload = {
    generated_at: new Date().toISOString(),
    min_date: args.minDate,
    evidence: allEvidence,
  };
  await mkdir(path.dirname(args.out), { recursive: true });
  await atomicWriteFile(args.out, `${JSON.stringify(payload, null, 2)}\n`);

  const confirmed = allEvidence.filter((row) => row.card_last4).length;
  const methodOnly = allEvidence.filter((row) => row.status === "method_only").length;
  const blocked = allEvidence.filter((row) => row.status === "login_required" || row.status === "error").length;
  console.log(`Fetched ${evidence.length} payment evidence row(s); cached=${allEvidence.length} cards_found=${confirmed} method_only=${methodOnly} blocked_or_failed=${blocked}`);
}

export {pageExtractor, hostPattern, hasStableEvidence};
if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
