import { defineConfig, devices } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const dataDir = mkdtempSync(join(tmpdir(), 'hnefatafl-registration-browser-'));
export default defineConfig({
  testDir: './registration-tests',
  workers: 1,
  fullyParallel: false,
  timeout: 90000,
  expect: { timeout: 15000 },
  use: {
    baseURL: 'http://127.0.0.1:4175',
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE },
    trace: 'off',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
  ],
  webServer: [
    {
      command: '../.venv/bin/python tests/start_registration_backend.py',
      url: 'http://127.0.0.1:8003/api/health',
      env: { HNEFATAFL_DATA_DIR: dataDir, HNEFATAFL_PUBLIC_URL: 'http://127.0.0.1:4175' },
      reuseExistingServer: false,
      timeout: 60000,
    },
    {
      command: 'npm run dev -- --port 4175 --strictPort',
      url: 'http://127.0.0.1:4175',
      env: { VITE_API_TARGET: 'http://127.0.0.1:8003' },
      reuseExistingServer: false,
      timeout: 30000,
    },
  ],
});
