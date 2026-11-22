import { pathToFileURL } from "node:url";
import { connectCdp, listTabs, sleep } from "./cdp-client.mjs";


import { createScotiaLoginWaiter } from "./scotia-login-wait.mjs";

const SCOTIA_HOSTS = new Set([
  "secure.scotiabank.com",
  "auth.scotiaonline.scotiabank.com",
]);

const INSPECT_LOGIN_EXPRESSION = `(() => {
  const visible = (element) => {
    if (!element || element.disabled) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none"
      && style.visibility !== "hidden"
      && rect.width > 0
      && rect.height > 0;
  };
  const inputs = Array.from(document.querySelectorAll("input")).filter(visible);
  const username = inputs.find((input) => {
    const hint = [
      input.type,
      input.name,
      input.id,
      input.autocomplete,
      input.getAttribute("aria-label"),
      input.placeholder,
    ].join(" ").toLowerCase();
    return input.type !== "password"
      && /user|username|card|access|login|email/.test(hint);
  });
  const password = inputs.find((input) =>
    input.type === "password"
      || String(input.autocomplete || "").toLowerCase() === "current-password"
  );
  const form = password?.closest("form") || username?.closest("form") || document;
  const buttons = Array.from(
    form.querySelectorAll('button, input[type="submit"], [role="button"]')
  ).filter(visible);
  const submit = buttons.find((button) => {
    const label = [
      button.innerText,
      button.value,
      button.getAttribute("aria-label"),
      button.getAttribute("title"),
    ].join(" ").replace(/\\s+/g, " ").trim().toLowerCase();
    return /sign in|log in|login|continue|next|submit|登录|登入|继续|下一步/.test(label);
  });
  const text = String(document.body?.innerText || "").slice(0, 5000);
  const normalized = text.toLowerCase();
  const url = location.href;
  const verification = /one[- ]time|verification code|security code|two[- ]step|two[- ]factor|authenticator|验证码|二次验证|安全码/.test(normalized)
    || /\\/(?:verify|verification|mfa|challenge)(?:[/?#]|$)/i.test(url);
  const blocked = /captcha|verify (?:that )?you are human|unusual traffic|access denied|robot check|验证码图片/.test(normalized);
  const accountMarker = document.querySelector(
    '#summaryAccountTitle-basic, [id*="summaryAccountTitle"], [data-testid*="account-summary"]'
  );
  const accountText = /my accounts|account summary|chequing|savings|credit card|账户概览|我的账户/.test(normalized);
  const secureAccount = location.hostname === "secure.scotiabank.com"
    && (Boolean(accountMarker) || accountText)
    && !/sign in|log in|登录|登入/.test(normalized.slice(0, 1200));

  return {
    host: location.hostname,
    path: location.pathname,
    secureAccount,
    verification,
    blocked,
    usernamePresent: Boolean(username),
    usernameFilled: Boolean(username?.value),
    passwordPresent: Boolean(password),
    passwordFilled: Boolean(password?.value),
    submitPresent: Boolean(submit),
  };
})()`;

const FOCUS_LOGIN_EXPRESSION = `(() => {
  const visible = (element) => {
    if (!element || element.disabled) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none"
      && style.visibility !== "hidden"
      && rect.width > 0
      && rect.height > 0;
  };
  const inputs = Array.from(document.querySelectorAll("input")).filter(visible);
  const target = inputs.find((input) => input.type !== "hidden");
  if (!target) return false;
  target.focus();
  target.click();
  return true;
})()`;

const CLICK_SUBMIT_EXPRESSION = `(() => {
  const visible = (element) => {
    if (!element || element.disabled) return false;
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none"
      && style.visibility !== "hidden"
      && rect.width > 0
      && rect.height > 0;
  };
  const inputs = Array.from(document.querySelectorAll("input")).filter(visible);
  const username = inputs.find((input) => {
    const hint = [
      input.type,
      input.name,
      input.id,
      input.autocomplete,
      input.getAttribute("aria-label"),
      input.placeholder,
    ].join(" ").toLowerCase();
    return input.type !== "password"
      && /user|username|card|access|login|email/.test(hint);
  });
  const password = inputs.find((input) =>
    input.type === "password"
      || String(input.autocomplete || "").toLowerCase() === "current-password"
  );
  const form = password?.closest("form") || username?.closest("form") || document;
  const submit = Array.from(
    form.querySelectorAll('button, input[type="submit"], [role="button"]')
  ).filter(visible).find((button) => {
    const label = [
      button.innerText,
      button.value,
      button.getAttribute("aria-label"),
      button.getAttribute("title"),
    ].join(" ").replace(/\\s+/g, " ").trim().toLowerCase();
    return /sign in|log in|login|continue|next|submit|登录|登入|继续|下一步/.test(label);
  });
  if (!submit) return false;
  submit.click();
  return true;
})()`;


