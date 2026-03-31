import fs from "node:fs";
import path from "node:path";

const nextDir = path.resolve(process.cwd(), ".next");

try {
  if (fs.existsSync(nextDir)) {
    fs.rmSync(nextDir, { recursive: true, force: true });
    console.log(`Removed stale Next build directory: ${nextDir}`);
  }
} catch (error) {
  console.warn(`Could not clean ${nextDir}; continuing anyway.`);
  console.warn(error instanceof Error ? error.message : String(error));
}
