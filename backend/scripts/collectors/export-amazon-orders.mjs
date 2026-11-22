import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { connectCdp, listTabs } from "../browser/cdp-client.mjs";
import { navigateToContent } from "../browser/content-ready.mjs";
import {
  CollectionSessionError,
  observedDates,
  runWithCollectionPolicy,
} from "../browser/collection-policy.mjs";
import { atomicWriteFile } from "../lib/atomic-file.mjs";
import {
  cleanText,
  amazonCardFingerprint,
  collectAmazonDetails,
  extractAmazonOrderId,
  mergeAmazonOrders,
  normalizeFulfillment,
  parseAmazonDate,
  parseMoney,
  reconcileAmounts,
} from "./amazon-order-utils.mjs";

// Selector strategy reviewed against Xenolphthalein/order-history-exporter-for-amazon
// (Unlicense). This implementation keeps the existing isolated-CDP ledger contract.

function parseArgs(argv) {
  const args = {
    port: 9223,
    out: "data/amazon_orders.json",
    evidenceOut: "data/payment_evidence.json",
    minDate: "2026-05-01",
    maxOrders: 12,
    maxPages: 8,
    refreshAll: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--port") args.port = Number(argv[++index]);
    else if (arg === "--out") args.out = argv[++index] ?? args.out;
    else if (arg === "--evidence-out") args.evidenceOut = argv[++index] ?? args.evidenceOut;
    else if (arg === "--min-date") args.minDate = argv[++index] ?? args.minDate;
    else if (arg === "--max-orders") args.maxOrders = Number(argv[++index]);
    else if (arg === "--max-pages") args.maxPages = Number(argv[++index]);
    else if (arg === "--refresh-all") args.refreshAll = true;
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

async function evaluateJson(cdp, expression, options = {}) {
  const result = await cdp.call("Runtime.evaluate", {
    expression,
    awaitPromise: Boolean(options.awaitPromise),
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(
      result.exceptionDetails.exception?.description
      || result.exceptionDetails.text
      || "Amazon page evaluation failed.",
    );
  }
  return JSON.parse(result.result?.value || "{}");
}

async function navigate(cdp, policy, url) {
  const state = await navigateToContent(cdp, policy, url, {
    label: "Amazon order list",
    expression: `JSON.parse(${ordersPageExpression()})`,
    ready: page => Boolean(page?.empty) || Boolean(page?.orders?.length && page.orders.every(order => order.order_id && order.date)),
  });
  return state.content;
}

export function ordersPageExpression() {
  return `JSON.stringify((() => {
    const clean = value => String(value || "").replace(/\\s+/g, " ").trim();
    const orderId = value => String(value || "").match(/\\b\\d{3}-\\d{7}-\\d{7}\\b/)?.[0] || "";
    const absoluteUrl = href => {
      const raw = String(href || "").trim();
      if (!raw || raw === "#") return "";
      try {
        const url = new URL(raw, location.origin);
        if (url.protocol !== "https:" || !/(^|\\.)amazon\\.ca$/i.test(url.hostname)) return "";
        url.hash = "";
        return url.href;
      } catch {
        return "";
      }
    };
    const amount = value => {
      const match = String(value || "").match(/(?:CAD|C\\$|\\$)\\s*([0-9][0-9,]*(?:\\.[0-9]{1,2})?)/i);
      return match ? Number(match[1].replace(/,/g, "")) : 0;
    };
    const parseDate = value => {
      const months = {
        january:1,february:2,march:3,april:4,may:5,june:6,july:7,august:8,
        september:9,october:10,november:11,december:12,
        janvier:1,fevrier:2,mars:3,avril:4,mai:5,juin:6,juillet:7,aout:8,
        septembre:9,octobre:10,novembre:11,decembre:12
      };
      const text = clean(value).normalize("NFD").replace(/[\\u0300-\\u036f]/g, "").toLowerCase();
      let match = text.match(/\\b([a-z]+)\\s+(\\d{1,2})(?:st|nd|rd|th)?,?\\s+(20\\d{2})\\b/i);
      let year, month, day;
      if (match && months[match[1]]) {
        month = months[match[1]]; day = Number(match[2]); year = Number(match[3]);
      } else {
        match = text.match(/\\b(\\d{1,2})(?:er|st|nd|rd|th)?\\s+([a-z]+)\\s+(20\\d{2})\\b/i);
        if (!match || !months[match[2]]) return "";
        day = Number(match[1]); month = months[match[2]]; year = Number(match[3]);
      }
      return year + "-" + String(month).padStart(2, "0") + "-" + String(day).padStart(2, "0");
    };
    const lineAmount = (lines, labelPattern) => {
      for (let index = 0; index < lines.length; index += 1) {
        if (!labelPattern.test(lines[index])) continue;
        for (let offset = 0; offset < 3; offset += 1) {
          const found = amount(lines[index + offset] || "");
          if (found) return found;
        }
      }
      return 0;
    };

    const selectors = [
      ".order-card",
      "[data-component='orderCard']",
      "[data-testid='order-card']",
      ".a-box-group.order",
      ".your-orders-content-container .a-box-group",
      "#ordersContainer .order-card",
      ".js-order-card",
      "[class*='order-card']"
    ];
    let cards = [];
    for (const selector of selectors) {
      const found = [...document.querySelectorAll(selector)];
      if (found.length) {
        cards = found;
        break;
      }
    }
    if (!cards.length) {
      const containers = new Set();
      for (const link of document.querySelectorAll("a[href*='order-details'],a[href*='orderID='],a[href*='orderId=']")) {
        let node = link;
        for (let depth = 0; depth < 8 && node; depth += 1, node = node.parentElement) {
          if (orderId(node.textContent)) {
            containers.add(node);
            if (node.classList.contains("a-box-group") || node.classList.contains("order-card")) break;
          }
        }
      }
      cards = [...containers];
    }

    const parsed = [];
    const seen = new Set();
    for (const card of cards) {
      const text = String(card.innerText || card.textContent || "");
      const links = [...card.querySelectorAll("a[href]")];
      let id = orderId(text);
      if (!id) {
        for (const link of links) {
          id = orderId(link.getAttribute("href"));
          if (id) break;
        }
      }
      if (!id || seen.has(id)) continue;
      seen.add(id);
      const lines = text.split(/\\r?\\n/).map(clean).filter(Boolean);
      const detailsLink = links.find(link => /order-details|orderID=|orderId=/i.test(link.getAttribute("href") || ""));
      const detailsUrl = absoluteUrl(detailsLink?.getAttribute("href") || "");
      const itemMap = new Map();
      for (const link of card.querySelectorAll("a[href*='/dp/'],a[href*='/gp/product/']")) {
        const href = link.getAttribute("href") || "";
        const asin = href.match(/\\/(?:dp|gp\\/product)\\/([A-Z0-9]{10})/i)?.[1]?.toUpperCase();
        if (!asin || itemMap.has(asin)) continue;
        let title = clean(link.textContent || link.getAttribute("title") || link.getAttribute("aria-label"));
        if (!title) title = clean(link.querySelector("img")?.alt || link.parentElement?.querySelector("img")?.alt);
        let quantity = 1;
        let parent = link.parentElement;
        for (let depth = 0; depth < 6 && parent; depth += 1, parent = parent.parentElement) {
          const quantityMatch = String(parent.textContent || "").match(/(?:Qty|Quantity|Quantite)[:\\s]*(\\d+)/i);
          if (quantityMatch) {
            quantity = Number(quantityMatch[1]) || 1;
            break;
          }
        }
        itemMap.set(asin, {
          asin,
          title: title || asin,
          quantity,
          line_total: null,
          product_url: "https://www.amazon.ca/dp/" + asin
        });
      }
      const status = lines.filter(line => /delivered|arriv|shipped|not yet shipped|cancelled|canceled|livr|expedi|annul|refunded|refund (?:issued|pending)|returned|return (?:started|requested|initiated|received|complete|in progress)|rembours|retour (?:en cours|reçu)/i.test(line)).join("; ");
      parsed.push({
        order_id: id,
        date: parseDate(text),
        total: lineAmount(lines, /^(?:order total|grand total|total|total de la commande)$/i),
        status,
        details_url: detailsUrl,
        items: [...itemMap.values()]
      });
    }

    const next = document.querySelector(
      ".a-pagination .a-last:not(.a-disabled) a, .a-pagination li:last-child:not(.a-disabled) a, a[aria-label*='Next'], a[aria-label*='Suivant']"
    );
    const nextUrl = absoluteUrl(next?.getAttribute("href"));
    return {
      orders: parsed,
      next_url: nextUrl && new URL(nextUrl).pathname !== "/" ? nextUrl : "",
      page_url: location.origin + location.pathname,
      body_hint: clean(document.body?.innerText || "").slice(0, 200),
      empty: parsed.length === 0 && /you have not placed any orders|no orders found|aucune commande/i.test(document.body?.innerText || "")
    };
  })())`;
}

function detailPageExpression(detailsUrl, cardItems) {
  return `(async () => {
    const clean = value => String(value || "").replace(/\\s+/g, " ").trim();
    const amount = (value, missing = 0) => {
      const match = String(value || "").match(/(-?)\\s*(?:CAD|C\\$|\\$)\\s*([0-9][0-9,]*(?:\\.[0-9]{1,2})?)/i);
      if (!match) return missing;
      const parsed = Number(match[2].replace(/,/g, ""));
      return match[1] ? -parsed : parsed;
    };
    const response = await fetch(${JSON.stringify(detailsUrl)}, { credentials: "include" });
    const html = await response.text();
    const doc = new DOMParser().parseFromString(html, "text/html");
    const text = String(doc.body?.innerText || doc.body?.textContent || "");
    const summaryRoot = doc.querySelector("#od-subtotals > div:nth-child(1) > div")
      || doc.querySelector("#od-subtotals")
      || [...doc.querySelectorAll("#orderDetails .a-box, #orderDetails .a-row")]
        .find(node => /item\\(s\\) subtotal|grand total|order summary/i.test(node.textContent || ""));
    const summaryLines = String(summaryRoot?.innerText || summaryRoot?.textContent || "")
      .split(/\\r?\\n/).map(clean).filter(Boolean);
    const signedLineAmount = (lines, pattern, options = {}) => {
      for (let index = 0; index < lines.length; index += 1) {
        if (!pattern.test(lines[index]) || (options.exclude && options.exclude.test(lines[index]))) continue;
        for (let offset = 0; offset < 3; offset += 1) {
          const found = amount(lines[index + offset] || "");
          if (found) return found;
        }
      }
      return 0;
    };
    const sumLines = (lines, pattern, exclude) => {
      const seen = new Set();
      let total = 0;
      for (let index = 0; index < lines.length; index += 1) {
        if (!pattern.test(lines[index]) || (exclude && exclude.test(lines[index]))) continue;
        for (let offset = 0; offset < 3; offset += 1) {
          const found = amount(lines[index + offset] || "");
          if (!found) continue;
          const key = lines[index].toLowerCase() + "|" + found;
          if (!seen.has(key)) {
            seen.add(key);
            total += Math.abs(found);
          }
          break;
        }
      }
      return total;
    };
    const paymentRoot = doc.querySelector("#__next > div > div > div > div > div > div")
      || doc.querySelector("#__next")
      || [...doc.querySelectorAll("#orderDetails .a-box, #orderDetails .a-row")]
        .find(node => /payment method|mode de paiement/i.test(node.textContent || ""));
    const paymentText = clean(paymentRoot?.innerText || paymentRoot?.textContent || "");
    const embeddedPaymentMatch = html.match(/"paymentMethodHeader":"([^"]+)".{0,800}?"lastDigits":"(\\d{4})"/is);
    const compactPaymentMatch = paymentText.match(/\\b(Visa|Mastercard|MasterCard|American Express|Amex)[^\\d]{0,40}[\\u2022*xX-]{2,}\\s*(\\d{4})\\b/i);
    const paymentMatch = compactPaymentMatch
      || paymentText.match(/\\b(Visa|Mastercard|MasterCard|American Express|Amex)[^\\n]{0,100}(?:ending in|ending with|last digits?|se terminant par)\\s*[*xX-]*\\s*(\\d{4})\\b/i)
      || paymentText.match(/(?:ending in|ending with|last digits?|se terminant par)\\s*[*xX-]*\\s*(\\d{4})\\b/i);
    let paymentNetwork = "";
    let paymentLast4 = "";
    if (embeddedPaymentMatch) {
      paymentNetwork = embeddedPaymentMatch[1].match(/Visa|Mastercard|MasterCard|American Express|Amex/i)?.[0] || "";
      paymentLast4 = embeddedPaymentMatch[2] || "";
    } else if (paymentMatch) {
      if (paymentMatch.length >= 3) {
        paymentNetwork = paymentMatch[1] || "";
        paymentLast4 = paymentMatch[2] || "";
      } else {
        paymentLast4 = paymentMatch[1] || "";
      }
    }

    const cardItemRows = ${JSON.stringify(cardItems)};
    const itemMap = new Map(cardItemRows.map(item => [item.asin, item]));
    const knownAsins = new Set(cardItemRows.map(item => item.asin).filter(Boolean));
    const exactItemsRoot = doc.querySelector("#orderDetails > div > div.a-cardui > div > div:nth-child(1) > div:nth-child(8) > div > div > div > div");
    const semanticItemsRoot = [...doc.querySelectorAll("#orderDetails .a-cardui, #orderDetails .a-row")]
      .find(node => {
        const links = [...node.querySelectorAll("a[href*='/dp/'],a[href*='/gp/product/']")];
        return links.some(link => /ppx_(?:hz)?od_/i.test(link.getAttribute("href") || ""));
      });
    const purchasedItemsRoot = exactItemsRoot || semanticItemsRoot || doc.querySelector("#orderDetails");
    for (const link of purchasedItemsRoot?.querySelectorAll("a[href*='/dp/'],a[href*='/gp/product/']") || []) {
      const href = link.getAttribute("href") || "";
      if (/sspa|sp_csd|adid=/i.test(href)) continue;
      const asin = href.match(/\\/(?:dp|gp\\/product)\\/([A-Z0-9]{10})/i)?.[1]?.toUpperCase();
      if (!asin) continue;
      if (!exactItemsRoot && knownAsins.size && !knownAsins.has(asin)) continue;
      const current = itemMap.get(asin) || {
        asin,
        title: "",
        quantity: 1,
        line_total: null,
        product_url: "https://www.amazon.ca/dp/" + asin
      };
      if (!current.title || current.title === asin) {
        current.title = clean(
          link.textContent
          || link.getAttribute("title")
          || link.getAttribute("aria-label")
          || link.querySelector("img")?.alt
        ) || asin;
      }
      let parent = link.closest(".a-fixed-left-grid-inner, [data-component='purchasedItem'], [class*='shipment-item']");
      if (!parent) parent = link.parentElement;
      for (let depth = 0; depth < 8 && parent; depth += 1, parent = parent.parentElement) {
        const rowAsins = new Set(
          [...parent.querySelectorAll("a[href*='/dp/'],a[href*='/gp/product/']")]
            .map(candidate => (candidate.getAttribute("href") || "").match(/\\/(?:dp|gp\\/product)\\/([A-Z0-9]{10})/i)?.[1]?.toUpperCase())
            .filter(Boolean)
        );
        if (rowAsins.size !== 1 || !rowAsins.has(asin)) continue;
        const quantityMatch = String(parent.textContent || "").match(/(?:Qty|Quantity|Quantite)[:\\s]*(\\d+)/i);
        if (quantityMatch) current.quantity = Number(quantityMatch[1]) || current.quantity;
        const priceElement = parent.querySelector(".a-price .a-offscreen, .a-color-price, [class*='item-price']");
        const unitPrice = amount(priceElement?.textContent || "", null);
        if (unitPrice !== null && unitPrice >= 0) {
          current.line_total = Number((unitPrice * (current.quantity || 1)).toFixed(2));
          break;
        }
      }
      itemMap.set(asin, current);
    }

    const loginRequired = /\\/ap\\/signin/i.test(response.url)
      || /sign in to (?:continue|view|your account)/i.test(text.slice(0, 3000));
    return JSON.stringify({
      http_status: response.status,
      login_required: loginRequired,
      subtotal: Math.abs(signedLineAmount(summaryLines, /item\\(s\\) subtotal|items subtotal|subtotal des articles|sous-total/i)),
      shipping: Math.abs(signedLineAmount(summaryLines, /shipping\\s*(?:&|and)?\\s*handling|shipping|livraison/i)),
      promotions: sumLines(summaryLines, /promotion|coupon|discount|savings|rabais|economies/i),
      tax: sumLines(summaryLines, /tax|gst|hst|pst|qst|tps|tvh|tvq/i, /before tax|avant taxes/i),
      total: Math.abs(signedLineAmount(summaryLines, /grand total|order total|total de la commande|^total$/i)),
      payment_network: clean(paymentNetwork).replace(/^MasterCard$/i, "Mastercard"),
      card_last4: paymentLast4,
      items: [...itemMap.values()],
      selector_sources: {
        summary: summaryRoot?.id === "od-subtotals" || summaryRoot?.closest?.("#od-subtotals")
          ? "od-subtotals"
          : "semantic_fallback",
        payment: paymentRoot?.closest?.("#__next") || paymentRoot?.id === "__next"
          ? "__next"
          : "semantic_fallback",
        items: exactItemsRoot ? "known_path" : (semanticItemsRoot ? "semantic_fallback" : "orderDetails_fallback")
      }
    });
  })()`;
}

async function collectDetail(cdp, policy, order) {
  if (!order.details_url) return {};
  const detail = await policy.retry(
    `fetch Amazon order detail ${order.order_id}`,
    () => evaluateJson(
      cdp,
      detailPageExpression(order.details_url, order.items || []),
      { awaitPromise: true },
    ),
  );
  await policy.handleHttpStatus(detail.http_status, "Amazon order detail");
  if (detail.login_required) {
    await policy.recordLoginRequired("Amazon order detail requires login.");
    throw new CollectionSessionError("amazon_ca: the dedicated browser session requires login.");
  }
  return detail;
}

function normalizedOrder(card, detail) {
  const amounts = reconcileAmounts({
    total: detail.total || card.total,
    subtotal: detail.subtotal,
    tax: detail.tax,
    shipping: detail.shipping,
    promotions: detail.promotions,
  });
  const items = (detail.items || card.items || [])
    .map((item) => ({
      asin: cleanText(item.asin).toUpperCase(),
      title: cleanText(item.title),
      quantity: Math.max(1, Number(item.quantity) || 1),
      line_total: item.line_total == null ? null : Number(Number(item.line_total).toFixed(2)),
      price_known: item.line_total != null,
      product_url: cleanText(item.product_url),
    }))
    .filter((item) => (
      item.asin
      && item.title
      && !/Amazon(?:\.ca)? (?:Rewards )?(?:Visa|Mastercard)/i.test(item.title)
    ));
  const itemPriceTotal = items.reduce((sum, item) => sum + Number(item.line_total || 0), 0);
  const itemPricesReconcile = items.length > 0
    && items.every((item) => Number(item.line_total) > 0)
    && Math.abs(itemPriceTotal - amounts.subtotal) <= 0.02;
  return {
    date: parseAmazonDate(card.date) || card.date,
    platform: "amazon_ca",
    order_id: extractAmazonOrderId(card.order_id),
    merchant: "Amazon",
    status: cleanText(card.status),
    fulfillment: normalizeFulfillment(card.status),
    category: "general_retail",
    item: items.map((item) => item.title).join("; "),
    quantity: String(items.reduce((sum, item) => sum + item.quantity, 0) || ""),
    item_total: amounts.subtotal.toFixed(2),
    tax: amounts.tax.toFixed(2),
    fees: amounts.fees.toFixed(2),
    tip: "0.00",
    total: amounts.total.toFixed(2),
    currency: "CAD",
    source_url: card.details_url,
    notes: [
      `${items.length} sanitized Amazon item line(s).`,
      detail.subtotal ? "Order summary parsed." : "Order total used as reconciliation fallback.",
      itemPricesReconcile
        ? "Item prices are purchase-time values reconciled to the order subtotal."
        : "One or more item prices could not be reconciled to the order subtotal.",
      detail.selector_sources
        ? `selectors=${JSON.stringify(detail.selector_sources)}`
        : "",
    ].filter(Boolean).join(" "),
    items,
    payment: {
      payment_method: detail.payment_network ? "card" : "",
      card_network: cleanText(detail.payment_network),
      card_last4: cleanText(detail.card_last4),
    },
  };
}

function evidenceForOrder(order, capturedAt) {
  const payment = order.payment || {};
  const found = Boolean(payment.card_last4);
  return {
    platform: "amazon_ca",
    order_id: order.order_id,
    source: "amazon_ca_payment_dom",
    payment_method: payment.payment_method || "",
    card_network: payment.card_network || "",
    card_last4: payment.card_last4 || "",
    status: found ? "confirmed" : "not_found",
    evidence_snippet: found
      ? `${payment.card_network || "card"} ending ${payment.card_last4}`
      : "",
    page_url: order.source_url || "",
    captured_at: capturedAt,
  };
}

function evidenceKey(row) {
  return `${row.platform || ""}|${row.order_id || ""}|${row.source || ""}`;
}

async function writeEvidence(outputPath, orders, capturedAt) {
  const existing = await readJson(outputPath, { evidence: [] });
  const merged = new Map();
  for (const row of existing.evidence || []) merged.set(evidenceKey(row), row);
  for (const order of orders) {
    const row = evidenceForOrder(order, capturedAt);
    merged.set(evidenceKey(row), row);
  }
  await mkdir(path.dirname(outputPath), { recursive: true });
  await atomicWriteFile(outputPath, `${JSON.stringify({
    generated_at: capturedAt,
    evidence: [...merged.values()],
  }, null, 2)}\n`);
}

function initialHistoryUrl() {
  return "https://www.amazon.ca/gp/css/order-history";
}

async function main(args, policy) {
  const tabs = await policy.retry("list browser tabs", () => listTabs(args.port));
  const page = tabs
    .filter((tab) => tab.type === "page" && tab.webSocketDebuggerUrl)
    .find((tab) => {
      try {
        return /(^|\.)amazon\.ca$/i.test(new URL(tab.url).hostname);
      } catch {
        return false;
      }
    });
  if (!page) throw new Error("No Amazon.ca tab is open in the dedicated shop browser.");

  const cdp = await policy.retry(
    "connect Amazon tab",
    () => connectCdp(page.webSocketDebuggerUrl, {
      callTimeoutMs: policy.config.cdp_call_timeout_ms,
    }),
  );

  try {
    await cdp.call("Page.enable");
    let pageUrl = initialHistoryUrl();
    const cardsById = new Map();

    for (let pageNumber = 0; pageNumber < args.maxPages; pageNumber += 1) {
      const pageData = await navigate(cdp, policy, pageUrl);
      for (const card of pageData.orders || []) {
        const id = extractAmazonOrderId(card.order_id);
        if (!id || !card.date || card.date < args.minDate) continue;
        cardsById.set(id, { ...card, order_id: id });
        if (cardsById.size >= args.maxOrders) break;
      }
      if (cardsById.size >= args.maxOrders || !pageData.next_url) break;
      const pageDates = (pageData.orders || []).map((order) => order.date).filter(Boolean);
      if (pageDates.length && pageDates.every((date) => date < args.minDate)) break;
      pageUrl = pageData.next_url;
    }

    const cards = [...cardsById.values()].slice(0, args.maxOrders);
    const existing = await readJson(args.out, { orders: [] });
    const { collected, reused } = await collectAmazonDetails(cards, existing.orders || [], async card => {
      const detail = await collectDetail(cdp, policy, card);
      const order = normalizedOrder(card, detail);
      await policy.pace(0.5);
      if (!order.order_id || !order.date || order.date < args.minDate || !Number(order.total)) return null;
      if (Number(detail.subtotal) > 0) {
        order.detail_cache = {
          version: 1, fingerprint: amazonCardFingerprint(card), checked_at: new Date().toISOString(),
        };
      }
      return order;
    }, { refreshAll: args.refreshAll });
    const merged = mergeAmazonOrders(existing.orders || [], collected);
    const generatedAt = new Date().toISOString();
    await mkdir(path.dirname(args.out), { recursive: true });
    await atomicWriteFile(args.out, `${JSON.stringify({
      platform: "amazon_ca",
      generated_at: generatedAt,
      min_date: args.minDate,
      orders: merged,
    }, null, 2)}\n`);
    await writeEvidence(args.evidenceOut, collected, generatedAt);

    const total = collected.reduce((sum, order) => sum + Number(order.total || 0), 0);
    console.log(
      `Fetched ${collected.length} Amazon.ca order(s), CAD ${total.toFixed(2)}; reused=${reused}; cached=${merged.length}.`,
    );
    return {
      observed_dates: observedDates(cards),
      scanned: cards.length,
      imported: collected.length,
      detail_requests: cards.length - reused,
      reused,
      cached: merged.length,
    };
  } finally {
    cdp.close();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const args = parseArgs(process.argv.slice(2));
  runWithCollectionPolicy(
    "amazon_ca",
    (policy) => main(args, policy),
    { summary: (result) => result },
  ).catch((error) => {
    console.error(error.message);
    process.exit(1);
  });
}
