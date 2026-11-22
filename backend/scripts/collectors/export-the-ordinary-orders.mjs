import { readFile } from "node:fs/promises";
import path from "node:path";
import { connectCdp, listTabs, sleep } from "../browser/cdp-client.mjs";
import {
  CollectionSessionError,
  observedDates,
  runWithCollectionPolicy,
} from "../browser/collection-policy.mjs";
import { atomicWriteFile } from "../lib/atomic-file.mjs";
import {
  cleanText,
  extractOrdinaryOrderId,
  mergeOrdinaryOrders,
  normalizeFulfillment,
  normalizeProductUrl,
  parseMoney,
  parseOrdinaryDate,
  reconcileAmounts,
} from "./the-ordinary-order-utils.mjs";
import {
  classifyOrdinaryEntryState,
  ordinaryAuthFactsExpression,
  ordinaryGoogleClickExpression,
  runOrdinaryDetailWithRecovery,
  runOrdinaryOAuthRecovery,
} from "./the-ordinary-auth.mjs";
import {
  isOrdinaryLoginTabUrl,
  isOrdinaryOrdersTabUrl,
  ordinaryOrdersTabScore,
} from "./the-ordinary-tab-selection.mjs";

const PLATFORM = "the_ordinary_ca";
const ORDERS_URL = "https://theordinary.com/en-ca/orders";
const DETAIL_PATH = "/on/demandware.store/Sites-deciem-Site/en_CA/Order-DetailsDrawer";

function parseArgs(argv) {
  const args = {
    port: 9223,
    out: "data/the_ordinary_orders.json",
    evidenceOut: "data/payment_evidence.json",
    minDate: "2026-05-01",
    maxOrders: 12,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--port") args.port = Number(argv[++index]);
    else if (arg === "--out") args.out = argv[++index] ?? args.out;
    else if (arg === "--evidence-out") args.evidenceOut = argv[++index] ?? args.evidenceOut;
    else if (arg === "--min-date") args.minDate = argv[++index] ?? args.minDate;
    else if (arg === "--max-orders") args.maxOrders = Number(argv[++index]);
  }
  if (!Number.isInteger(args.port) || args.port < 1 || args.port > 65535) {
    throw new Error("--port must be a valid TCP port.");
  }
  if (!/^20\d{2}-\d{2}-\d{2}$/.test(args.minDate)) {
    throw new Error("--min-date must use YYYY-MM-DD.");
  }
  if (!Number.isInteger(args.maxOrders) || args.maxOrders < 1 || args.maxOrders > 100) {
    throw new Error("--max-orders must be from 1 to 100.");
  }
  return args;
}

async function readJson(file, fallback) {
  try {
    return JSON.parse(await readFile(file, "utf8"));
  } catch {
    return fallback;
  }
}

