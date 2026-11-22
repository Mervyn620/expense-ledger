import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { connectCdp, fetchJson, listTabs } from "../browser/cdp-client.mjs";
import {
  CollectionSessionError,
  runWithCollectionPolicy,
} from "../browser/collection-policy.mjs";
import { atomicWriteFile } from "../lib/atomic-file.mjs";
import { parseCsv } from "./csv.mjs";

function parseArgs(argv) {
  const args = {
    port: 9223,
    out: "data/uber_eats_receipts.json",
    minDate: "2026-05-01",
    maxReceipts: 12,
    refreshAll: false,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--port") args.port = Number(argv[++i]);
    else if (arg === "--out") args.out = argv[++i] ?? args.out;
    else if (arg === "--min-date") args.minDate = argv[++i] ?? args.minDate;
    else if (arg === "--max-receipts") args.maxReceipts = Number(argv[++i]);
    else if (arg === "--refresh-all") args.refreshAll = true;
  }

  return args;
}

async function readExisting(outputPath) {
  try {
    const payload = JSON.parse(await readFile(outputPath, "utf8"));
    return Array.isArray(payload.receipts) ? payload.receipts : [];
  } catch {
    return [];
  }
}

function receiptKey(receipt) {
  return receipt.order_id || receipt.workflowUuid || receipt.workflow_uuid || "";
}

function hasStablePayment(receipt) {
  return Boolean(receipt.card_last4) && receipt.ok !== false && !receipt.login_likely;
}

async function readUberTransactions(minDate, maxReceipts, existing, refreshAll) {
  const completed = new Set(existing.filter(hasStablePayment).map(receiptKey));
  const text = await readFile("data/transactions.csv", "utf8");
  return parseCsv(text)
    .filter((row) => row.platform === "uber_eats" && row.order_id && (!row.date || row.date >= minDate))
    .filter((row) => refreshAll || !completed.has(row.order_id))
    .slice(0, maxReceipts)
    .map((row) => ({
      workflowUuid: row.order_id,
      order_id: row.order_id,
      date: row.date || "",
      merchant: row.merchant || "",
      total: row.total || "",
      currency: row.currency || "CAD",
    }));
}

