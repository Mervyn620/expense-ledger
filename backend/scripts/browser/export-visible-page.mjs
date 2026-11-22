import { mkdir } from "node:fs/promises";
import path from "node:path";
import { connectCdp, listTabs } from "./cdp-client.mjs";
import { browserSanitizerSource } from "./sanitize.mjs";
import { atomicWriteFile } from "../lib/atomic-file.mjs";

function parseArgs(argv) {
  const args = {
    port: 9223,
    match: "",
    platform: "unknown",
    outDir: "raw",
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--port") args.port = Number(argv[++i]);
    else if (arg === "--match") args.match = argv[++i] ?? "";
    else if (arg === "--platform") args.platform = argv[++i] ?? "unknown";
    else if (arg === "--out") args.outDir = argv[++i] ?? "raw";
    else if (arg === "--help" || arg === "-h") {
      console.log("Usage: node scripts/browser/export-visible-page.mjs --match walmart --platform walmart_ca [--port 9223]");
      process.exit(0);
    }
  }

  return args;
}

function slug(value) {
  return String(value || "page")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80) || "page";
}

const pageExpression = `(() => {
  ${browserSanitizerSource}

  const text = document.body?.innerText || "";
  const lines = text
    .split(/\\r?\\n/)
    .map(sanitizeAccountingText)
    .filter(Boolean);

  const orderLinePattern = /order|ordered|placed|purchase|receipt|invoice|delivered|pickup|picked up|shipping|subtotal|tax|tip|fee|total|refund|return|item|\\$\\s*\\d|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec/i;
  const orderishLines = lines
    .filter((line) => orderLinePattern.test(line))
    .slice(0, 5000);

  const visibleLinks = Array.from(document.querySelectorAll("a[href]"))
    .map((link) => ({
      text: sanitizeAccountingText(link.innerText || link.textContent || ""),
      href: sanitizeAccountingUrl(link.href),
    }))
    .filter((link) => link.text || /order|receipt|invoice|walmart|amazon|costco|instacart/i.test(link.href))
    .slice(0, 500);

  return {
    title: sanitizeAccountingText(document.title),
    url: sanitizeAccountingUrl(location.href),
    capturedAt: new Date().toISOString(),
    lineCount: lines.length,
    lines,
    orderishLines,
    visibleLinks,
  };
})()`;

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const tabs = await listTabs(args.port);
  const pages = tabs.filter((tab) => tab.type === "page" && tab.webSocketDebuggerUrl);
  if (pages.length === 0) {
    throw new Error("No debuggable Chrome pages found. Start the shop browser first.");
  }

  const match = args.match.toLowerCase();
  const page = match
    ? pages.find((tab) => `${tab.title}\n${tab.url}`.toLowerCase().includes(match))
    : pages[0];

  if (!page) {
    const available = pages.map((tab) => `- ${tab.title} ${tab.url}`).join("\n");
    throw new Error(`No tab matched "${args.match}". Available tabs:\n${available}`);
  }

  const cdp = await connectCdp(page.webSocketDebuggerUrl);
  const result = await cdp.call("Runtime.evaluate", {
    expression: pageExpression,
    returnByValue: true,
    awaitPromise: false,
  });
  cdp.close();

  if (!result?.result?.value) {
    throw new Error("Chrome returned no page data.");
  }

  const snapshot = {
    platform: args.platform,
    ...result.result.value,
  };

  const outDir = path.resolve(args.outDir);
  await mkdir(outDir, { recursive: true });

  const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
  const host = slug(new URL(snapshot.url).host);
  const output = path.join(outDir, `${timestamp}_${args.platform}_${host}.json`);
  await atomicWriteFile(output, JSON.stringify(snapshot, null, 2));

  console.log(JSON.stringify({
    output,
    platform: snapshot.platform,
    title: snapshot.title,
    url: snapshot.url,
    lineCount: snapshot.lineCount,
    orderishLineCount: snapshot.orderishLines.length,
  }, null, 2));
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
