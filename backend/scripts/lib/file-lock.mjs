import { mkdir, open, rm, stat } from "node:fs/promises";
import path from "node:path";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Cross-process lock for a read-modify-write of a shared file (several collectors can
// run at once). The lock is a sibling file created exclusively; a lock older than
// staleMs is left over from a crashed process and is removed.
export async function withFileLock(target, action, { timeoutMs = 15000, staleMs = 30000, pollMs = 50 } = {}) {
  const lockPath = `${target}.lock`;
  await mkdir(path.dirname(path.resolve(lockPath)), { recursive: true });
  const deadline = Date.now() + timeoutMs;
  let handle = null;
  while (!handle) {
    try {
      handle = await open(lockPath, "wx");
      await handle.writeFile(`${process.pid}\n`);
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
      try {
        const info = await stat(lockPath);
        if (Date.now() - info.mtimeMs > staleMs) {
          await rm(lockPath, { force: true });
          continue;
        }
      } catch (statError) {
        if (statError.code === "ENOENT") continue;
        throw statError;
      }
      if (Date.now() > deadline) throw new Error(`Timed out waiting for lock ${lockPath}`);
      await sleep(pollMs);
    }
  }
  try {
    return await action();
  } finally {
    await handle.close().catch(() => {});
    await rm(lockPath, { force: true }).catch(() => {});
  }
}
