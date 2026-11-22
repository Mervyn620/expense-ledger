import { pathToFileURL } from "node:url";

import {
  connectCdp,
  fetchJson,
  listTabs,
  sleep,
} from "../browser/cdp-client.mjs";

export const REQUEST_CONTRACT = "expense-price-watch-local-request/v1";
export const RESULT_CONTRACT = "expense-price-watch/v1";
export const MY_ITEMS_URL = "https://www.walmart.ca/en/my-items";

function integer(value, field, minimum, maximum) {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${field} must be an integer from ${minimum} to ${maximum}`);
  }
  return value;
}

export function normalizeRequest(payload) {
  if (!payload || payload.contract !== REQUEST_CONTRACT) {
    throw new Error("Unsupported local price-watch request");
  }
  const storeId = String(payload.store_id || "").trim();
  const storeName = String(payload.store_name || "").replace(/\s+/g, " ").trim();
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(storeId) || !storeName) {
    throw new Error("Local price-watch store configuration is invalid");
  }
  return {
    contract: REQUEST_CONTRACT,
    port: integer(payload.port ?? 9223, "port", 1024, 65535),
    store_id: storeId,
    store_name: storeName,
    settle_ms: integer(payload.settle_ms ?? 8000, "settle_ms", 3000, 30000),
    max_scrolls: integer(payload.max_scrolls ?? 12, "max_scrolls", 1, 30),
  };
}

export function parseMoneyToken(value) {
  const token = String(value || "").replace(/\s+/g, " ").trim();
  const cents = token.match(/^([0-9]{1,3})\s*¢$/);
  if (cents) return Math.round(Number(cents[1])) / 100;
  const dollars = token.match(
    /^\$\s*([0-9]{1,6}(?:,[0-9]{3})*)(?:[.\s]([0-9]{2}))?$/,
  );
  if (!dollars) return null;
  return Math.round((
    Number(dollars[1].replace(/,/g, "")) * 100
    + Number(dollars[2] || "0")
  )) / 100;
}

export function parseMyItemsText(value) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  const moneySource = "\\$\\s*[0-9]{1,6}(?:,[0-9]{3})*(?:[.\\s][0-9]{2})?|[0-9]{1,3}\\s*¢";
  const was = text.match(new RegExp(`\\bwas\\s+(${moneySource})`, "i"));
  if (!was || was.index === undefined) return null;
  const beforeWas = text.slice(0, was.index).trim();
  const currentMatches = [...beforeWas.matchAll(new RegExp(moneySource, "gi"))];
  const current = currentMatches.at(-1);
  if (!current || current.index === undefined) return null;
  const currentPrice = parseMoneyToken(current[0]);
  const regularPrice = parseMoneyToken(was[1]);
  const sourceName = beforeWas
    .slice(0, current.index)
    .replace(/\b(?:current price is|now)\s*$/i, "")
    .replace(/[,\s·-]+$/g, "")
    .trim();
  if (
    !sourceName
    || currentPrice === null
    || regularPrice === null
    || currentPrice >= regularPrice
  ) {
    return null;
  }
  return {
    source_name: sourceName.slice(0, 300),
    current_price: currentPrice,
    regular_price: regularPrice,
    on_sale: true,
  };
}

export function parseMyItemsRows(values) {
  const rows = new Map();
  values.forEach((value) => {
    const parsed = parseMyItemsText(value);
    if (!parsed) return;
    const key = parsed.source_name.toLocaleLowerCase("en-CA");
    rows.set(key, parsed);
  });
  return [...rows.values()].sort((left, right) => (
    left.source_name.localeCompare(right.source_name, "en-CA")
  ));
}

export function myItemsExpression(expectedStore) {
  return `(() => {
    const clean = (value) => String(value || "").replace(/\\s+/g, " ").trim();
    const parseMoneyToken = ${parseMoneyToken.toString()};
    const parseMyItemsText = ${parseMyItemsText.toString()};
    const parseMyItemsRows = ${parseMyItemsRows.toString()};
    const text = clean(document.body?.innerText);
    const root = document.querySelector("#maincontent > main > section:nth-child(2)")
      || document.querySelector("#maincontent")
      || document.body;
    const anchors = [...root.querySelectorAll("a")];
    const labels = anchors.map((anchor) => {
      const spans = [...anchor.querySelectorAll("span")]
        .map((span) => clean(span.innerText || span.textContent))
        .filter(Boolean);
      return spans.find((value) => /\\bwas\\b/i.test(value))
        || clean(anchor.innerText || anchor.textContent);
    }).filter(Boolean);
    const itemLabels = labels.filter((value) => /(?:\\$|¢)/.test(value));
    return {
      blocked: /verify your identity|real shoppers, not robots|press and hold|access denied/i.test(text)
        || /\\/blocked(?:[/?#]|$)/i.test(location.href),
      loginRequired: !/\\/en\\/my-items(?:[/?#]|$)/i.test(location.href)
        || /\\b(?:sign in|log in)\\b/i.test(document.title),
      storeMatched: text.toUpperCase().includes(
        ${JSON.stringify(expectedStore.toUpperCase())}
      ),
      itemCount: itemLabels.length,
      deals: parseMyItemsRows(itemLabels),
      url: location.href,
    };
  })()`;
}

export function selectWalmartTarget(tabs) {
  const pages = tabs.filter((tab) => (
    tab?.type === "page" && tab.webSocketDebuggerUrl
  ));
  return pages.find((tab) => tab.url === MY_ITEMS_URL)
    || pages.find((tab) => {
      try {
        const hostname = new URL(tab.url).hostname.toLowerCase();
        return hostname === "walmart.ca" || hostname.endsWith(".walmart.ca");
      } catch {
        return false;
      }
    });
}

async function pageTarget(port) {
  const tabs = await listTabs(port);
  const existing = selectWalmartTarget(tabs);
  if (existing) return existing;
  return fetchJson(
    `http://127.0.0.1:${port}/json/new?${encodeURIComponent(MY_ITEMS_URL)}`,
    { method: "PUT" },
  );
}

async function scrollUntilStable(cdp, maximum) {
  let previousCount = -1;
  let stable = 0;
  for (let index = 0; index < maximum; index += 1) {
    const state = await cdp.call("Runtime.evaluate", {
      expression: `(() => {
        const root = document.querySelector("#maincontent > main > section:nth-child(2)")
          || document.querySelector("#maincontent")
          || document.body;
        const count = [...root.querySelectorAll("a")].filter((anchor) => (
          /(?:\\$|¢)/.test(anchor.innerText || anchor.textContent || "")
        )).length;
        window.scrollTo(0, document.body.scrollHeight);
        return { count, atBottom: window.innerHeight + window.scrollY >= document.body.scrollHeight - 8 };
      })()`,
      returnByValue: true,
    });
    const count = Number(state.result?.value?.count || 0);
    stable = count === previousCount ? stable + 1 : 0;
    previousCount = count;
    if (stable >= 2 && state.result?.value?.atBottom) break;
    await sleep(800);
  }
  await cdp.call("Runtime.evaluate", {
    expression: "window.scrollTo(0, 0)",
    returnByValue: true,
  });
}

export async function collectLocalPrices(request) {
  const normalized = normalizeRequest(request);
  const target = await pageTarget(normalized.port);
  const cdp = await connectCdp(
    target.webSocketDebuggerUrl,
    { callTimeoutMs: 60000 },
  );
  try {
    await cdp.call("Page.enable");
    await cdp.call("Runtime.enable");
    if (target.url !== MY_ITEMS_URL) {
      const navigation = await cdp.call("Page.navigate", { url: MY_ITEMS_URL });
      if (navigation.errorText) throw new Error("My Items navigation failed");
    }
    await sleep(normalized.settle_ms);
    await scrollUntilStable(cdp, normalized.max_scrolls);
    const evaluated = await cdp.call("Runtime.evaluate", {
      expression: myItemsExpression(normalized.store_name),
      returnByValue: true,
    });
    const facts = evaluated.result?.value;
    if (!facts || typeof facts !== "object") {
      throw new Error("My Items page returned no price facts");
    }
    let collectorStatus = "completed";
    if (facts.blocked) collectorStatus = "blocked";
    else if (facts.loginRequired) collectorStatus = "login_required";
    else if (!facts.storeMatched) collectorStatus = "wrong_store";
    else if (!Number.isInteger(facts.itemCount) || facts.itemCount < 1) {
      collectorStatus = "failed";
    }
    const checkedAt = new Date().toISOString();
    const deals = collectorStatus === "completed"
      ? facts.deals.map((row) => ({ ...row, checked_at: checkedAt }))
      : [];
    return {
      contract: RESULT_CONTRACT,
      generated_at: checkedAt,
      cursor: "",
      store_id: normalized.store_id,
      route: "local",
      collector_status: collectorStatus,
      checked: collectorStatus === "completed" ? facts.itemCount : 0,
      deals,
      checks: [],
    };
  } finally {
    cdp.close();
  }
}

async function main() {
  let input = "";
  process.stdin.setEncoding("utf8");
  for await (const chunk of process.stdin) input += chunk;
  const result = await collectLocalPrices(JSON.parse(input));
  process.stdout.write(`${JSON.stringify(result)}\n`);
}

if (
  process.argv[1]
  && import.meta.url === pathToFileURL(process.argv[1]).href
) {
  main().catch((error) => {
    process.stderr.write(`${String(error?.message || error).slice(0, 240)}\n`);
    process.exitCode = 1;
  });
}