async function evaluateJson(cdp, expression, { awaitPromise = false } = {}) {
  const result = await cdp.call("Runtime.evaluate", {
    expression,
    awaitPromise,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(
      result.exceptionDetails.exception?.description
      || result.exceptionDetails.text
      || "The Ordinary page evaluation failed.",
    );
  }
  const value = result.result?.value;
  if (typeof value === "string") return JSON.parse(value || "{}");
  return value && typeof value === "object" ? value : {};
}

async function openOrdersTab(port) {
  const response = await fetch(
    `http://127.0.0.1:${port}/json/new?${encodeURIComponent(ORDERS_URL)}`,
    { method: "PUT" },
  );
  if (!response.ok) throw new Error("Could not open The Ordinary orders page.");
  return response.json();
}

async function inspectOrdersTab(tab) {
  const cdp = await connectCdp(tab.webSocketDebuggerUrl, { callTimeoutMs: 2500 });
  try {
    return await evaluateJson(cdp, `JSON.stringify((() => {
      const clean = value => String(value || "").replace(/\\s+/g, " ").trim();
      const root = document.querySelector("#orderHistoryCard");
      const children = [...(root?.children || [])];
      const validCards = children.filter(card => {
        const link = card.querySelector(
          "a.btn-order-details[data-order-id], .account-order__header-info a"
        );
        return Boolean(
          clean(link?.getAttribute("data-order-id"))
          || /order\\s+(?:number|#)?\\s*[A-Za-z0-9-]{4,}/i.test(clean(link?.textContent))
        );
      });
      const historyText = clean(root?.textContent);
      return {
        url: location.href,
        ready_state: document.readyState,
        visibility_state: document.visibilityState,
        root_found: Boolean(root),
        child_count: children.length,
        valid_card_count: validCards.length,
        empty_signal: validCards.length === 0
          && /no orders|haven't placed an order|have not placed an order/i.test(historyText)
      };
    })())`);
  } finally {
    cdp.close();
  }
}

async function findOrdersTab(port) {
  const tabs = await listTabs(port);
  const candidates = tabs.filter((tab) => (
    tab.type === "page"
    && tab.webSocketDebuggerUrl
    && (isOrdinaryOrdersTabUrl(tab.url) || isOrdinaryLoginTabUrl(tab.url))
  )).slice(0, 6);
  let selected = null;
  let selectedScore = -1;
  for (const candidate of candidates) {
    try {
      const score = ordinaryOrdersTabScore(await inspectOrdersTab(candidate));
      if (score > selectedScore) {
        selected = candidate;
        selectedScore = score;
      }
      if (score >= 100) break;
    } catch {
      // A stale restored tab is ignored; another candidate or a fresh tab is used.
    }
  }
  return selected || candidates[0] || openOrdersTab(port);
}

async function waitForDocument(cdp, timeoutMs = 15000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    const state = await evaluateJson(cdp, `JSON.stringify({
      readyState: document.readyState,
      hasBody: Boolean(document.body)
    })`);
    if (state.hasBody && ["interactive", "complete"].includes(state.readyState)) return;
    await sleep(300);
  }
  throw new Error("Timed out waiting for The Ordinary orders page.");
}

function cardsExpression() {
  return `JSON.stringify((() => {
    const clean = value => String(value || "").replace(/\\s+/g, " ").trim();
    const rows = [];
    for (const card of document.querySelectorAll("#orderHistoryCard > .account-order")) {
      const link = card.querySelector("a.btn-order-details[data-order-id]");
      const orderId = clean(link?.getAttribute("data-order-id"));
      if (!orderId) continue;
      rows.push({
        order_id: orderId,
        date_text: clean(card.querySelector(".account-order__date")?.textContent),
        status_text: clean(card.querySelector(".account-order__status-pill")?.textContent)
      });
    }
    const historyText = clean(document.querySelector("#orderHistoryCard")?.textContent);
    return {
      orders: rows,
      empty_signal: rows.length === 0 && /no orders|haven't placed an order|have not placed an order/i.test(historyText),
      page_path: location.pathname
    };
  })())`;
}

async function waitForOrderCards(cdp, timeoutMs = 20000) {
  const startedAt = Date.now();
  let latest = {};
  while (Date.now() - startedAt < timeoutMs) {
    latest = await evaluateJson(cdp, cardsExpression());
    if ((latest.orders || []).length || latest.empty_signal) return latest;
    await sleep(400);
  }
  return latest;
}

async function waitForEntryState(cdp, timeoutMs = 15000) {
  const startedAt = Date.now();
  let latestAuth = {};
  while (Date.now() - startedAt < timeoutMs) {
    latestAuth = await evaluateJson(cdp, ordinaryAuthFactsExpression());
    const page = await evaluateJson(cdp, cardsExpression());
    if (classifyOrdinaryEntryState(latestAuth, page) !== "waiting") {
      return latestAuth;
    }
    await sleep(300);
  }
  return latestAuth;
}

