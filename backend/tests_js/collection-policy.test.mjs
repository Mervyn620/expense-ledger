import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  CollectionBlockedError,
  CollectionSessionError,
  classifyCollectionError,
  classifyPageState,
  createCollectionPolicy,
  isTransientCollectionError,
  runWithCollectionPolicy,
} from "../scripts/browser/collection-policy.mjs";


test("classifies challenge and login pages without guessing from normal headers", () => {
  assert.equal(classifyPageState({
    url: "https://www.walmart.ca/orders",
    title: "Orders",
    text: "Sign in Shop groceries Order history",
  }).status, "ok");
  assert.equal(classifyPageState({
    url: "https://auth.uber.com/login",
    title: "Sign in",
    text: "Sign in to continue",
  }).status, "login_required");
  assert.equal(classifyPageState({
    url: "https://www.example.com/challenge",
    title: "Security challenge",
    text: "Verify you are human",
  }).status, "blocked");
});

test("retries only transient failures with bounded exponential delay", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "expense-policy-"));
  const waits = [];
  let attempts = 0;
  try {
    const policy = await createCollectionPolicy("walmart_ca", {
      root,
      config: {
        min_action_delay_ms: 0,
        max_action_delay_ms: 0,
        max_retries: 2,
        backoff_base_ms: 100,
        backoff_max_ms: 1000,
      },
      randomFn: () => 0.5,
      sleepFn: async (milliseconds) => waits.push(milliseconds),
    });
    const value = await policy.retry("test", async () => {
      attempts += 1;
      if (attempts < 2) throw new Error("Timed out connecting");
      return "ok";
    });

    assert.equal(value, "ok");
    assert.equal(attempts, 2);
    assert.deepEqual(waits, [100]);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("does not retry certificate or TLS protocol failures", () => {
  const certificate = new Error("net::ERR_CERT_DATE_INVALID");
  const handshake = new Error("net::ERR_SSL_PROTOCOL_ERROR");
  const reset = new Error("net::ERR_CONNECTION_RESET");

  assert.deepEqual(classifyCollectionError(certificate), {
    category: "certificate_error",
    transient: false,
  });
  assert.deepEqual(classifyCollectionError(handshake), {
    category: "tls_handshake_failed",
    transient: false,
  });
  assert.equal(isTransientCollectionError(certificate), false);
  assert.equal(isTransientCollectionError(handshake), false);
  assert.equal(isTransientCollectionError(reset), true);
});

test("opens a cooldown circuit for explicit rate limits", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "expense-policy-"));
  const now = Date.parse("2026-06-27T12:00:00Z");
  try {
    const options = {
      root,
      config: { block_cooldown_minutes: 60 },
      nowFn: () => now,
      sleepFn: async () => {},
    };
    const policy = await createCollectionPolicy("uber_eats", options);
    await assert.rejects(
      policy.handleHttpStatus(429, "receipt"),
      CollectionBlockedError,
    );
    await assert.rejects(
      createCollectionPolicy("uber_eats", options),
      CollectionBlockedError,
    );
    const state = JSON.parse(
      await readFile(path.join(root, ".codex", "collector-health.json"), "utf8"),
    );
    assert.equal(state.platforms.uber_eats.status, "blocked");
    assert.equal(
      state.platforms.uber_eats.blocked_until,
      "2026-06-27T13:00:00.000Z",
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("records login requirements without opening a cooldown circuit", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "expense-policy-"));
  try {
    const policy = await createCollectionPolicy("fantuan", {
      root,
      config: {},
      sleepFn: async () => {},
    });
    await assert.rejects(
      policy.handleHttpStatus(401, "order detail"),
      CollectionSessionError,
    );
    const next = await createCollectionPolicy("fantuan", {
      root,
      config: {},
      sleepFn: async () => {},
    });
    assert.equal(next.platform, "fantuan");
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("repeated ordinary failures enter a short maintenance cooldown", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "expense-policy-"));
  try {
    const options = {
      root,
      config: {
        max_consecutive_failures: 2,
        failure_cooldown_minutes: 15,
      },
      sleepFn: async () => {},
    };
    for (let attempt = 0; attempt < 2; attempt += 1) {
      await assert.rejects(
        runWithCollectionPolicy(
          "walmart_ca",
          async () => {
            throw new Error("selector drift");
          },
          options,
        ),
        /selector drift/,
      );
    }
    await assert.rejects(
      createCollectionPolicy("walmart_ca", options),
      CollectionBlockedError,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
