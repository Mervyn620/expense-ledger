// Child process for collector-health-lock.test.mjs: 20 read-modify-write updates for one platform.
import { withFileLock } from "../../scripts/lib/file-lock.mjs";
import { atomicWriteFile } from "../../scripts/lib/atomic-file.mjs";
import { readFile } from "node:fs/promises";

const [statePath, platform] = process.argv.slice(2);
for (let i = 0; i < 20; i += 1) {
  await withFileLock(statePath, async () => {
    let state = { platforms: {} };
    try { state = JSON.parse(await readFile(statePath, "utf8")); } catch {}
    const row = state.platforms[platform] || { writes: 0 };
    row.writes += 1;
    state.platforms[platform] = row;
    await atomicWriteFile(statePath, JSON.stringify(state));
  });
}
