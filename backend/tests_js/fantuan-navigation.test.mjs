import assert from "node:assert/strict";
import test from "node:test";

import {
  canRecoverFantuanNavigation,
  isFantuanBaseState,
  isFantuanDocumentReady,
} from "../scripts/collectors/fantuan-navigation.mjs";

test("Fantuan reuses an already rendered base page", () => {
  const state = {
    url: "https://www.fantuanorder.com/zh-CN",
    readyState: "interactive",
  };
  assert.equal(isFantuanBaseState(state), true);
  assert.equal(isFantuanDocumentReady(state), true);
});

test("Fantuan recovers only a Page.navigate timeout that reached the target", () => {
  const timeout = new Error(
    "Timed out calling Chrome DevTools method Page.navigate",
  );
  assert.equal(
    canRecoverFantuanNavigation(timeout, {
      url: "https://www.fantuanorder.com/zh-CN",
    }),
    true,
  );
  assert.equal(
    canRecoverFantuanNavigation(timeout, { url: "https://example.com/" }),
    false,
  );
  assert.equal(
    canRecoverFantuanNavigation(new Error("Schema mismatch"), {
      url: "https://www.fantuanorder.com/zh-CN",
    }),
    false,
  );
});

test("Fantuan rejects lookalike hosts and non-HTTPS pages", () => {
  assert.equal(
    isFantuanBaseState({ url: "https://fantuanorder.com.example/zh-CN" }),
    false,
  );
  assert.equal(
    isFantuanBaseState({ url: "http://www.fantuanorder.com/zh-CN" }),
    false,
  );
});

test("Fantuan returns from an existing detail page before discovering active cards", () => {
  assert.equal(isFantuanDocumentReady({url:"https://www.fantuanorder.com/zh-CN/order-detail/fixture",readyState:"complete"}), false);
  assert.equal(isFantuanDocumentReady({url:"https://www.fantuanorder.com/zh-CN/",readyState:"complete"}), true);
});
