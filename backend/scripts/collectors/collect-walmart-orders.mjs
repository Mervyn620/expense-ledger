import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { connectCdp, fetchJson, listTabs } from "../browser/cdp-client.mjs";
import { navigateToContent, waitForContent } from "../browser/content-ready.mjs";
import { observedDates, runWithCollectionPolicy } from "../browser/collection-policy.mjs";
import { browserSanitizerSource } from "../browser/sanitize.mjs";
import { atomicWriteFile } from "../lib/atomic-file.mjs";
import {
  isCancelledWalmartSnapshot,
  createWalmartHistoryBoundary,
  walmartFullScanDue,
  walmartPageSignature,
  resolveWalmartBackfillOrderId,
  walmartCandidateKey,
  walmartOrderParserSource,
  walmartProductParserSource,
  walmartOrderNeedsRefresh,
} from "./walmart-order-utils.mjs";
import { parseCsv } from "./csv.mjs";

function parseArgs(argv) {
  const args = {
    port: 9223,
    minDate: "2026-05-01",
    maxOrders: 12,
    outDir: "raw",
    fullHistory: false,
    refreshOrders: [],
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--port") args.port = Number(argv[++i]);
    else if (arg === "--min-date") args.minDate = argv[++i] ?? args.minDate;
    else if (arg === "--max-orders") args.maxOrders = Number(argv[++i]);
    else if (arg === "--out") args.outDir = argv[++i] ?? args.outDir;
    else if (arg === "--full-history") args.fullHistory = true;
    else if (arg === "--refresh-order") args.refreshOrders.push(argv[++i] || "");
  }
  if (args.refreshOrders.length > args.maxOrders || args.refreshOrders.some(id => !/^\d{8,}$/.test(id))) {
    throw new Error("Invalid or unbounded Walmart refresh order IDs");
  }
  return args;
}

const PRODUCT_LINK_ATTEMPTS_PATH = ".codex/walmart-product-link-attempts.json";
const HISTORY_SCAN_PATH = ".codex/walmart-history-scan.json";

async function knownOrderState() {
  try {
    const rows = parseCsv(await readFile("data/transactions.csv", "utf8"));
    const known = new Set(
      rows.filter((row) => row.platform === "walmart_ca").map((row) => row.order_id),
    );
    const orders = rows
      .filter((row) => row.platform === "walmart_ca")
      .map((row) => ({
        orderId: row.order_id,
        date: row.date,
        total: row.total,
        fulfillment: row.fulfillment,
      }));
    let linked = new Set();
    try {
      const itemRows = parseCsv(await readFile("data/walmart_items.csv", "utf8"));
      linked = new Set(
        itemRows
          .filter((row) => row.platform === "walmart_ca" && row.product_link)
          .map((row) => row.order_id),
      );
    } catch {
      // A missing item staging file means all known orders are eligible for
      // the bounded product-link backfill.
    }
    return { known, linked, orders };
  } catch {
    return { known: new Set(), linked: new Set(), orders: [] };
  }
}

async function readProductLinkAttempts() {
  try {
    const payload = JSON.parse(await readFile(PRODUCT_LINK_ATTEMPTS_PATH, "utf8"));
    return payload?.orders && typeof payload.orders === "object" ? payload.orders : {};
  } catch {
    return {};
  }
}

async function writeProductLinkAttempts(orders) {
  await atomicWriteFile(PRODUCT_LINK_ATTEMPTS_PATH, `${JSON.stringify({
    contract: "expense-walmart-link-capture/v1",
    updatedAt: new Date().toISOString(),
    orders,
  }, null, 2)}\n`);
}

function needsProductLinkCapture(orderId, orderState, attempts) {
  return Boolean(
    orderId &&
    orderState.known.has(orderId) &&
    !orderState.linked.has(orderId) &&
    !attempts[orderId],
  );
}

async function rememberProductLinkAttempt(attempts, orderId, productLinks) {
  attempts[orderId] = {
    attemptedAt: new Date().toISOString(),
    productLinks,
  };
  await writeProductLinkAttempts(attempts);
}

