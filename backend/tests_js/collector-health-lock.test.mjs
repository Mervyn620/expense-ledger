import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fork } from "node:child_process";
import { fileURLToPath } from "node:url";

import { createCollectionPolicy } from "../scripts/browser/collection-policy.mjs";
import { withFileLock } from "../scripts/lib/file-lock.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));

test("concurrent platform updates in one process all survive", async () => {
  const dir = await mkdtemp(path.join(tmpdir(), "health-"));
  try {
    const statePath = path.join(dir, "collector-health.json");
    const platforms = ["walmart_ca", "fantuan", "tnt_ca", "amazon_ca", "uber_eats", "the_ordinary_ca"];
    const policies = await Promise.all(platforms.map((platform) =>
      createCollectionPolicy(platform, { statePath, config: {}, sleepFn: async () => {} })));
    await Promise.all(policies.map((policy) => policy.recordBlock("test block")));
    const state = JSON.parse(await readFile(statePath, "utf8"));
    assert.deepEqual(Object.keys(state.platforms).sort(), [...platforms].sort());
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("updates from separate processes all survive", async () => {
  const dir = await mkdtemp(path.join(tmpdir(), "health-"));
  try {
    const statePath = path.join(dir, "collector-health.json");
    const worker = path.join(here, "fixtures", "health-writer.mjs");
    const platforms = ["walmart_ca", "fantuan", "tnt_ca", "amazon_ca"];
    await Promise.all(platforms.map((platform) => new Promise((resolve, reject) => {
      const child = fork(worker, [statePath, platform], { stdio: "ignore" });
      child.on("exit", (code) => (code === 0 ? resolve() : reject(new Error(`${platform} exited ${code}`))));
    })));
    const state = JSON.parse(await readFile(statePath, "utf8"));
    assert.deepEqual(Object.keys(state.platforms).sort(), [...platforms].sort());
    for (const platform of platforms) assert.equal(state.platforms[platform].writes, 20);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("a stale lock left by a crashed process is cleared", async () => {
  const dir = await mkdtemp(path.join(tmpdir(), "health-"));
  try {
    const target = path.join(dir, "state.json");
    await writeFile(`${target}.lock`, "999999\n");
    const { utimes } = await import("node:fs/promises");
    const old = new Date(Date.now() - 60000);
    await utimes(`${target}.lock`, old, old);
    assert.equal(await withFileLock(target, async () => "ran", { timeoutMs: 2000 }), "ran");
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});

test("parallel collection moves every store after the first out of a shared window", async () => {
  const { planOwnWindows } = await import("../scripts/browser/window-plan.mjs");
  const moves = planOwnWindows([
    { platform: "walmart_ca", targetId: "a", windowId: 1 },
    { platform: "fantuan", targetId: "b", windowId: 1 },
    { platform: "tnt_ca", targetId: "c", windowId: 2 },
    { platform: "amazon_ca", targetId: "d", windowId: 1 },
  ]);
  assert.deepEqual(moves.map((move) => move.platform), ["fantuan", "amazon_ca"]);
  assert.deepEqual(planOwnWindows([{ platform: "walmart_ca", targetId: "a", windowId: 1 }]), []);
});
