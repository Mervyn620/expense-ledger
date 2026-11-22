import assert from "node:assert/strict";
import test from "node:test";
import { decideScotiaLoginAction, revealScotiaLoginWindow } from "../scripts/browser/prepare-scotia-login.mjs";


test("submits only when every visible login field is already filled", () => {
  assert.equal(decideScotiaLoginAction({
    usernamePresent: true,
    usernameFilled: true,
    passwordPresent: true,
    passwordFilled: true,
    submitPresent: true,
  }), "submit");

  assert.equal(decideScotiaLoginAction({
    usernamePresent: true,
    usernameFilled: true,
    passwordPresent: true,
    passwordFilled: false,
    submitPresent: true,
  }), "manual_login_required");
});

test("never submits verification or challenge pages", () => {
  assert.equal(decideScotiaLoginAction({
    verification: true,
    usernamePresent: true,
    usernameFilled: true,
    passwordPresent: true,
    passwordFilled: true,
    submitPresent: true,
  }), "verification_required");

  assert.equal(decideScotiaLoginAction({
    blocked: true,
  }), "verification_required");
});

test("recognizes an authenticated Scotia account page", () => {
  assert.equal(decideScotiaLoginAction({
    secureAccount: true,
  }), "logged_in");
});


test("restores the selected minimized Scotia window before activating its tab", async () => {
  const calls = [];
  const cdp = { call: async (method, params) => {
    calls.push([method, params]);
    if (method === "Browser.getWindowForTarget") return { windowId: 17, bounds: { windowState: "minimized" } };
    return {};
  } };
  await revealScotiaLoginWindow(cdp, "scotia-target");
  assert.deepEqual(calls, [
    ["Browser.getWindowForTarget", { targetId: "scotia-target" }],
    ["Browser.setWindowBounds", { windowId: 17, bounds: { windowState: "normal" } }],
    ["Page.bringToFront", undefined],
  ]);
});

for (const windowState of ["maximized", "fullscreen"]) {
  test(`keeps a ${windowState} window's size while activating Scotia`, async () => {
    const calls = [];
    const cdp = { call: async (method, params) => {
      calls.push(method);
      return { windowId: 17, bounds: { windowState } };
    } };
    await revealScotiaLoginWindow(cdp, "scotia-target");
    assert.deepEqual(calls, ["Browser.getWindowForTarget", "Page.bringToFront"]);
  });
}

test("reports an unavailable Scotia window without trying another window", async () => {
  const calls = [];
  const cdp = { call: async (method) => { calls.push(method); return {}; } };
  await assert.rejects(revealScotiaLoginWindow(cdp, "closed-target"), /window was not found/);
  assert.deepEqual(calls, ["Browser.getWindowForTarget"]);
});