function candidateIsEligible(candidate, args, orderState, attempts) {
  if (!candidate.date || candidate.date >= args.minDate) return true;
  const known = orderState.orders.find(order => order.orderId === candidate.orderId);
  return walmartOrderNeedsRefresh(candidate, known) || needsProductLinkCapture(candidate.orderId, orderState, attempts);
}

function shouldSkipKnownOrder(orderId, orderState, attempts, candidate) {
  const known = orderState.orders.find(order => order.orderId === orderId);
  return !candidate?.forceRefresh && !walmartOrderNeedsRefresh(candidate, known) &&
    orderState.known.has(orderId) &&
    !needsProductLinkCapture(orderId, orderState, attempts);
}

function captureKind(orderId, orderState) {
  return orderState.known.has(orderId) ? "product-link-backfill" : "new-order";
}

function resolvedCandidateOrderId(candidate, orderState, attempts) {
  if (candidate.orderId) return candidate.orderId;
  const known = resolveWalmartBackfillOrderId(candidate, orderState.orders, new Set());
  if (known) return known;
  const excluded = new Set([
    ...orderState.linked,
    ...Object.keys(attempts),
  ]);
  return resolveWalmartBackfillOrderId(candidate, orderState.orders, excluded);
}

function productLinkCaptureSummary(exported, orderState, attempts) {
  return {
    new_orders: exported.filter((row) => row.kind === "new-order").length,
    refreshed_orders: exported.filter(row => row.kind === "order-refresh").length,
    product_link_backfills: exported.filter(
      (row) => row.kind === "product-link-backfill"
    ).length,
    product_links: exported.reduce((total, row) => total + row.productLinks, 0),
    known_orders: orderState.known.size,
    known_orders_with_links: orderState.linked.size,
    attempted_orders: Object.keys(attempts).length,
  };
}

function collectionResult(
  candidates,
  pages,
  exported,
  skippedCancelled,
  skippedUnresolved,
  orderState,
  attempts,
) {
  return {
    candidates,
    pages,
    exported: exported.length,
    ...productLinkCaptureSummary(exported, orderState, attempts),
    skipped_cancelled: skippedCancelled.length,
    skipped_unresolved: skippedUnresolved.length,
  };
}

