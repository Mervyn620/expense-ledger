import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { connectCdp, listTabs, sleep } from "../browser/cdp-client.mjs";
import { observedDates, runWithCollectionPolicy } from "../browser/collection-policy.mjs";
import { atomicWriteFile } from "../lib/atomic-file.mjs";
import {
  canRecoverFantuanNavigation,
  isFantuanDocumentReady,
} from "./fantuan-navigation.mjs";
import {
  parseFantuanCardText,
  fantuanOrderMetadata,
  fantuanCardsNeedingDetails,
  refreshFantuanCachedStatuses,
} from "./fantuan-order-utils.mjs";

function parseArgs(argv) {
  const args = {
    port: 9223,
    out: "data/fantuan_orders.json",
    minDate: "2026-05-01",
    maxOrders: 12,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--port") args.port = Number(argv[++i]);
    else if (arg === "--out") args.out = argv[++i] ?? args.out;
    else if (arg === "--min-date") args.minDate = argv[++i] ?? args.minDate;
    else if (arg === "--max-orders") args.maxOrders = Number(argv[++i]);
  }

  return args;
}

function clean(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function money(value) {
  if (typeof value === "number") return value;
  const match = String(value || "").match(/-?\$?\s*([0-9]+(?:\.[0-9]{1,2})?)/);
  return match ? Number(match[1]) : 0;
}

function moneyString(value) {
  return money(value).toFixed(2);
}

function addMoney(target, field, amount) {
  target[field] = Number((target[field] + amount).toFixed(2));
}

function extractFees(settlementInfo = []) {
  const fees = {
    tax: 0,
    tip: 0,
    service_fee: 0,
    delivery_fee: 0,
    regulatory_fee: 0,
    other_fee: 0,
    breakdown: [],
  };

  for (const entry of settlementInfo || []) {
    const title = clean(entry?.title);
    const amount = money(entry?.amount);
    if (!title && !amount) continue;
    fees.breakdown.push({ title, amount: moneyString(entry?.amount) });

    if (/小费|tip/i.test(title)) {
      addMoney(fees, "tip", amount);
      continue;
    }
    if (/配送|delivery/i.test(title)) {
      addMoney(fees, "delivery_fee", amount);
      continue;
    }
    if (/法规|regulatory/i.test(title)) {
      addMoney(fees, "regulatory_fee", amount);
      continue;
    }
    if (/税|tax/i.test(title)) {
      let taxFound = false;
      for (const detail of entry?.labelDesc?.content || []) {
        const detailTitle = clean(detail?.title);
        const detailAmount = money(detail?.amount ?? detailTitle);
        if (/税|tax/i.test(detailTitle)) {
          taxFound = true;
          addMoney(fees, "tax", detailAmount);
        } else if (/服务费|service/i.test(detailTitle)) {
          addMoney(fees, "service_fee", money(detailTitle || detail?.amount));
        } else if (detailAmount) {
          addMoney(fees, "other_fee", detailAmount);
        }
      }
      if (!taxFound && amount) addMoney(fees, "tax", amount);
      continue;
    }
    if (/服务|service/i.test(title)) {
      addMoney(fees, "service_fee", amount);
      continue;
    }
    if (/费|fee/i.test(title)) addMoney(fees, "other_fee", amount);
  }

  return fees;
}

function summarizeDetail(json, card) {
  const data = json?.data || {};
  const receipt = data.receiptInfo || {};
  const goods = (receipt.goods || []).map((item) => ({
    name: clean(item.goodsName),
    quantity: String(item.goodsCount ?? ""),
    price: /[0-9]/.test(String(item.goodsPrice ?? "")) ? moneyString(item.goodsPrice) : null,
    attributes: clean(item.goodsAttrs),
  })).filter((item) => item.name);

  const subtotalEntry = (receipt.goodTotalPriceInfos || []).find((entry) => /小计|subtotal/i.test(clean(entry?.title)));
  const feeInfo = extractFees(receipt.settlementInfo || []);
  const metadata = fantuanOrderMetadata(data, card);
  const orderId = metadata.orderId;
  const totalAmount = money(data.price || receipt.totalPrice || card.total);
  const itemTotal = money(subtotalEntry?.amount || 0) || totalAmount;
  const netFees = totalAmount - itemTotal - feeInfo.tax - feeInfo.tip;

  return {
    date: metadata.date,
    datetime: metadata.dateTime,
    platform: "fantuan",
    order_id: orderId,
    merchant: clean(data.restaurantName) || card.merchant,
    status: metadata.status,
    category: "food_delivery",
    item: goods.map((item) => item.name).join("; ") || card.itemSummary,
    quantity: String(goods.reduce((sum, item) => sum + Number(item.quantity || 0), 0) || ""),
    item_total: itemTotal.toFixed(2),
    tax: feeInfo.tax.toFixed(2),
    fees: netFees.toFixed(2),
    tip: feeInfo.tip.toFixed(2),
    total: totalAmount.toFixed(2),
    currency: "CAD",
    source_url: `https://www.fantuanorder.com/zh-CN/order-detail/${encodeURIComponent(orderId)}`,
    notes: [
      "Imported from Fantuan order detail via dedicated Codex browser.",
      `Fantuan status: ${metadata.status}`,
      `fees=${JSON.stringify(feeInfo.breakdown)}`,
    ].join(" "),
    goods,
  };
}

async function evaluateJson(cdp, expression, options = {}) {
  const result = await cdp.call("Runtime.evaluate", {
    expression,
    awaitPromise: Boolean(options.awaitPromise),
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(JSON.stringify(result.exceptionDetails.exception?.description || result.exceptionDetails));
  }
  return JSON.parse(result.result?.value || "{}");
}

async function waitForJson(cdp, expression, predicate, timeoutMs = 12000, intervalMs = 500) {
  const startedAt = Date.now();
  let lastValue = null;
  while (Date.now() - startedAt < timeoutMs) {
    lastValue = await evaluateJson(cdp, expression);
    if (predicate(lastValue)) return lastValue;
    await sleep(intervalMs);
  }
  throw new Error(`Timed out waiting for page state: ${JSON.stringify(lastValue)}`);
}

async function fantuanPageState(cdp) {
  return evaluateJson(cdp, `JSON.stringify({
    readyState: document.readyState,
    hasHeaderMenu: Boolean(document.querySelector("#header .header_left i, #header > div > div.header_left > i")),
    url: location.href
  })`);
}

async function waitForFantuanPage(cdp, timeoutMs = 12000) {
  return waitForJson(
    cdp,
    `JSON.stringify({
      readyState: document.readyState,
      hasHeaderMenu: Boolean(document.querySelector("#header .header_left i, #header > div > div.header_left > i")),
      url: location.href
    })`,
    (value) => ["interactive", "complete"].includes(value.readyState)
      && value.hasHeaderMenu,
    timeoutMs,
  );
}

async function navigate(cdp, policy, url, waitMs = 3500) {
  const current = await fantuanPageState(cdp);
  if (isFantuanDocumentReady(current)) {
    await policy.inspectPage(cdp);
    try {
      await waitForFantuanPage(cdp);
      return;
    } catch (error) {
      if (!/^Timed out waiting for page state:/.test(String(error?.message || error))) {
        throw error;
      }
    }
  }

  await policy.pace();
  await policy.retry(
    "navigate Fantuan page",
    async () => {
      try {
        return await cdp.call("Page.navigate", { url });
      } catch (error) {
        let state = {};
        try {
          state = await fantuanPageState(cdp);
        } catch {
          // The retry policy will handle the original CDP navigation error.
        }
        if (canRecoverFantuanNavigation(error, state)) {
          return { recovered_after_timeout: true };
        }
        throw error;
      }
    },
  );
  await policy.settle(Math.max(1, waitMs / 1800));
  await waitForFantuanPage(cdp);
  await policy.inspectPage(cdp);
}

async function openOrderModal(cdp, policy) {
  await waitForJson(
    cdp,
    `JSON.stringify((() => ({
      readyState: document.readyState,
      hasHeaderMenu: Boolean(document.querySelector("#header .header_left i, #header > div > div.header_left > i")),
      hasOrderModal: [...document.querySelectorAll(".ant-modal-wrap, .ant-modal")]
        .some(el => String(el.innerText || el.textContent || "").includes("\\u6211\\u7684\\u8ba2\\u5355")),
    }))())`,
    (value) => value.hasHeaderMenu || value.hasOrderModal,
    12000,
  );

  await policy.pace();
  const result = await evaluateJson(cdp, `JSON.stringify((() => {
    const clean = value => String(value || "").replace(/\\s+/g, " ").trim();
    const modal = [...document.querySelectorAll(".ant-modal-wrap, .ant-modal")]
      .find(el => clean(el.innerText || el.textContent || "").includes("\\u6211\\u7684\\u8ba2\\u5355"));
    if (modal) return { opened: true, alreadyOpen: true };

    const header = document.querySelector("#header .header_left i, #header > div > div.header_left > i");
    if (!header) return { opened: false, reason: "menu selector not found" };
    header.click();
    return { opened: true, clickedMenu: true };
  })())`);
  if (!result.opened) throw new Error(`Could not open Fantuan menu: ${result.reason}`);

  await policy.settle();
  await policy.pace();
  const clicked = await evaluateJson(cdp, `JSON.stringify((() => {
    const clean = value => String(value || "").replace(/\\s+/g, " ").trim();
    const modal = [...document.querySelectorAll(".ant-modal-wrap, .ant-modal")]
      .find(el => clean(el.innerText || el.textContent || "").includes("\\u6211\\u7684\\u8ba2\\u5355"));
    if (modal) return { clicked: true, alreadyOpen: true };

    const orderText = "\\u8ba2\\u5355";
    const nodes = [...document.querySelectorAll(".ant-drawer a,.ant-drawer button,.ant-drawer div,.ant-drawer li,[role='button']")];
    const node = nodes.find(el => clean(el.innerText || el.textContent || "") === orderText);
    if (!node) return { clicked: false, reason: "order item not found", drawerText: clean(document.querySelector(".ant-drawer")?.innerText || "").slice(0, 300) };
    node.click();
    return { clicked: true };
  })())`);
  if (!clicked.clicked) throw new Error(`Could not click Fantuan order menu: ${clicked.reason || clicked.drawerText}`);
  await policy.settle(1.25);
}

async function visibleCards(cdp) {
  const result = await evaluateJson(cdp, `JSON.stringify((() => {
    const clean = value => String(value || "").replace(/\\s+/g, " ").trim();
    const container = document.querySelector("#dyx") || document.querySelector(".ant-modal-body");
    const cards = [...document.querySelectorAll('[class*="history-order_historyOrderCard"]')]
      .map((card, index) => ({ index, text: String(card.innerText || card.textContent || "").trim() }))
      .filter(card => card.text);
    return {
      cards,
      activeCards: [...document.querySelectorAll('[class*="underway-order_underwayOrderCard"]')]
        .map((card,index)=>({index,text:String(card.innerText || card.textContent || "").trim()})).filter(card=>card.text),
      hasModal: Boolean(document.querySelector(".ant-modal-wrap, .ant-modal")),
      scrollTop: container?.scrollTop || 0,
      scrollHeight: container?.scrollHeight || 0,
      clientHeight: container?.clientHeight || 0,
    };
  })())`);
  return result;
}

async function collectCards(cdp, policy, minDate, maxOrders) {
  const byKey = new Map();
  let idleScrolls = 0;

  for (let round = 0; round < 12 && byKey.size < maxOrders; round += 1) {
    const state = await visibleCards(cdp);
    const before = byKey.size;
    for (const card of state.cards || []) {
      const parsed = parseFantuanCardText(card.text);
      if (!parsed.date || parsed.date < minDate) continue;
      const key = `${parsed.date}|${parsed.merchant}|${parsed.total}|${parsed.itemSummary}`;
      byKey.set(key, parsed);
    }
    if (byKey.size === before) idleScrolls += 1;
    else idleScrolls = 0;

    if (!state.scrollHeight || state.scrollTop + state.clientHeight + 20 >= state.scrollHeight || idleScrolls >= 2) {
      break;
    }

    await cdp.call("Runtime.evaluate", {
      expression: `(() => {
        const container = document.querySelector("#dyx") || document.querySelector(".ant-modal-body");
        if (container) container.scrollTop = container.scrollTop + Math.max(container.clientHeight - 40, 240);
      })()`,
      returnByValue: true,
    });
    await policy.settle(0.75);
  }

  return [...byKey.values()].slice(0, maxOrders);
}

async function findAndClickCard(cdp, policy, card) {
  if (card.kind === "active") {
    await policy.pace();
    const result = await evaluateJson(cdp, `JSON.stringify((() => {
      const merchant = ${JSON.stringify(card.merchant)};
      const pickup = ${JSON.stringify(card.pickupNumber)};
      const original = ${JSON.stringify(card.text)};
      const cards = [...document.querySelectorAll('[class*="underway-order_underwayOrderCard"]')];
      const targets = cards.filter(el => {
        const text = String(el.innerText || el.textContent || "").trim();
        return text.includes(merchant) && (pickup ? text.includes(pickup) : text === original);
      });
      if (targets.length !== 1) return {clicked:false};
      targets[0].scrollIntoView({block:"center"});targets[0].click();return {clicked:true};
    })())`);
    return result.clicked;
  }
  for (let round = 0; round < 12; round += 1) {
    await policy.pace();
    const result = await evaluateJson(cdp, `JSON.stringify((() => {
      const merchant = ${JSON.stringify(card.merchant)};
      const date = ${JSON.stringify(card.dateTime)};
      const total = ${JSON.stringify(`$${card.total}`)};
      const cards = [...document.querySelectorAll('[class*="history-order_historyOrderCard"]')];
      const target = cards.find(card => {
        const text = String(card.innerText || card.textContent || "");
        return text.includes(merchant) && text.includes(date) && text.includes(total);
      });
      if (target) {
        target.scrollIntoView({block: "center"});
        target.click();
        return { clicked: true };
      }
      const container = document.querySelector("#dyx") || document.querySelector(".ant-modal-body");
      if (container) container.scrollTop = container.scrollTop + Math.max(container.clientHeight - 40, 240);
      return { clicked: false, scrollTop: container?.scrollTop || 0, scrollHeight: container?.scrollHeight || 0 };
    })())`);
    if (result.clicked) return true;
    await policy.settle(0.5);
  }
  return false;
}

async function readOrderDetail(cdp, policy, card) {
  const clicked = await findAndClickCard(cdp, policy, card);
  if (!clicked) throw new Error(`Could not click Fantuan order card: ${card.merchant} ${card.dateTime} ${card.total}`);

  const detailPage = await waitForJson(cdp, "JSON.stringify({path:location.pathname})",
    value => /\/order-detail\/[^/?]+/.test(value.path || ""), 8000);
  const visibleOrderId = detailPage.path.match(/\/order-detail\/([^/?]+)/)[1];
  let response = null;
  for (let attempt = 0; attempt < 2 && !response; attempt += 1) {
    const responseStart = attempt === 0 ? 0 : cdp.orderResponses.length;
    if (attempt === 1) {
      // React may already hold the active order in memory. Reload its visible
      // detail page once to observe the platform's fresh, authenticated response.
      await policy.pace();
      await cdp.call("Page.reload", {ignoreCache: true});
      await policy.settle();
      await policy.inspectPage(cdp);
    }
    for (let i = 0; i < 16; i += 1) {
      const next = [...cdp.orderResponses.slice(responseStart)].reverse().find(entry =>
        new URL(entry.url).searchParams.get("orderSn") === visibleOrderId);
      if (next) {
        await policy.handleHttpStatus(next.status, "Fantuan order detail");
        response = next;
        break;
      }
      await sleep(500);
    }
  }
  if (!response) throw new Error(`No Fantuan order detail response captured for ${card.merchant} ${card.dateTime}`);

  const body = await cdp.call("Network.getResponseBody", { requestId: response.requestId });
  const json = JSON.parse(body.body || "{}");
  if (json.code !== 0) throw new Error(`Fantuan detail returned code ${json.code} for ${card.merchant} ${card.dateTime}`);
  return summarizeDetail(json, card);
}

async function readExisting(outputPath) {
  try {
    const text = await readFile(outputPath, "utf8");
    const data = JSON.parse(text);
    return Array.isArray(data.orders) ? data.orders : [];
  } catch {
    return [];
  }
}

async function main(args, policy) {
  const tabs = await policy.retry("list browser tabs", () => listTabs(args.port));
  const page = tabs
    .filter((tab) => tab.type === "page" && tab.webSocketDebuggerUrl)
    .find((tab) => /^https:\/\/www\.fantuanorder\.com\//.test(tab.url));
  if (!page) throw new Error("No Fantuan tab is open in the dedicated shop browser.");

  const orderResponses = [];
  const cdp = await policy.retry(
    "connect Fantuan tab",
    () => connectCdp(page.webSocketDebuggerUrl, {
      callTimeoutMs: policy.config.cdp_call_timeout_ms,
      onMessage(message) {
        if (message.method !== "Network.responseReceived") return;
        const url = message.params.response?.url || "";
        if (!/\/api\/orderservice\/public\/order\?orderSn=/.test(url)) return;
        orderResponses.push({
          requestId: message.params.requestId,
          url,
          status: message.params.response.status,
        });
      },
    }),
  );
  cdp.orderResponses = orderResponses;
  try {
    await cdp.call("Network.enable", { maxResourceBufferSize: 10000000, maxTotalBufferSize: 20000000 });
    await cdp.call("Page.enable");

    const baseUrl = "https://www.fantuanorder.com/zh-CN";
    await navigate(cdp, policy, baseUrl);
    await openOrderModal(cdp, policy);
    const initial = await visibleCards(cdp);
    const activeCards = (initial.activeCards || []).map(card=>parseFantuanCardText(card.text,"active"));
    if (activeCards.length > args.maxOrders) throw new Error("Too many active Fantuan orders for this bounded run");
    const historyCards = await collectCards(cdp, policy, args.minDate, args.maxOrders);
    const cards = [...activeCards,...historyCards];
    if (!cards.length) {
      console.log(`No Fantuan orders found since ${args.minDate}.`);
      return { cards: 0, imported: 0 };
    }

    const previous = await readExisting(args.out);
    const existing = refreshFantuanCachedStatuses(previous, historyCards);
    const pendingCards = fantuanCardsNeedingDetails(previous, cards);
    const orders = [];
    for (const card of pendingCards) {
      const order = await policy.retry(
        "collect Fantuan order detail",
        async () => {
          await navigate(cdp, policy, baseUrl, 2200);
          await openOrderModal(cdp, policy);
          return readOrderDetail(cdp, policy, card);
        },
      );
      if (order.date >= args.minDate) orders.push(order);
    }

    const merged = new Map();
    for (const order of existing) merged.set(order.order_id, order);
    for (const order of orders) merged.set(order.order_id, order);
    const allOrders = [...merged.values()].sort((a, b) => `${b.date} ${b.datetime}`.localeCompare(`${a.date} ${a.datetime}`));

    await mkdir(path.dirname(args.out), { recursive: true });
    await atomicWriteFile(args.out, `${JSON.stringify({
      platform: "fantuan",
      generated_at: new Date().toISOString(),
      min_date: args.minDate,
      orders: allOrders,
    }, null, 2)}\n`);

    const importedTotal = orders.reduce((sum, order) => sum + money(order.total), 0);
    console.log(`Refreshed ${orders.length} Fantuan order detail(s), CAD ${importedTotal.toFixed(2)}; cached=${allOrders.length}.`);
    return {
      cards: cards.length,
      observed_dates: observedDates(cards),
      active_cards: activeCards.length,
      imported: orders.length,
      cached: allOrders.length,
    };
  } finally {
    cdp.close();
  }
}

const args = parseArgs(process.argv.slice(2));
runWithCollectionPolicy(
  "fantuan",
  (policy) => main(args, policy),
  { summary: (result) => result },
).catch((error) => {
  console.error(error.message);
  process.exit(1);
});