export function decideScotiaLoginAction(state) {
  if (state.secureAccount) return "logged_in";
  if (state.verification || state.blocked) return "verification_required";
  const hasFields = state.usernamePresent || state.passwordPresent;
  const usernameReady = !state.usernamePresent || state.usernameFilled;
  const passwordReady = !state.passwordPresent || state.passwordFilled;
  if (hasFields && usernameReady && passwordReady && state.submitPresent) {
    return "submit";
  }
  return "manual_login_required";
}

// A tab activation alone does not restore a minimized/background browser window.
// Resolve only the selected Scotia target; never change unrelated Chrome windows.
export async function revealScotiaLoginWindow(cdp, targetId) {
  const { windowId, bounds } = await cdp.call("Browser.getWindowForTarget", { targetId });
  if (!Number.isInteger(windowId)) throw new Error("Scotiabank browser window was not found.");
  if (bounds?.windowState !== "maximized" && bounds?.windowState !== "fullscreen") {
    await cdp.call("Browser.setWindowBounds", {
      windowId,
      bounds: { windowState: "normal" },
    });
  }
  await cdp.call("Page.bringToFront");
}

function stageSignature(state) {
  return [
    state.host,
    state.path,
    state.usernamePresent,
    state.passwordPresent,
  ].join("|");
}

async function inspect(cdp) {
  const result = await cdp.call("Runtime.evaluate", {
    expression: INSPECT_LOGIN_EXPRESSION,
    returnByValue: true,
  });
  return result.result?.value || {};
}

async function main() {
  const portIndex = process.argv.indexOf("--port");
  const port = portIndex >= 0 ? Number(process.argv[portIndex + 1]) : 9223;
  const tabs = await listTabs(port);
  const page = tabs.find((tab) => {
    try {
      return tab.type === "page"
        && tab.webSocketDebuggerUrl
        && SCOTIA_HOSTS.has(new URL(tab.url).hostname);
    } catch {
      return false;
    }
  });
  if (!page) throw new Error("Scotiabank login page was not found in the dedicated browser.");

  let waiter;
  let closed = false;
  const cdp = await connectCdp(page.webSocketDebuggerUrl, {
    onMessage: message => waiter?.onMessage(message),
    onClose: () => { closed = true; waiter?.onClose(); },
  });
  if (process.argv.includes("--wait-for-login")) {
    try {
      await revealScotiaLoginWindow(cdp, page.id);
      let lastState;
      waiter = createScotiaLoginWaiter(cdp, { onState: state => {
        if (state !== lastState) console.log(`scotia_login_status=${state}`);
        lastState = state;
      } });
      if (closed) waiter.onClose();
      await waiter.wait();
      console.log("scotia_login_status=logged_in");
      return;
    } finally { cdp.close(); }
  }
  let submitted = 0;
  let focused = false;
  let finalStatus = "manual_login_required";
  const submittedStages = new Set();
  try {
    await cdp.call("Page.enable");
    await cdp.call("Runtime.enable");
    await revealScotiaLoginWindow(cdp, page.id);
    await sleep(1800);

    for (let attempt = 0; attempt < 5; attempt += 1) {
      const state = await inspect(cdp);
      const action = decideScotiaLoginAction(state);
      if (action === "logged_in" || action === "verification_required") {
        finalStatus = action;
        break;
      }
      if (action === "submit") {
        const signature = stageSignature(state);
        if (submittedStages.has(signature)) {
          finalStatus = "submitted";
          break;
        }
        submittedStages.add(signature);
        const clicked = await cdp.call("Runtime.evaluate", {
          expression: CLICK_SUBMIT_EXPRESSION,
          returnByValue: true,
        });
        if (!clicked.result?.value) {
          finalStatus = "manual_login_required";
          break;
        }
        submitted += 1;
        finalStatus = "submitted";
        await sleep(2600);
        continue;
      }
      if (!focused && (state.usernamePresent || state.passwordPresent)) {
        focused = true;
        await cdp.call("Runtime.evaluate", {
          expression: FOCUS_LOGIN_EXPRESSION,
          returnByValue: true,
        });
        await sleep(1600);
        continue;
      }
      finalStatus = "manual_login_required";
      break;
    }
  } finally {
    cdp.close();
  }

  console.log(`scotia_login_status=${finalStatus}`);
  console.log(`scotia_login_submitted=${submitted}`);
  console.log("scotia_login_page_open=1");
}

const isMain = process.argv[1]
  && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isMain) {
  main().catch((error) => {
    console.error(error.message);
    process.exit(1);
  });
}
