import { defineConfig } from '@playwright/test'

/**
 * Playwright e2e (tasks 7.1–7.3, design decision 11). Two projects:
 *
 * - `e2e` — the app against the scriptable mock engine (tests/mock-engine/),
 *   adopted through the production discovery path.
 * - `integration` — the real-engine smoke: spawns `uv run podcast-reader
 *   serve` via the dev fallback (requires the repo's Python toolchain;
 *   `uv sync --extra dev` at the repo root). Skip with `--project e2e`.
 *
 * Run `npm run build` first (the suite launches `out/main/index.js`); on
 * headless hosts wrap in `xvfb-run -a`. One worker: each test boots a full
 * Electron app — serial keeps xvfb and the single-instance locks calm.
 */
export default defineConfig({
  testDir: 'tests/e2e',
  timeout: 90_000,
  workers: 1,
  retries: process.env.CI !== undefined ? 1 : 0,
  preserveOutput: 'always',
  reporter: [['list']],
  projects: [
    { name: 'e2e', testIgnore: /integration\.spec\.ts/ },
    { name: 'integration', testMatch: /integration\.spec\.ts/ }
  ]
})
