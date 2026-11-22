import { connectCdp, listTabs } from "./cdp-client.mjs";

function parseArgs(argv) {
  const args = { port: 9223, match: "", text: "", waitMs: 3000 };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--port") args.port = Number(argv[++i]);
    else if (arg === "--match") args.match = argv[++i] ?? "";
    else if (arg === "--text") args.text = argv[++i] ?? "";
    else if (arg === "--wait-ms") args.waitMs = Number(argv[++i]);
  }
  return args;
}

const args = parseArgs(process.argv.slice(2));
if (!args.text) {
  console.error("--text is required");
  process.exit(1);
}

const tabs = await listTabs(args.port);
const page = tabs
  .filter((tab) => tab.type === "page" && tab.webSocketDebuggerUrl)
  .find((tab) => `${tab.title}\n${tab.url}`.toLowerCase().includes(args.match.toLowerCase()));

if (!page) {
  console.error(`No page matched "${args.match}"`);
  process.exit(1);
}

const cdp = await connectCdp(page.webSocketDebuggerUrl);
const result = await cdp.call("Runtime.evaluate", {
  expression: `(() => {
    const needle = ${JSON.stringify(args.text)};
    const visible = (el) => {
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
    };
    const candidates = Array.from(document.querySelectorAll('button, a, [role="button"], [tabindex]'))
      .filter((el) => visible(el) && (el.innerText || el.textContent || "").includes(needle));
    if (!candidates.length) return { clicked: false, reason: "not found" };
    candidates.sort((a, b) => {
      const at = (a.innerText || a.textContent || "").trim();
      const bt = (b.innerText || b.textContent || "").trim();
      const exactA = at === needle ? 0 : 1;
      const exactB = bt === needle ? 0 : 1;
      return exactA - exactB || at.length - bt.length;
    });
    const target = candidates[0];
    target.scrollIntoView({ block: "center", inline: "center" });
    const rect = target.getBoundingClientRect();
    target.focus();
    target.click();
    return {
      clicked: true,
      text: (target.innerText || target.textContent || "").trim().slice(0, 120),
      tag: target.tagName,
      aria: target.getAttribute("aria-label") || "",
      className: String(target.className || "").slice(0, 160),
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
    };
  })()`,
  returnByValue: true,
});
const value = result?.result?.value;
if (!value?.x || !value?.y) {
  cdp.close();
  console.error(`Could not find clickable text "${args.text}"`);
  process.exit(1);
}
if (!value.clicked) {
  await cdp.call("Input.dispatchMouseEvent", { type: "mouseMoved", x: value.x, y: value.y });
  await cdp.call("Input.dispatchMouseEvent", { type: "mousePressed", x: value.x, y: value.y, button: "left", clickCount: 1 });
  await cdp.call("Input.dispatchMouseEvent", { type: "mouseReleased", x: value.x, y: value.y, button: "left", clickCount: 1 });
}
await new Promise((resolve) => setTimeout(resolve, args.waitMs));
cdp.close();
console.log(JSON.stringify({
  action: "clicked",
  text: args.text,
  page: page.url,
  tag: value.tag,
  aria: value.aria,
  className: value.className,
  x: value.x,
  y: value.y,
}, null, 2));
