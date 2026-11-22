import { connectCdp, listTabs } from "../browser/cdp-client.mjs";


const port = Number(process.argv[2] || 9223);
const tabs = await listTabs(port);
const target = tabs.find((tab) => (
  tab.type === "page"
  && tab.url === "https://www.walmart.ca/en/my-items"
  && tab.webSocketDebuggerUrl
));
if (!target) throw new Error("No Walmart My Items tab is open.");

const cdp = await connectCdp(
  target.webSocketDebuggerUrl,
  { callTimeoutMs: 15000 },
);
try {
  await cdp.call("Runtime.enable");
  const evaluated = await cdp.call("Runtime.evaluate", {
    expression: `(() => {
      const clean = (value) => String(value || "").replace(/\\s+/g, " ").trim();
      const root = document.querySelector("#maincontent > main > section:nth-child(2)")
        || document.querySelector("#maincontent")
        || document.body;
      const anchors = [...root.querySelectorAll("a")];
      const rows = anchors.map((anchor) => {
        const spans = [...anchor.querySelectorAll("span")]
          .map((span) => clean(span.innerText || span.textContent))
          .filter(Boolean);
        return {
          anchorText: clean(anchor.innerText || anchor.textContent),
          aria: clean(anchor.getAttribute("aria-label")),
          spanTexts: spans,
        };
      });
      const priceRows = rows.filter((row) => (
        /(?:\\$|¢)/.test([row.anchorText, row.aria, ...row.spanTexts].join(" "))
      ));
      const saleRows = priceRows.filter((row) => (
        /\\bwas\\b/i.test([row.anchorText, row.aria, ...row.spanTexts].join(" "))
      ));
      const body = clean(document.body?.innerText);
      return {
        url: location.href,
        title: document.title,
        rootFound: root !== document.body,
        anchorCount: anchors.length,
        priceRowCount: priceRows.length,
        saleRowCount: saleRows.length,
        squareOneMatched: body.toUpperCase().includes(
          "MISSISSAUGA (SQUARE ONE), ON"
        ),
        samples: saleRows.slice(0, 8),
      };
    })()`,
    returnByValue: true,
  });
  process.stdout.write(
    `${JSON.stringify(evaluated.result?.value || {}, null, 2)}\n`,
  );
} finally {
  cdp.close();
}
