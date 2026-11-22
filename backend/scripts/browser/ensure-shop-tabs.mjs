import { readFile } from "node:fs/promises";
import path from "node:path";
import { connectCdp, fetchJson, listTabs, sleep } from "./cdp-client.mjs";
import { planOwnWindows } from "./window-plan.mjs";

function parseArgs(argv) {
  const args = {
    port: 9223,
    platforms: "walmart_ca,amazon_ca,the_ordinary_ca,uber_eats,fantuan",
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--port") args.port = Number(argv[++i]);
    else if (arg === "--platforms") args.platforms = argv[++i] ?? args.platforms;
    else if (arg === "--separate-windows") args.separateWindows = true;
  }
  return args;
}

async function readPlatforms(root = process.cwd()) {
  const configPath = path.join(root, "config", "platforms.json");
  const payload = JSON.parse(await readFile(configPath, "utf8"));
  const result = {};
  for (const [id, platform] of Object.entries(payload)) {
    const ordersUrl = String(platform?.ordersUrl || "");
    if (!ordersUrl) continue;
    let inferredHost = "";
    try {
      inferredHost = new URL(ordersUrl).host;
    } catch {
      throw new Error(`Invalid ordersUrl for platform ${id}`);
    }
    const hosts = Array.isArray(platform.hosts) && platform.hosts.length
      ? platform.hosts.map((value) => String(value).toLowerCase())
      : [inferredHost.toLowerCase()];
    result[id] = { ordersUrl, hosts };
  }
  return result;
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return `${url.origin}${url.pathname}`;
  } catch {
    return "";
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const platforms = args.platforms.split(",").map((value) => value.trim()).filter(Boolean);
  const platformConfig = await readPlatforms();
  const tabs = await listTabs(args.port);
  const pages = tabs.filter((tab) => tab.type === "page");
  const result = [];

  for (const platform of platforms) {
    const config = platformConfig[platform];
    if (!config) throw new Error(`Unsupported platform: ${platform}`);
    const url = config.ordersUrl;
    const hosts = config.hosts;
    const existing = pages.find((page) => {
      try {
        return hosts.includes(new URL(page.url).host);
      } catch {
        return false;
      }
    });
    if (existing) {
      result.push({ platform, action: "reused", url: safeUrl(existing.url) });
      continue;
    }
    const opened = await fetchJson(
      `http://127.0.0.1:${args.port}/json/new?${encodeURIComponent(url)}`,
      { method: "PUT" },
    );
    result.push({ platform, action: "opened", url: safeUrl(opened.url || url) });
  }

  if (args.separateWindows) await giveEachStoreItsOwnWindow(args.port, platforms, platformConfig, result);
  console.log(JSON.stringify(result, null, 2));
}

function findTab(pages, hosts) {
  return pages.find((page) => {
    try {
      return hosts.includes(new URL(page.url).host);
    } catch {
      return false;
    }
  });
}

async function giveEachStoreItsOwnWindow(port, platforms, platformConfig, result) {
  // Newly opened tabs may need a moment before they report their final URL.
  let pages = [];
  for (let attempt = 0; attempt < 10; attempt += 1) {
    pages = (await listTabs(port)).filter((tab) => tab.type === "page");
    if (platforms.every((platform) => findTab(pages, platformConfig[platform].hosts))) break;
    await sleep(300);
  }
  const version = await fetchJson(`http://127.0.0.1:${port}/json/version`);
  const browser = await connectCdp(version.webSocketDebuggerUrl);
  try {
    const entries = [];
    for (const platform of platforms) {
      const tab = findTab(pages, platformConfig[platform].hosts);
      if (!tab) continue;
      const { windowId } = await browser.call("Browser.getWindowForTarget", { targetId: tab.id });
      entries.push({ platform, targetId: tab.id, windowId });
    }
    for (const move of planOwnWindows(entries)) {
      await browser.call("Target.createTarget", { url: platformConfig[move.platform].ordersUrl, newWindow: true });
      await browser.call("Target.closeTarget", { targetId: move.targetId });
      const row = result.find((item) => item.platform === move.platform);
      if (row) row.window = "moved_to_own_window";
    }
  } finally {
    browser.close();
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
