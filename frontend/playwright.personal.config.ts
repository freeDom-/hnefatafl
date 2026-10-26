import { defineConfig, devices } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const dataDir = mkdtempSync(join(tmpdir(), 'hnefatafl-personal-browser-'));
export default defineConfig({
  testDir: './personal-tests',
  workers: 1,
  fullyParallel: false,
  timeout: 120000,
  expect: { timeout: 30000 },
  use: {
    baseURL: 'http://127.0.0.1:8009',
    actionTimeout: 15000,
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
  ],
  webServer: {
    command: 'npm run build && ../.venv/bin/python tests/start_personal_backend.py',
    url: 'http://127.0.0.1:8009/api/health',
    env: { HNEFATAFL_DATA_DIR: dataDir, HNEFATAFL_PUBLIC_URL: 'http://127.0.0.1:8009' },
    reuseExistingServer: false,
    timeout: 120000,
  },
});
