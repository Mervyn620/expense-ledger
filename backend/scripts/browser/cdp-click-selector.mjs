import { connectCdp, listTabs } from "./cdp-client.mjs";

function parseArgs(argv) {
  const args = { port: 9223, match: "", selector: "", waitMs: 2500 };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--port") args.port = Number(argv[++i]);
    else if (arg === "--match") args.match = argv[++i] ?? "";
    else if (arg === "--selector") args.selector = argv[++i] ?? "";
    else if (arg === "--wait-ms") args.waitMs = Number(argv[++i]);
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.selector) throw new Error("--selector is required");
  const tabs = await listTabs(args.port);
  const page = tabs
    .filter((tab) => tab.type === "page" && tab.webSocketDebuggerUrl && /^https?:\/\//.test(tab.url))
    .find((tab) => `${tab.title}\n${tab.url}`.toLowerCase().includes(args.match.toLowerCase()));
  if (!page) throw new Error(`No page matched "${args.match}"`);

  const cdp = await connectCdp(page.webSocketDebuggerUrl);
  const result = await cdp.call("Runtime.evaluate", {
    expression: `JSON.stringify((() => {
      const target = document.querySelector(${JSON.stringify(args.selector)});
      if (!target) return { clicked: false, reason: "selector_not_found" };
      target.scrollIntoView({ block: "center", inline: "center" });
      target.focus();
      target.click();
      return {
        clicked: true,
        tag: target.tagName,
        role: target.getAttribute("role") || "",
        aria: target.getAttribute("aria-label") || "",
      };
    })())`,
    returnByValue: true,
  });
  await new Promise((resolve) => setTimeout(resolve, args.waitMs));
  cdp.close();
  const value = JSON.parse(result.result?.value || "{}");
  if (!value.clicked) throw new Error(`Could not click ${args.selector}: ${value.reason}`);
  console.log(JSON.stringify({ selector: args.selector, page: page.url, ...value }, null, 2));
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