async function restoreGoogleSessionOnce(
  cdp,
  { port, currentTabId, timeoutMs = 60000 } = {},
) {
  return runOrdinaryOAuthRecovery({
    cdp,
    port,
    currentTabId,
    timeoutMs,
    listTabs,
    sleep,
    evaluateAuthFacts: (target) => evaluateJson(
      target,
      ordinaryAuthFactsExpression(),
    ),
    clickGoogle: (target) => evaluateJson(
      target,
      ordinaryGoogleClickExpression(),
    ),
    connectCdp,
    waitForDocument,
  });
}

function detailExpression(orderId) {
  const requestUrl = `https://theordinary.com${DETAIL_PATH}?orderID=${encodeURIComponent(orderId)}`;
  return `(async () => {
    const clean = value => String(value || "").replace(/\\s+/g, " ").trim();
    const absoluteProductUrl = value => {
      try {
        const url = new URL(value || "", "https://theordinary.com");
        if (!/(^|\\.)theordinary\\.com$/i.test(url.hostname)) return "";
        url.protocol = "https:";
        url.hostname = "theordinary.com";
        url.search = "";
        url.hash = "";
        return url.href;
      } catch { return ""; }
    };
    let response;
    try {
      response = await fetch(${JSON.stringify(requestUrl)}, {
        credentials: "include",
        headers: {
          "Accept": "text/html, */*; q=0.01",
          "X-Requested-With": "XMLHttpRequest"
        }
      });
    } catch {
      throw new Error("No The Ordinary detail response captured.");
    }
    const html = await response.text();
    const doc = new DOMParser().parseFromString(html, "text/html");
    const bodyText = clean(doc.body?.innerText || doc.body?.textContent || "");
    const responseUrl = new URL(response.url || ${JSON.stringify(requestUrl)});
    const loginSignal = /\\/(?:login|signin|sign-in)(?:[/?#]|$)/i.test(responseUrl.pathname)
      || /sign in to (?:continue|view|your account)/i.test(bodyText.slice(0, 800));
    const root = doc.querySelector(".confirm-order-details");
    if (!root) {
      return {
        http_status: response.status,
        login_signal: loginSignal,
        schema_ok: false
      };
    }
    const paymentText = clean(root.querySelector(".payment-details")?.textContent);
    const payment = paymentText.match(/\\b(Visa|Mastercard|MasterCard|American Express|Amex)?[^0-9]{0,80}[*xX\\u2022-]{2,}\\s*(\\d{4})\\b/i);
    const items = [...root.querySelectorAll(".js-product-line-item")].map(row => {
      const productLink = row.querySelector(".mini_cart_details a[href]")
        || row.querySelector(".item-image a[href]");
      return {
        title: clean(row.querySelector(".line-item-name")?.textContent),
        quantity_text: clean(row.querySelector(".line-item-total-qty")?.textContent)
          || clean(row.querySelector(".qty-attr")?.textContent),
        line_total_text: clean(row.querySelector(".line-item-total-price-amount")?.textContent),
        product_url: absoluteProductUrl(productLink?.getAttribute("href"))
      };
    }).filter(item => item.title);
    return {
      http_status: response.status,
      login_signal: loginSignal,
      schema_ok: true,
      order_id: clean(root.querySelector(".order-detail__number-value")?.textContent),
      date_text: clean(root.querySelector(".order-detail__date-value")?.textContent),
      status_text: clean(root.querySelector(".order-heading-status")?.textContent),
      subtotal_text: clean(root.querySelector(".sub-total")?.textContent),
      tax_text: clean(root.querySelector(".tax-hst")?.textContent)
        || clean(root.querySelector(".tax-total")?.textContent),
      total_text: clean(root.querySelector(".grand-total-sum")?.textContent),
      payment_network: payment?.[1] || "",
      card_last4: payment?.[2] || "",
      items
    };
  })()`;
}

