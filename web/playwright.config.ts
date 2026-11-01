import { defineConfig, devices } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

/* The browser tests drive the built app against the real server, on a database
   with weeks of history (tests/e2e/demo.py): the shapes a chairman actually
   sees, not an empty install. One seed per run — it is a whole portfolio worked
   by the scripted crew, so it costs a couple of minutes — and every spec acts on
   its own project, so they cannot trip over each other.

     npm run build && npx playwright test        (from web/)

   YAAF_PYTHON picks the interpreter that has yaaf installed (default python3). */

const PORT = Number(process.env.YAAF_E2E_PORT ?? 8765);
const python = process.env.YAAF_PYTHON ?? "python3";
// Evaluated once more in each worker; only the runner's copy starts a server.
const dir = mkdtempSync(join(tmpdir(), "yaaf-e2e-"));

export default defineConfig({
  testDir: "e2e",
  // The specs share one seeded database. Running them one at a time keeps a
  // spec's "what the desk says now" from racing another spec's decision.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "sign-in", testMatch: /sign-in\.setup\.ts/ },
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], storageState: "e2e/.auth/chairman.json" },
      dependencies: ["sign-in"],
    },
  ],
  webServer: {
    command: `${python} ../tests/e2e/demo.py ${dir} --serve=${PORT}`,
    url: `http://127.0.0.1:${PORT}/healthz`,
    timeout: 600_000,
    reuseExistingServer: false,
    stdout: "ignore",
    stderr: process.env.CI ? "pipe" : "ignore",
  },
});