function receiptExpression(orders, policyConfig) {
  return `(async () => JSON.stringify(await (async () => {
    const orders = ${JSON.stringify(orders)};
    const requestDelayMin = ${Math.round(policyConfig.min_action_delay_ms)};
    const requestDelayMax = ${Math.round(policyConfig.max_action_delay_ms)};
    const paymentNeedles = /visa|master\\s*card|mastercard|amex|american express|card|payment|paid|ending|last\\s*4|last four|\\*{2,}|\\u2022{2,}|\\u652f\\u4ed8|\\u4ed8\\u6b3e|\\u5c3e\\u53f7|1111|2222/i;
    const cardNumberPattern = /\\b(?:\\d[ -]*?){13,19}\\b/g;
    const emailPattern = /[A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,}/gi;
    const phonePattern = /\\b(?:\\+?1[-.\\s]?)?(?:\\(?\\d{3}\\)?[-.\\s]?)\\d{3}[-.\\s]?\\d{4}\\b/g;
    const addressPattern = /\\b\\d{1,6}\\s+[A-Za-z0-9.' -]+\\s+(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Cres|Crescent|Blvd|Boulevard|Way|Lane|Ln)\\b[^,\\n]*/gi;

    const normalize = (value) => String(value || "")
      .replace(/<script[\\s\\S]*?<\\/script>/gi, " ")
      .replace(/<style[\\s\\S]*?<\\/style>/gi, " ")
      .replace(/<[^>]+>/g, " ")
      .replace(/&nbsp;/gi, " ")
      .replace(/&amp;/gi, "&")
      .replace(/&quot;/gi, '"')
      .replace(/&#39;/gi, "'")
      .replace(/\\u00a0/g, " ")
      .replace(/\\s+/g, " ")
      .trim();

    const redact = (value) => normalize(value)
      .replace(emailPattern, "[email]")
      .replace(phonePattern, "[phone]")
      .replace(addressPattern, "[address]")
      .replace(cardNumberPattern, "[card]");

    const shortSnippet = (value) => {
      const text = redact(value);
      if (!text) return "";
      const index = text.search(paymentNeedles);
      const start = index < 0 ? 0 : Math.max(0, index - 80);
      return text.slice(start, start + 220);
    };

    const collectStrings = (value, output = [], depth = 0) => {
      if (depth > 8 || output.length > 500) return output;
      if (typeof value === "string") {
        const cleaned = normalize(value);
        if (paymentNeedles.test(cleaned)) output.push(cleaned);
        return output;
      }
      if (Array.isArray(value)) {
        for (const item of value) collectStrings(item, output, depth + 1);
        return output;
      }
      if (value && typeof value === "object") {
        for (const [key, item] of Object.entries(value)) {
          if (paymentNeedles.test(key)) output.push(key + ": " + normalize(String(item)));
          collectStrings(item, output, depth + 1);
        }
      }
      return output;
    };

    const extractCard = (strings) => {
      const joined = strings.map(redact).join(" | ");
      const network = /master\\s*card|mastercard/i.test(joined)
        ? "Mastercard"
        : /visa/i.test(joined)
          ? "Visa"
          : /amex|american express/i.test(joined)
            ? "Amex"
            : "";
      const patterns = [
        /(?:visa|master\\s*card|mastercard|amex|american express)[^0-9]{0,40}(\\d{4})/i,
        /(?:ending(?:\\s+in)?|last\\s*4|last four|\\u5c3e\\u53f7)[^0-9]{0,40}(\\d{4})/i,
        /(?:\\*{2,}|\\u2022{2,}|x{2,})[^0-9]{0,20}(\\d{4})/i,
      ];
      for (const pattern of patterns) {
        const match = joined.match(pattern);
        if (match) return { network, last4: match[1] };
      }
      if (network) {
        const loose = joined.match(/\\b(1111|2222)\\b/);
        if (loose) return { network, last4: loose[1] };
      }
      return { network, last4: "" };
    };

    const parseReceipt = async (order) => {
      try {
        const response = await fetch("/_p/api/getReceiptByWorkflowUuidV1", {
          method: "POST",
          credentials: "include",
          headers: {
            "content-type": "application/json",
            "x-csrf-token": "x",
          },
          body: JSON.stringify({
            contentType: "WEB_HTML",
            workflowUuid: order.workflowUuid,
            timestamp: null,
          }),
        });
        const text = await response.text();
        let payload = null;
        try {
          payload = JSON.parse(text);
        } catch {
          payload = { text };
        }

        const strings = collectStrings(payload);
        const snippets = [...new Set(strings.map(shortSnippet).filter(Boolean))].slice(0, 10);
        const card = extractCard(snippets);
        const loginLikely = /login|sign in|auth\\.uber\\.com/i.test(location.href + " " + text);

        return {
          ...order,
          ok: response.ok,
          http_status: response.status,
          login_likely: loginLikely,
          card_network: card.network,
          card_last4: card.last4,
          payment_snippets: snippets,
        };
      } catch (error) {
        return {
          ...order,
          ok: false,
          http_status: "",
          login_likely: /login|auth\\.uber\\.com/i.test(location.href),
          card_network: "",
          card_last4: "",
          payment_snippets: [],
          error: String(error?.message || error).slice(0, 180),
        };
      }
    };

    const receipts = [];
    for (const order of orders) {
      receipts.push(await parseReceipt(order));
      const delay = Math.round(requestDelayMin + Math.random() * Math.max(0, requestDelayMax - requestDelayMin));
      await new Promise((resolve) => setTimeout(resolve, delay));
    }

    return {
      platform: "uber_eats",
      generated_at: new Date().toISOString(),
      page_url: location.href,
      receipts,
    };
  })()))()`;
}