function normalizeDetail(card, detail) {
  const orderId = extractOrdinaryOrderId(detail.order_id)
    || extractOrdinaryOrderId(card.order_id);
  if (!orderId || orderId !== extractOrdinaryOrderId(card.order_id)) {
    throw new Error("The Ordinary detail response did not match the requested order.");
  }
  const date = parseOrdinaryDate(detail.date_text) || parseOrdinaryDate(card.date_text);
  if (!date) throw new Error("The Ordinary order date schema was not recognized.");
  const amounts = reconcileAmounts({
    total: parseMoney(detail.total_text),
    subtotal: parseMoney(detail.subtotal_text),
    tax: parseMoney(detail.tax_text),
  });
  if (amounts.total <= 0) {
    throw new Error("The Ordinary order total schema was not recognized.");
  }
  const items = (detail.items || []).map((item) => ({
    title: cleanText(item.title),
    quantity: Math.max(1, Number(String(item.quantity_text || "").match(/\d+/)?.[0]) || 1),
    line_total: /[0-9]/.test(item.line_total_text) ? Math.max(0, parseMoney(item.line_total_text)) : null,
    price_known: /[0-9]/.test(item.line_total_text),
    product_url: normalizeProductUrl(item.product_url),
  })).filter((item) => item.title);
  return {
    order_id: orderId,
    date,
    total: amounts.total,
    item_total: amounts.subtotal,
    tax: amounts.tax,
    fees: amounts.fees,
    currency: "CAD",
    fulfillment: normalizeFulfillment(detail.status_text || card.status_text),
    category: "personal_care",
    source_url: ORDERS_URL,
    items,
    payment: {
      payment_method: detail.card_last4 ? "card" : "",
      card_network: cleanText(detail.payment_network).replace(/^MasterCard$/i, "Mastercard"),
      card_last4: /^\d{4}$/.test(detail.card_last4 || "") ? detail.card_last4 : "",
    },
  };
}

function evidenceKey(row) {
  return `${row.platform || ""}|${row.order_id || ""}`;
}

async function writeEvidence(outputPath, orders, capturedAt) {
  const existing = await readJson(outputPath, { evidence: [] });
  const merged = new Map();
  for (const row of existing.evidence || []) merged.set(evidenceKey(row), row);
  for (const order of orders) {
    const payment = order.payment || {};
    const found = Boolean(payment.card_last4);
    const row = {
      platform: PLATFORM,
      order_id: order.order_id,
      source: "the_ordinary_ca_payment_dom",
      payment_method: payment.payment_method || "",
      card_network: payment.card_network || "",
      card_last4: payment.card_last4 || "",
      status: found ? "found" : "not_found",
      evidence_snippet: found
        ? `${payment.card_network || "card"} ending ${payment.card_last4}`
        : "Payment card was not exposed in the order detail drawer.",
      captured_at: capturedAt,
    };
    merged.set(evidenceKey(row), row);
  }
  await atomicWriteFile(outputPath, `${JSON.stringify({
    contract: "expense-payment-evidence/v1",
    generated_at: capturedAt,
    evidence: [...merged.values()],
  }, null, 2)}\n`);
}