async function evaluateJson(cdp, expression) {
  const result = await cdp.call("Runtime.evaluate", {
    expression: `JSON.stringify(${expression})`,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(String(result.exceptionDetails.exception?.description || "page evaluation failed"));
  }
  return JSON.parse(result.result?.value || "{}");
}

async function navigate(cdp, policy, url) {
  const isDetail = /\/orders\/\d{8,}/.test(url);
  return navigateToContent(cdp, policy, url, {
    label: isDetail ? "Walmart order detail" : "Walmart order list",
    expression: isDetail ? `({
      items: [...document.querySelectorAll('[data-testid="itemtile-stack"], [data-testid="productName"], .print-items-list')]
        .map(node => node.innerText || node.textContent || "").filter(text => text.trim()),
      cancelled: (document.body?.innerText || "").split(/\\r?\\n/).some(line => /^cancel(?:led|ed)$/i.test(line.trim()))
    })` : listExpression,
    ready: content => isDetail
      ? Boolean(content?.items?.length || content?.cancelled)
      : Boolean(content?.candidates?.length),
  });
}

const listExpression = `(() => {
  ${walmartOrderParserSource}
  const candidates = [...document.querySelectorAll("button")]
    .filter((button) => cleanWalmartText(button.innerText || button.textContent) === "View details")
    .map((button, index) => {
      let container = button;
      for (let depth = 0; depth < 10 && container; depth += 1, container = container.parentElement) {
        const text = cleanWalmartText(container.innerText || container.textContent);
        if (/Order total\\s*\\$[0-9]/i.test(text)) break;
      }
      container = container || button.parentElement;
      const text = cleanWalmartText(container?.innerText || container?.textContent || "");
      const hrefs = [...(container?.querySelectorAll("a[href]") || [])].map((link) => link.href);
      const parsed = parseWalmartOrderCandidate({
        text,
        hrefs,
        aria: button.getAttribute("aria-label") || "",
      });
      return { index, ...parsed };
    });
  return { url: location.href, candidates };
})()`;

const nextPageExpression = `(() => {
  const button = [...document.querySelectorAll("button")]
    .find((candidate) => /next page/i.test(candidate.getAttribute("aria-label") || ""));
  if (!button || button.disabled || button.getAttribute("aria-disabled") === "true") {
    return { clicked: false };
  }
  button.scrollIntoView({ block: "center" });
  button.click();
  return { clicked: true };
})()`;

const detailExpression = `(() => {
  ${browserSanitizerSource}
  ${walmartProductParserSource}
  const lines = (document.body?.innerText || "").split(/\\r?\\n/).map(sanitizeAccountingText).filter(Boolean);
  const links = [...document.querySelectorAll("a[href]")].map((link) => ({
    text: sanitizeAccountingText(link.innerText || link.textContent || ""),
    href: sanitizeAccountingUrl(link.href),
  }));
  const orderIds = [...new Set(links.flatMap((link) =>
    [...link.href.matchAll(/\\/orders\\/(\\d{8,})/g)].map((match) => match[1])
  ))];
  const itemElements = [...document.querySelectorAll(
    '[data-testid="itemtile-stack"], [data-testid="productName"], .print-items-list, .dn.print-items-list'
  )];
  const itemCandidates = [...new Set(itemElements
    .map((element) => sanitizeAccountingText(element.innerText || element.textContent || ""))
    .filter(Boolean))];
  const productLinks = [];
  const seenProductLinks = new Set();
  for (const element of itemElements) {
    const container = element.closest('[data-testid="itemtile-stack"], .print-items-list, .dn.print-items-list') || element;
    const itemCandidate = sanitizeAccountingText(container.innerText || container.textContent || "");
    const anchors = [
      ...(container.matches("a[href]") ? [container] : []),
      ...container.querySelectorAll("a[href]"),
    ];
    for (const anchor of anchors) {
      const href = canonicalizeWalmartProductUrl(anchor.href);
      if (!href) continue;
      const productName = sanitizeAccountingText(
        anchor.innerText ||
        anchor.textContent ||
        anchor.getAttribute("aria-label") ||
        anchor.getAttribute("title") ||
        ""
      );
      const key = [href, productName, itemCandidate].join("\\n");
      if (seenProductLinks.has(key)) continue;
      seenProductLinks.add(key);
      productLinks.push({ productName, productUrl: href, itemCandidate });
    }
  }
  return {
    platform: "walmart_ca",
    title: sanitizeAccountingText(document.title),
    url: sanitizeAccountingUrl(location.href),
    capturedAt: new Date().toISOString(),
    lines,
    links,
    orderIds,
    buttons: [],
    itemCandidates,
    productLinks,
  };
})()`;

async function findOrOpenWalmartPage(port) {
  const tabs = await listTabs(port);
  const page = tabs
    .filter((tab) => tab.type === "page" && tab.webSocketDebuggerUrl)
    .find((tab) => /^https:\/\/www\.walmart\.ca\//.test(tab.url));
  if (page) return page;
  return fetchJson(
    `http://127.0.0.1:${port}/json/new?${encodeURIComponent("https://www.walmart.ca/orders")}`,
    { method: "PUT" },
  );
}

export async function collectCandidates(cdp, policy, boundary, wait = waitForContent) {
  const unique = [];
  const seen = new Set();
  const visitedPages = new Set();
  let stopReason = "page_limit";
  let page = await evaluateJson(cdp, listExpression);

  for (let pageNumber = 1; pageNumber <= 50; pageNumber += 1) {
    const signature = walmartPageSignature(page);
    if (!page?.url || visitedPages.has(signature)) throw new Error("Walmart pagination repeated an order page.");
    visitedPages.add(signature);

    for (const candidate of page.candidates || []) {
      const enriched = { ...candidate, listUrl: page.url, pageNumber };
      const key = walmartCandidateKey(enriched);
      if (seen.has(key)) continue;
      seen.add(key);
      unique.push(enriched);
    }

    if (boundary(page)) {
      stopReason = "date_boundary";
      break;
    }
    await policy.pace();
    const next = await evaluateJson(cdp, nextPageExpression);
    if (!next.clicked) {
      stopReason = "history_end";
      break;
    }
    const waitNext = () => wait({
      label: "next Walmart order page",
      read: () => evaluateJson(cdp, listExpression),
      inspect: () => policy.inspectPage(cdp),
      ready: value => Boolean(value?.candidates?.length) && walmartPageSignature(value) !== signature,
      signature: walmartPageSignature,
      timeoutMs: 12000,
    });
    try {
      page = await waitNext();
    } catch (error) {
      if (error.message !== "Timed out waiting for next Walmart order page.") throw error;
      const current = await evaluateJson(cdp, listExpression);
      await policy.inspectPage(cdp);
      if (current?.candidates?.length && walmartPageSignature(current) !== signature) {
        page = current;
      } else if (!current?.candidates?.length) {
        // The next page is still loading. Waiting again avoids skipping it.
        page = await waitNext();
      } else {
        // A changed URL may represent an unparsed page: never click past it.
        if (current.url !== page.url) throw error;
        await policy.pace();
        const retry = await evaluateJson(cdp, nextPageExpression);
        if (!retry.clicked) throw error;
        page = await waitNext();
      }
    }
  }

  return { candidates: unique, pages: visitedPages.size, stopReason };
}

async function revealChargeHistory(cdp, policy) {
  const result = await evaluateJson(cdp, `(() => {
    const button = document.querySelector('[data-testid="charge-history-cta"]');
    if (!button) return { clicked: false };
    button.scrollIntoView({ block: "center" });
    button.click();
    return { clicked: true };
  })()`);
  if (result.clicked) await policy.settle(0.75);
  return result.clicked;
}

async function writeSnapshot(outDir, snapshot, orderId) {
  await mkdir(outDir, { recursive: true });
  const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
  const output = path.resolve(outDir, `${timestamp}_walmart_ca_www-walmart-ca_${orderId}.json`);
  await atomicWriteFile(output, `${JSON.stringify(snapshot, null, 2)}\n`);
  return output;
}

async function main(args, policy) {
  const orderState = await knownOrderState();
  const productLinkAttempts = await readProductLinkAttempts();
  let lastFullScan;
  try { lastFullScan = JSON.parse(await readFile(HISTORY_SCAN_PATH, "utf8")).completed_at; } catch { /* First run scans all pages. */ }
  const fullHistory = args.fullHistory || walmartFullScanDue(lastFullScan);
  const boundary = createWalmartHistoryBoundary({
    minDate: args.minDate,
    knownOrders: orderState.orders,
    backfillIds: [...orderState.known].filter(id => needsProductLinkCapture(id, orderState, productLinkAttempts)
      || walmartOrderNeedsRefresh(undefined, orderState.orders.find(order => order.orderId === id))),
    fullHistory,
  });
  const page = await policy.retry(
    "find Walmart tab",
    () => findOrOpenWalmartPage(args.port),
  );
  const cdp = await policy.retry(
    "connect Walmart tab",
    () => connectCdp(page.webSocketDebuggerUrl, {
      callTimeoutMs: policy.config.cdp_call_timeout_ms,
    }),
  );
  try {
    await cdp.call("Page.enable");

    await navigate(cdp, policy, "https://www.walmart.ca/orders");
    await cdp.call("Runtime.evaluate", {
      expression: "window.scrollTo(0, document.body.scrollHeight)",
      returnByValue: true,
    });
    await waitForContent({
      label: "Walmart order list after scrolling",
      read: () => evaluateJson(cdp, listExpression),
      inspect: () => policy.inspectPage(cdp),
      ready: value => Boolean(value?.candidates?.length),
      signature: walmartPageSignature,
    });
    const collected = await collectCandidates(cdp, policy, boundary);
    const candidates = collected.candidates;
    if (!candidates.length) {
      throw new Error("No Walmart order detail buttons found; the page layout may have changed.");
    }

    const unique = args.refreshOrders.map(orderId => ({orderId, forceRefresh:true}));
    const seen = new Set(args.refreshOrders);
    for (const candidate of candidates) {
      if (!candidateIsEligible(candidate, args, orderState, productLinkAttempts)) continue;
      const resolvedOrderId = resolvedCandidateOrderId(
        candidate,
        orderState,
        productLinkAttempts,
      );
      const key = resolvedOrderId || `${candidate.aria}|${candidate.total}`;
      if (seen.has(key)) continue;
      seen.add(key);
      unique.push({ ...candidate, resolvedOrderId });
    }

    const exported = [];
    const skippedCancelled = [];
    const skippedUnresolved = [];
    for (const candidate of unique) {
      if (exported.length >= args.maxOrders) break;
      const targetOrderId = candidate.orderId || candidate.resolvedOrderId;
      if (
        targetOrderId &&
        shouldSkipKnownOrder(targetOrderId, orderState, productLinkAttempts, candidate)
      ) continue;

      // Multi-fulfillment orders repeat "View details" for individual delivery
      // sections. Those child buttons do not carry a stable order URL; the main
      // order card on the same page does and is the authoritative candidate.
      if (!targetOrderId) {
        skippedUnresolved.push({
          page: candidate.pageNumber,
          date: candidate.date,
          total: candidate.total,
        });
        continue;
      }

      const state = await navigate(
        cdp,
        policy,
        `https://www.walmart.ca/default/orders/${encodeURIComponent(targetOrderId)}`,
      );

      const orderId = state.url.match(/\/orders\/(\d{8,})/)?.[1] || targetOrderId;
      if (
        !orderId ||
        shouldSkipKnownOrder(orderId, orderState, productLinkAttempts, candidate) ||
        exported.some((row) => row.orderId === orderId)
      ) continue;
      await revealChargeHistory(cdp, policy);
      const snapshot = await evaluateJson(cdp, detailExpression);
      if (!snapshot.itemCandidates?.length) {
        if (isCancelledWalmartSnapshot(snapshot)) {
          skippedCancelled.push(orderId);
          continue;
        }
        throw new Error(`Walmart order ${orderId} loaded without item details.`);
      }
      const output = await writeSnapshot(args.outDir, snapshot, orderId);
      await rememberProductLinkAttempt(
        productLinkAttempts,
        orderId,
        snapshot.productLinks?.length || 0,
      );
      exported.push({
        orderId,
        output,
        kind: orderState.known.has(orderId) && (candidate.forceRefresh || walmartOrderNeedsRefresh(candidate, orderState.orders.find(order=>order.orderId===orderId)))
          ? "order-refresh" : captureKind(orderId, orderState),
        itemCandidates: snapshot.itemCandidates.length,
        productLinks: snapshot.productLinks?.length || 0,
      });
    }

    const result = collectionResult(
      unique.length,
      collected.pages,
      exported,
      skippedCancelled,
      skippedUnresolved,
      orderState,
      productLinkAttempts,
    );
    result.observed_dates = observedDates(unique);
    result.stop_reason = collected.stopReason;
    result.full_history = fullHistory;
    if (collected.stopReason === "history_end") {
      await atomicWriteFile(HISTORY_SCAN_PATH, `${JSON.stringify({ completed_at: new Date().toISOString() })}\n`);
    }
    console.log(JSON.stringify({
      minDate: args.minDate,
      ...result,
      exported,
      skippedCancelled,
      skippedUnresolved,
    }, null, 2));
    return result;
  } finally {
    cdp.close();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const args = parseArgs(process.argv.slice(2));
  runWithCollectionPolicy(
    "walmart_ca",
    (policy) => main(args, policy),
    { summary: (result) => result },
  ).catch((error) => {
    console.error(error.message);
    process.exit(1);
  });
}
