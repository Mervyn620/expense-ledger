import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { connectCdp, listTabs, sleep } from "../browser/cdp-client.mjs";
import {
  CollectionSessionError,
  observedDates,
  runWithCollectionPolicy,
} from "../browser/collection-policy.mjs";
import { atomicWriteFile } from "../lib/atomic-file.mjs";
import {
  normalizeScotiaTransaction,
  scotiaPeriodMatches,
} from "./scotia-transaction-utils.mjs";

function parseArgs(argv) {
  const args = {
    port: 9223,
    minDate: "2026-05-01",
    out: "data/scotia_transactions.json",
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--port") args.port = Number(argv[++i]);
    else if (arg === "--min-date") args.minDate = argv[++i] ?? args.minDate;
    else if (arg === "--out") args.out = argv[++i] ?? args.out;
  }
  return args;
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

function summarizeWaitValue(value) {
  if (!value || typeof value !== "object") return value;
  const summary = {};
  for (const key of ["url", "value", "firstDate", "loading", "hasFilter", "hasAccount"]) {
    if (Object.hasOwn(value, key)) summary[key] = key === "url" ? "[url]" : value[key];
  }
  if (Object.hasOwn(value, "text")) {
    summary.text = `[text length=${String(value.text || "").length}]`;
  }
  return summary;
}

async function waitFor(cdp, expression, predicate, timeoutMs = 12000) {
  const startedAt = Date.now();
  let value = null;
  while (Date.now() - startedAt < timeoutMs) {
    value = await evaluateJson(cdp, expression);
    if (predicate(value)) return value;
    await sleep(400);
  }
  throw new Error(`Timed out waiting for Scotiabank page state: ${JSON.stringify(summarizeWaitValue(value))}`);
}

async function findScotiaPage(port) {
  const tabs = await listTabs(port);
  return tabs
    .filter((tab) => tab.type === "page" && tab.webSocketDebuggerUrl)
    .find((tab) => /^https:\/\/secure\.scotiabank\.com\//.test(tab.url));
}

async function ensureAccountDetails(cdp, policy) {
  const state = await evaluateJson(cdp, `({
    url: location.href,
    hasFilter: Boolean(document.querySelector("#transaction-filter__search-filter")),
    hasAccount: Boolean(document.querySelector("#summaryAccountTitle-basic"))
  })`);
  if (state.hasFilter) return;
  if (!state.hasAccount) {
    await policy.recordLoginRequired(
      "Scotiabank account control was not found.",
    );
    throw new CollectionSessionError(
      "Scotiabank login or account selection is required in the dedicated browser.",
    );
  }
  await policy.pace();
  await cdp.call("Runtime.evaluate", {
    expression: `(() => {
      const target = document.querySelector("#summaryAccountTitle-basic");
      target.scrollIntoView({ block: "center" });
      target.click();
    })()`,
    returnByValue: true,
  });
  await waitFor(
    cdp,
    `({
      url: location.href,
      hasFilter: Boolean(document.querySelector("#transaction-filter__search-filter"))
    })`,
    (value) => value.hasFilter,
  );
  await policy.settle();
}

async function filterOptions(cdp) {
  return evaluateJson(cdp, `(() => {
    const select = document.querySelector("#transaction-filter__search-filter");
    if (!select) return [];
    return [...select.options].map((option) => ({
      label: String(option.textContent || "").replace(/\\s+/g, " ").trim(),
      value: option.value,
      selected: option.selected
    }));
  })()`);
}

function urlMatchesFilter(url, value) {
  if (!/^\d{4}-\d{2}-01$/.test(value)) return value === "currentStatementPeriod";
  const encoded = encodeURIComponent(value);
  const text = String(url || "");
  return text.includes(`filter-by=${encoded}`) || text.includes(`start-date=${encoded}`);
}

function hasTransactionRows(text) {
  return /Pos Purchase|Withdrawal|Service Charge|Correction|Deposit|Credit Memo/i.test(String(text || ""));
}

function selectedFilterLooksReady(state, value) {
  return state.value === value
    && !state.loading
    && String(state.text || "").length > 20
    && (
      scotiaPeriodMatches(state.firstDate, value)
      || urlMatchesFilter(state.url, value)
      || hasTransactionRows(state.text)
    );
}

async function selectFilter(cdp, policy, value) {
  const before = await evaluateJson(cdp, `({
    url: location.href,
    value: document.querySelector("#transaction-filter__search-filter")?.value || "",
    text: document.querySelector("#table-transactionTable")?.innerText || "",
    firstDate: document.querySelector("#table-transactionTable tbody tr td, #table-transactionTable [role='row'] [role='cell']")?.innerText || ""
  })`);
  if (selectedFilterLooksReady(before, value)) {
    return;
  }
  if (before.value === value) {
    await policy.pace();
    await evaluateJson(cdp, `(() => {
      const select = document.querySelector("#transaction-filter__search-filter");
      const alternate = [...select.options].find((item) =>
        item.value !== ${JSON.stringify(value)}
        && item.value !== "filtersSeparator"
        && item.value !== "customFilters"
      );
      if (!alternate) return { changed: false };
      const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
      if (setter) setter.call(select, alternate.value);
      else select.value = alternate.value;
      select.dispatchEvent(new Event("input", { bubbles: true }));
      select.dispatchEvent(new Event("change", { bubbles: true }));
      return { changed: true, value: alternate.value };
    })()`);
    await policy.settle();
  }
  await policy.pace();
  const changed = await evaluateJson(cdp, `(() => {
    const select = document.querySelector("#transaction-filter__search-filter");
    if (!select) return { changed: false, reason: "filter_not_found" };
    const option = [...select.options].find((item) => item.value === ${JSON.stringify(value)});
    if (!option) return { changed: false, reason: "option_not_found" };
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set;
    if (setter) setter.call(select, option.value);
    else select.value = option.value;
    select.dispatchEvent(new Event("input", { bubbles: true }));
    select.dispatchEvent(new Event("change", { bubbles: true }));
    return { changed: true, value: select.value };
  })()`);
  if (!changed.changed) throw new Error(`Could not select Scotia filter ${value}: ${changed.reason}`);

  await waitFor(
    cdp,
    `({
      url: location.href,
      value: document.querySelector("#transaction-filter__search-filter")?.value || "",
      text: document.querySelector("#table-transactionTable")?.innerText || "",
      firstDate: document.querySelector("#table-transactionTable tbody tr td, #table-transactionTable [role='row'] [role='cell']")?.innerText || "",
      loading: Boolean(document.querySelector("[aria-busy='true'], [data-testid*='loading']"))
    })`,
    (state) => selectedFilterLooksReady(state, value),
    15000,
  );
  await policy.settle(0.75);
}

async function extractRows(cdp, period) {
  return evaluateJson(cdp, `(() => {
    const clean = (value) => String(value || "").replace(/\\s+/g, " ").trim();
    const parseAmount = (value) => {
      const text = clean(value);
      const match = text.match(/-?\\$\\s*([0-9,]+(?:\\.\\d{2})?)/);
      if (!match) return null;
      const amount = Number(match[1].replace(/,/g, ""));
      return text.startsWith("-") ? -amount : amount;
    };
    const redact = (value) => clean(value)
      .replace(/\\b(?:\\d[ -]*?){8,19}\\b/g, "[number]");
    const table = document.querySelector("#table-transactionTable, #table-transaction-table");
    if (!table) return [];
    const rows = [...table.querySelectorAll("tbody tr, [role='row']")];
    return rows.map((row) => {
      const cells = [...row.querySelectorAll("th,td,[role='cell'],[role='columnheader']")]
        .map((cell) => clean(cell.innerText || cell.textContent || ""));
      if (cells.length < 3 || /^date$/i.test(cells[0])) return null;
      const amounts = cells.slice(2).map(parseAmount).filter((value) => value !== null);
      const transactionAmount = amounts[0] ?? null;
      return {
        period: ${JSON.stringify(period)},
        posted_date_text: cells[0],
        description: redact(cells[1]).slice(0, 180),
        amount: transactionAmount,
      };
    }).filter((row) => row && row.amount !== null);
  })()`);
}

async function readExisting(outputPath) {
  try {
    const payload = JSON.parse(await readFile(outputPath, "utf8"));
    return Array.isArray(payload.transactions) ? payload.transactions : [];
  } catch {
    return [];
  }
}

async function main(args, policy) {
  const page = await policy.retry(
    "find Scotiabank tab",
    () => findScotiaPage(args.port),
  );
  if (!page) {
    await policy.recordLoginRequired("No logged-in Scotiabank page was found.");
    throw new CollectionSessionError(
      "No logged-in Scotiabank page found in the dedicated browser.",
    );
  }
  const cdp = await policy.retry(
    "connect Scotiabank tab",
    () => connectCdp(page.webSocketDebuggerUrl, {
      callTimeoutMs: policy.config.cdp_call_timeout_ms,
    }),
  );
  try {
    await cdp.call("Page.enable");
    await policy.inspectPage(cdp);
    await ensureAccountDetails(cdp, policy);

    const options = await filterOptions(cdp);
    const currentMonth = new Date().toISOString().slice(0, 7);
    const targets = options.filter((option) => {
      if (option.value === "currentStatementPeriod") return currentMonth >= args.minDate.slice(0, 7);
      return /^\d{4}-\d{2}-01$/.test(option.value) && option.value >= args.minDate.slice(0, 7) + "-01";
    });

    const rows = [];
    for (const target of targets.reverse()) {
      await selectFilter(cdp, policy, target.value);
      rows.push(...await extractRows(cdp, target.value));
    }

    const merged = new Map();
    for (const row of rows) {
      const normalized = normalizeScotiaTransaction(row);
      if (!normalized || normalized.date < args.minDate) continue;
      merged.set(normalized.transaction_id, normalized);
    }

    const fetchedTransactions = [...merged.values()];
    const existing = await readExisting(args.out);
    for (const row of existing) {
      if (row.transaction_id) merged.set(row.transaction_id, row);
    }
    for (const row of fetchedTransactions) {
      merged.set(row.transaction_id, row);
    }
    const transactions = [...merged.values()].sort((a, b) =>
      `${a.date}|${a.description}|${a.amount}`.localeCompare(`${b.date}|${b.description}|${b.amount}`),
    );
    await mkdir(path.dirname(args.out), { recursive: true });
    await atomicWriteFile(args.out, `${JSON.stringify({
      generated_at: new Date().toISOString(),
      min_date: args.minDate,
      account_id: "scotia_chequing_3333",
      transactions,
    }, null, 2)}\n`);

    const debits = transactions.filter((row) => row.direction === "debit");
    const debitTotal = debits.reduce((sum, row) => sum + Number(row.amount), 0);
    console.log(
      `Fetched ${fetchedTransactions.length} sanitized Scotia transaction(s); `
      + `cached=${transactions.length} debits=${debits.length} debit_total=${debitTotal.toFixed(2)}`,
    );
    return {
      observed_dates: observedDates(fetchedTransactions),
      fetched: fetchedTransactions.length,
      cached: transactions.length,
    };
  } finally {
    cdp.close();
  }
}

const args = parseArgs(process.argv.slice(2));
runWithCollectionPolicy(
  "scotia",
  (policy) => main(args, policy),
  { summary: (result) => result },
).catch((error) => {
  console.error(error.message);
  process.exit(1);
});
