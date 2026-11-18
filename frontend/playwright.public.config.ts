import { defineConfig } from '@playwright/test';
import { mkdtempSync } from 'node:fs';

const dataDir = mkdtempSync('/tmp/hnefatafl-public-browser-');
export default defineConfig({
  testDir: './public-tests',
  outputDir: './test-results/public-production',
  fullyParallel: false,
  workers: 1,
  timeout: 90000,
  expect: { timeout: 10000 },
  retries: 0,
  use: {
    baseURL: 'http://127.0.0.1:8011',
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'phone',
      use: { viewport: { width: 320, height: 844 }, isMobile: true, hasTouch: true },
    },
    { name: 'tablet', use: { viewport: { width: 768, height: 1024 }, hasTouch: true } },
    { name: 'desktop', use: { viewport: { width: 1440, height: 900 } } },
    { name: '4k', use: { viewport: { width: 3840, height: 2160 } } },
  ],
  webServer: {
    command: 'npm run build && ../.venv/bin/python tests/start_public_backend.py',
    url: 'http://127.0.0.1:8011/api/health',
    env: { HNEFATAFL_DATA_DIR: dataDir },
    reuseExistingServer: false,
    timeout: 120000,
  },
});
