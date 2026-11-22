import { mkdir } from "node:fs/promises";
import path from "node:path";
import { connectCdp, fetchJson, listTabs } from "./cdp-client.mjs";
import { runWithCollectionPolicy } from "./collection-policy.mjs";
import { browserSanitizerSource } from "./sanitize.mjs";
import { atomicWriteFile } from "../lib/atomic-file.mjs";

function parseArgs(argv) {
  const args = {
    port: 9223,
    match: "walmart",
    url: "",
    waitMs: 3000,
    outDir: "raw",
    platform: "walmart_ca",
    scrollBottom: false,
    scrollCount: 6,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--port") args.port = Number(argv[++i]);
    else if (arg === "--match") args.match = argv[++i] ?? "";
    else if (arg === "--url") args.url = argv[++i] ?? "";
    else if (arg === "--wait-ms") args.waitMs = Number(argv[++i]);
    else if (arg === "--out") args.outDir = argv[++i] ?? "raw";
    else if (arg === "--platform") args.platform = argv[++i] ?? "walmart_ca";
    else if (arg === "--scroll-bottom") args.scrollBottom = true;
    else if (arg === "--scroll-count") args.scrollCount = Number(argv[++i]);
  }

  return args;
}

function slug(value) {
  return String(value || "page")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 90) || "page";
}

const extractionExpression = `(() => {
  ${browserSanitizerSource}

  const lines = (document.body?.innerText || "")
    .split(/\\r?\\n/)
    .map(sanitizeAccountingText)
    .filter(Boolean);

  const links = Array.from(document.querySelectorAll("a[href]")).map((link) => ({
    text: sanitizeAccountingText(link.innerText || link.textContent || ""),
    href: sanitizeAccountingUrl(link.href),
  }));

  const orderIds = [...new Set(
    links.flatMap((link) => {
      const matches = [...link.href.matchAll(/\\/orders\\/(\\d{8,})/g)];
      return matches.map((match) => match[1]);
    })
  )];

  const buttons = Array.from(document.querySelectorAll("button")).map((button) => ({
    text: sanitizeAccountingText(button.innerText || button.textContent || ""),
    aria: sanitizeAccountingText(button.getAttribute("aria-label") || ""),
    testId: button.getAttribute("data-testid") || "",
  })).filter((button) => button.text || button.aria || button.testId);

  const itemCandidates = Array.from(document.querySelectorAll('[data-testid="itemtile-stack"], [data-testid="productName"], .print-items-list, .dn.print-items-list'))
    .map((element) => sanitizeAccountingText(element.innerText || element.textContent || ""))
    .filter(Boolean);

  return {
    title: sanitizeAccountingText(document.title),
    url: sanitizeAccountingUrl(location.href),
    capturedAt: new Date().toISOString(),
    lines,
    links,
    orderIds,
    buttons,
    itemCandidates,
  };
})()`;

async function main(args, policy) {
  let tabs = await policy.retry("list browser tabs", () => listTabs(args.port));
  let pages = tabs.filter((tab) => tab.type === "page" && tab.webSocketDebuggerUrl);
  let page = args.match
    ? pages.find((tab) => `${tab.title}\n${tab.url}`.toLowerCase().includes(args.match.toLowerCase()))
    : pages[0];

  if (!page && args.url) {
    page = await policy.retry(
      "open browser tab",
      () => fetchJson(
        `http://127.0.0.1:${args.port}/json/new?${encodeURIComponent(args.url)}`,
        { method: "PUT" },
      ),
    );
  }
  if (!page) throw new Error(`No page matched "${args.match}"`);

  const cdp = await policy.retry(
    "connect to browser tab",
    () => connectCdp(page.webSocketDebuggerUrl, {
      callTimeoutMs: policy.config.cdp_call_timeout_ms,
    }),
  );
  try {
    await cdp.call("Page.enable");

    if (args.url) {
      await policy.pace();
      await policy.retry(
        "navigate order page",
        () => cdp.call("Page.navigate", { url: args.url }),
      );
      await policy.settle(Math.max(1, args.waitMs / 1500));
      await policy.inspectPage(cdp);
    }

    if (args.scrollBottom) {
      for (let i = 0; i < args.scrollCount; i += 1) {
        await cdp.call("Runtime.evaluate", {
          expression: "window.scrollTo(0, document.body.scrollHeight)",
          returnByValue: true,
        });
        await policy.settle(0.75);
      }
    }

    const result = await cdp.call("Runtime.evaluate", {
      expression: extractionExpression,
      returnByValue: true,
    });
    const snapshot = {
      platform: args.platform,
      ...result.result.value,
    };

    await mkdir(args.outDir, { recursive: true });
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    const host = slug(new URL(snapshot.url).host);
    const order = snapshot.url.match(/\/orders\/(\d{8,})/)?.[1] || "orders";
    const output = path.resolve(args.outDir, `${timestamp}_${args.platform}_${host}_${order}.json`);
    await atomicWriteFile(output, JSON.stringify(snapshot, null, 2));
    console.log(JSON.stringify({
      output,
      title: snapshot.title,
      url: snapshot.url,
      lineCount: snapshot.lines.length,
      orderIds: snapshot.orderIds,
      itemCandidateCount: snapshot.itemCandidates.length,
    }, null, 2));
    return {
      pages: 1,
      lines: snapshot.lines.length,
      orders: snapshot.orderIds.length,
    };
  } finally {
    cdp.close();
  }
}

const args = parseArgs(process.argv.slice(2));
runWithCollectionPolicy(
  args.platform,
  (policy) => main(args, policy),
  {
    summary: (result) => result,
  },
).catch((error) => {
  console.error(error.message);
  process.exit(1);
});
