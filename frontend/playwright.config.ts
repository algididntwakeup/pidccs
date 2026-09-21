import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright E2E config for the P&ID Studio frontend.
 *
 * The app under test is the already-running Docker stack (frontend on :3000,
 * api on :8000). We do NOT start a dev server here — we drive the real
 * production build served by the `frontend` container so that the E2E run
 * exercises exactly what the user sees.
 *
 * Run:  npx playwright test
 * Base URL can be overridden with PIDCCS_BASE_URL (default http://localhost:3000).
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: [['list']],
  timeout: 90_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL: process.env.PIDCCS_BASE_URL || 'http://localhost:3000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [
    {
      name: 'desktop-1080p',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1920, height: 1080 } },
    },
    {
      name: 'laptop-14in',
      // Full-HD panel on a 14" laptop under ~125% OS scaling ≈ 1536x864 CSS px.
      use: { ...devices['Desktop Chrome'], viewport: { width: 1366, height: 768 } },
    },
  ],
});
