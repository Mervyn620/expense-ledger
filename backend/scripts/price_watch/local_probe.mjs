import { connectCdp, fetchJson, listTabs, sleep } from "../browser/cdp-client.mjs";
import { browserSanitizerSource } from "../browser/sanitize.mjs";

function parseArgs(argv) {
  const args = {
    port: 9223,
    url: "",
    storeName: "",
    waitMs: 3500,
    inspectStoreDialog: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--port") args.port = Number(argv[++index]);
    else if (arg === "--url") args.url = argv[++index] || "";
    else if (arg === "--store-name") args.storeName = argv[++index] || "";
    else if (arg === "--wait-ms") args.waitMs = Number(argv[++index]);
    else if (arg === "--inspect-store-dialog") args.inspectStoreDialog = true;
  }
  if (!/^https:\/\/www\.walmart\.ca\/.+\/ip\//i.test(args.url)) {
    throw new Error("--url must be a walmart.ca product URL");
  }
  if (!args.storeName.trim()) throw new Error("--store-name is required");
  return args;
}

function canonicalProductUrl(value) {
  const parsed = new URL(value);
  parsed.protocol = "https:";
  parsed.hostname = "www.walmart.ca";
  parsed.search = "";
  parsed.hash = "";
  return parsed.toString().replace(/\/$/, "");
}

function extractionExpression(storeName) {
  return `(() => {
    ${browserSanitizerSource}
    const expectedStore = ${JSON.stringify(storeName)};
    const text = document.body?.innerText || "";
    const mainText = (document.querySelector("main")?.innerText || text)
      .split(/\\b(?:Similar items|Customers also|You may also like|More items to consider)\\b/i, 1)[0];
    const compact = mainText.replace(/\\s+/g, " ").trim();
    const amount = "([0-9]{1,6}(?:,[0-9]{3})*(?:\\\\.[0-9]{1,2})?)";
    const currentMatch = compact.match(new RegExp(
      "(?:current price is|now(?: price)?|sale price)\\\\s*(?:CAD\\\\s*)?\\\\$?\\\\s*" + amount,
      "i"
    ));
    const regularMatch = compact.match(new RegExp(
      "(?:was price|regular price|original price|list price|previous price)\\\\s*(?:CAD\\\\s*)?\\\\$?\\\\s*" + amount,
      "i"
    ));
    const parseMoney = (match) => match ? Number(match[1].replace(/,/g, "")) : null;
    const currentPrice = parseMoney(currentMatch);
    const regularPrice = parseMoney(regularMatch);
    const explicitSale = /\\b(?:rollback|clearance|reduced price|sale price|save \\$)\\b/i.test(compact);
    const storeControls = [...document.querySelectorAll("button, [role=button]")]
      .filter((element) => (
        (element.innerText || element.getAttribute("aria-label") || "")
          .toUpperCase()
          .includes(expectedStore.toUpperCase())
      ))
      .slice(0, 5)
      .map((element) => ({
        tag: element.tagName.toLowerCase(),
        testId: element.getAttribute("data-testid") || "",
        aria: sanitizeAccountingText(element.getAttribute("aria-label") || ""),
        text: sanitizeAccountingText(element.innerText || "").slice(0, 120),
      }));
    return {
      contract: "expense-price-probe/v1",
      url: sanitizeAccountingUrl(location.href, []),
      name: sanitizeAccountingText(document.querySelector("h1")?.innerText || document.title),
      storeMatched: text.toUpperCase().includes(expectedStore.toUpperCase()),
      currentPrice,
      regularPrice,
      onSale: currentPrice !== null && (
        (regularPrice !== null && currentPrice < regularPrice) || explicitSale
      ),
      storeControls,
      blocked: /verify your identity|real shoppers, not robots|press and hold|access denied/i.test(text)
        || /\\/blocked(?:[/?#]|$)/i.test(location.href),
      capturedAt: new Date().toISOString(),
    };
  })()`;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  let tabs = await listTabs(args.port);
  let tab = tabs.find((candidate) => (
    candidate.type === "page"
    && candidate.webSocketDebuggerUrl
    && candidate.url.includes("walmart.ca")
  ));
  if (!tab) {
    tab = await fetchJson(
      `http://127.0.0.1:${args.port}/json/new?${encodeURIComponent(args.url)}`,
      { method: "PUT" },
    );
  }
  const cdp = await connectCdp(tab.webSocketDebuggerUrl);
  try {
    await cdp.call("Page.enable");
    const target = canonicalProductUrl(args.url);
    await cdp.call("Page.navigate", { url: target });
    await sleep(Math.max(500, args.waitMs));
    if (args.inspectStoreDialog) {
      await cdp.call("Runtime.evaluate", {
        expression: `(() => {
          const expected = ${JSON.stringify(args.storeName)}.toUpperCase();
          const control = [...document.querySelectorAll("button")]
            .find((element) => (
              (element.getAttribute("aria-label") || "").toUpperCase()
                .includes(expected)
            ));
          control?.click();
        })()`,
      });
      await sleep(1000);
    }
    const evaluated = await cdp.call("Runtime.evaluate", {
      expression: args.inspectStoreDialog
        ? `(() => {
            ${browserSanitizerSource}
            return {
            inputs: [...document.querySelectorAll("input")].slice(-10).map((element) => ({
              type: element.type || "",
              name: element.name || "",
              testId: element.getAttribute("data-testid") || "",
              aria: sanitizeAccountingText(element.getAttribute("aria-label") || ""),
              placeholder: sanitizeAccountingText(element.getAttribute("placeholder") || ""),
            })),
            pickupStores: [...document.querySelectorAll('input[name="pickup-store"]')]
              .slice(0, 10)
              .map((element) => ({
                aria: sanitizeAccountingText(element.getAttribute("aria-label") || ""),
                value: sanitizeAccountingText(element.value || ""),
                label: sanitizeAccountingText(
                  element.closest("label")?.innerText
                    || element.parentElement?.innerText
                    || ""
                ).slice(0, 180),
              })),
            actions: [...document.querySelectorAll("button")]
              .filter((element) => {
                const text = (element.innerText || "").replace(/\\s+/g, " ").trim();
                const label = element.getAttribute("aria-label") || "";
                return text.length <= 40
                  && label.length <= 80
                  && /search|apply|select|save|close|cancel|current location/i.test(
                    [text, label, element.getAttribute("data-testid") || ""].join(" ")
                  );
              })
              .slice(-20)
              .map((element) => ({
                testId: element.getAttribute("data-testid") || "",
                aria: sanitizeAccountingText(element.getAttribute("aria-label") || ""),
                text: sanitizeAccountingText(element.innerText || "").slice(0, 40),
              })),
            };
          })()`
        : extractionExpression(args.storeName),
      returnByValue: true,
    });
    if (evaluated.exceptionDetails || !evaluated.result?.value) {
      throw new Error("Could not extract the product price from the dedicated browser");
    }
    console.log(JSON.stringify(evaluated.result.value));
  } finally {
    cdp.close();
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
