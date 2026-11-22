import { mkdir, rename, rm, writeFile } from "node:fs/promises";
import path from "node:path";

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
// Windows refuses to replace a file that another process has open at that instant
// (for example the app reading a status file). Retry briefly instead of failing.
const BUSY = new Set(["EPERM", "EBUSY", "EACCES"]);

export async function atomicWriteFile(destination, content, encoding = "utf8", { attempts = 50, delayMs = 100 } = {}) {
  const resolved = path.resolve(destination);
  await mkdir(path.dirname(resolved), { recursive: true });
  const temporary = path.join(
    path.dirname(resolved),
    `.${path.basename(resolved)}.${process.pid}.${Date.now()}.tmp`,
  );
  try {
    await writeFile(temporary, content, encoding);
    for (let attempt = 1; ; attempt += 1) {
      try {
        await rename(temporary, resolved);
        break;
      } catch (error) {
        if (!BUSY.has(error.code) || attempt >= attempts) throw error;
        await sleep(delayMs);
      }
    }
  } finally {
    await rm(temporary, { force: true }).catch(() => {});
  }
}
