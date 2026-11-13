import path from "node:path";
import { defineConfig, devices } from "@playwright/test";
import { AUTH_TOKEN } from "./e2e/constants";

/*
 * Opt-in browser flows (`npm run e2e`). They run against tests/e2e_web/fake_api_server.py:
 * the real FastAPI app + the real static export, with every backend dependency faked, so the
 * suite needs no network, model or sidecar. Build the UI first (`npm run build`).
 */
const PORT = Number(process.env.E2E_PORT ?? 18765);
const AUTH_PORT = Number(process.env.E2E_AUTH_PORT ?? 18766);

const repoRoot = path.resolve(__dirname, "..");
const python = process.env.PYTHON ?? path.join(repoRoot, ".venv", "bin", "python");
const server = path.join(repoRoot, "tests", "e2e_web", "fake_api_server.py");

const serverCommand = (port: number, extra = "") => `"${python}" "${server}" --port ${port} ${extra}`.trim();

export default defineConfig({
  testDir: "./e2e",
  // One shared stateful fake backend: keep files strictly sequential.
  fullyParallel: false,
  workers: 1,
  retries: 1,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      testIgnore: /auth\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], baseURL: `http://127.0.0.1:${PORT}` },
    },
    {
      name: "chromium-auth",
      testMatch: /auth\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], baseURL: `http://127.0.0.1:${AUTH_PORT}` },
    },
  ],
  webServer: [
    {
      command: serverCommand(PORT),
      url: `http://127.0.0.1:${PORT}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
      gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
    },
    {
      command: serverCommand(AUTH_PORT, `--token ${AUTH_TOKEN}`),
      url: `http://127.0.0.1:${AUTH_PORT}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
      gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
    },
  ],
});