async function collect(args) {
  const tab = await findOrdersTab(args.port);
  const initialCdp = await connectCdp(tab.webSocketDebuggerUrl);
  let cdp = initialCdp;
  let currentTabId = tab.id;
  const connectedCdps = new Set([initialCdp]);
  try {
    await cdp.call("Page.enable");
    await cdp.call("Runtime.enable");
    await waitForDocument(cdp);
    return await runWithCollectionPolicy(PLATFORM, async (policy) => {
      await waitForEntryState(cdp);
      const auth = await restoreGoogleSessionOnce(cdp, {
        port: args.port,
        currentTabId,
      });
      cdp = auth.cdp || cdp;
      connectedCdps.add(cdp);
      currentTabId = auth.tabId || currentTabId;
      let googleSessionRestored = auth.restored;
      if (auth.manual) {
        const message = auth.attempted
          ? "The Ordinary Google recovery did not return to the orders page within 60 seconds; manual confirmation is required."
          : "The Ordinary Google session requires manual confirmation.";
        await policy.recordLoginRequired(
          message,
        );
        throw new CollectionSessionError(
          message,
        );
      }
      await policy.inspectPage(cdp);
      const page = await waitForOrderCards(cdp);
      const cards = (page.orders || []).map((card) => ({
        ...card,
        order_id: extractOrdinaryOrderId(card.order_id),
        date: parseOrdinaryDate(card.date_text),
      })).filter((card) => card.order_id && (!card.date || card.date >= args.minDate));

      if (!cards.length && !page.empty_signal) {
        throw new Error("The Ordinary order-card schema was not recognized.");
      }

      const collected = [];
      for (const card of cards.slice(0, args.maxOrders)) {
        await policy.pace();
        const attempt = await runOrdinaryDetailWithRecovery({
          cdp,
          fetchDetail: (target) => policy.retry(
            "fetch The Ordinary detail drawer",
            () => evaluateJson(
              target,
              detailExpression(card.order_id),
              { awaitPromise: true },
            ),
          ),
          recover: async (target) => {
            await policy.pace();
            await policy.retry(
              "reload The Ordinary authenticated orders page",
              () => target.call("Page.navigate", { url: ORDERS_URL }),
            );
            await waitForDocument(target);
            await waitForEntryState(target);
            return restoreGoogleSessionOnce(target, {
              port: args.port,
              currentTabId,
            });
          },
        });
        cdp = attempt.cdp || cdp;
        connectedCdps.add(cdp);
        if (attempt.recovery) {
          currentTabId = attempt.recovery.tabId || currentTabId;
          googleSessionRestored = (
            googleSessionRestored || attempt.recovery.restored
          );
          if (attempt.recovery.manual) {
            const message = attempt.recovery.attempted
              ? "The Ordinary Google recovery did not return to the orders page within 60 seconds; manual confirmation is required."
              : "The Ordinary Google session requires manual confirmation.";
            await policy.recordLoginRequired(message);
            throw new CollectionSessionError(message);
          }
        }
        const detail = attempt.detail;
        await policy.handleHttpStatus(detail.http_status, "The Ordinary detail drawer");
        if (detail.login_signal) {
          await policy.recordLoginRequired("The Ordinary detail response requires login.");
          throw new CollectionSessionError("The Ordinary dedicated browser session requires login.");
        }
        if (!detail.schema_ok) {
          throw new Error("The Ordinary detail drawer schema was not recognized.");
        }
        const order = normalizeDetail(card, detail);
        if (order.date >= args.minDate) collected.push(order);
        await policy.settle(0.35);
      }

      const previous = await readJson(args.out, { orders: [] });
      const orders = mergeOrdinaryOrders(previous.orders, collected);
      const generatedAt = new Date().toISOString();
      await atomicWriteFile(args.out, `${JSON.stringify({
        contract: "expense-the-ordinary-orders/v1",
        platform: PLATFORM,
        source: "live",
        generated_at: generatedAt,
        orders,
      }, null, 2)}\n`);
      await writeEvidence(args.evidenceOut, collected, generatedAt);
      return {
        platform: PLATFORM,
        observed_dates: observedDates(cards),
        candidates: cards.length,
        collected: collected.length,
        retained: orders.length,
        google_session_restored: googleSessionRestored,
      };
    }, {
      summary: (result) => ({ collected: result.collected, retained: result.retained, observed_dates: result.observed_dates }),
    });
  } finally {
    for (const connection of connectedCdps) connection.close();
  }
}

try {
  const result = await collect(parseArgs(process.argv.slice(2)));
  console.log(JSON.stringify(result));
} catch (error) {
  console.error(`The Ordinary collection failed: ${cleanText(error?.message || error)}`);
  process.exitCode = 1;
}