async function findOrOpenUberPage(port) {
  const tabs = await listTabs(port);
  const pages = tabs.filter((tab) => tab.type === "page" && tab.webSocketDebuggerUrl);
  const existing = pages.find((tab) => /^https:\/\/www\.ubereats\.com\//.test(tab.url));
  if (existing) return existing;
  return fetchJson(`http://127.0.0.1:${port}/json/new?${encodeURIComponent("https://www.ubereats.com/orders")}`, {
    method: "PUT",
  });
}

async function main(args, policy) {
  const existing = await readExisting(args.out);
  const orders = await readUberTransactions(args.minDate, args.maxReceipts, existing, args.refreshAll);
  if (!orders.length) {
    console.log(`No missing Uber Eats receipt payments found since ${args.minDate}. cached=${existing.length}`);
    return { requested: 0, cached: existing.length };
  }

  const page = await policy.retry(
    "find Uber Eats tab",
    () => findOrOpenUberPage(args.port),
  );
  const cdp = await policy.retry(
    "connect Uber Eats tab",
    () => connectCdp(page.webSocketDebuggerUrl, {
      callTimeoutMs: Math.max(
        policy.config.cdp_call_timeout_ms,
        orders.length * policy.config.max_action_delay_ms + 20000,
      ),
    }),
  );
  try {
    await cdp.call("Page.enable");

    if (!/^https:\/\/www\.ubereats\.com\//.test(page.url)) {
      await policy.pace();
      await cdp.call("Page.navigate", { url: "https://www.ubereats.com/orders" });
      await policy.settle(2);
    }
    await policy.inspectPage(cdp);
    await policy.pace();

    const result = await cdp.call("Runtime.evaluate", {
      expression: receiptExpression(orders, policy.config),
      awaitPromise: true,
      returnByValue: true,
    });

    if (result.exceptionDetails) {
      throw new Error(JSON.stringify(result.exceptionDetails.exception?.description || result.exceptionDetails));
    }

    const payload = JSON.parse(result.result?.value || "{}");
    for (const receipt of payload.receipts || []) {
      if (receipt.http_status) {
        await policy.handleHttpStatus(receipt.http_status, "Uber Eats receipt");
      }
    }
    if ((payload.receipts || []).some((receipt) => receipt.login_likely)) {
      await policy.recordLoginRequired("Uber Eats receipt page requires login.");
      throw new CollectionSessionError("Uber Eats login is required in the dedicated browser.");
    }

    payload.min_date = args.minDate;
    const merged = new Map();
    for (const receipt of existing) merged.set(receiptKey(receipt), receipt);
    for (const receipt of payload.receipts || []) {
      const key = receiptKey(receipt);
      const previous = merged.get(key);
      if (previous && hasStablePayment(previous) && !hasStablePayment(receipt)) continue;
      merged.set(key, receipt);
    }
    payload.receipts = [...merged.values()].filter((receipt) => receiptKey(receipt));

    await mkdir(path.dirname(args.out), { recursive: true });
    await atomicWriteFile(args.out, `${JSON.stringify(payload, null, 2)}\n`);

    const confirmed = (payload.receipts || []).filter((receipt) => receipt.card_last4).length;
    const blocked = (payload.receipts || []).filter((receipt) => receipt.login_likely || !receipt.ok).length;
    console.log(`Fetched ${orders.length} Uber Eats receipt(s); cached=${payload.receipts.length} cards_found=${confirmed} blocked_or_failed=${blocked}`);
    return {
      requested: orders.length,
      cached: payload.receipts.length,
      confirmed,
      failed: blocked,
    };
  } finally {
    cdp.close();
  }
}

const args = parseArgs(process.argv.slice(2));
runWithCollectionPolicy(
  "uber_eats",
  (policy) => main(args, policy),
  { summary: (result) => result },
).catch((error) => {
  console.error(error.message);
  process.exit(1);
});
