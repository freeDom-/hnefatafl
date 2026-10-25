import { defineConfig, devices } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const dataDir = mkdtempSync(join(tmpdir(), 'hnefatafl-online-browser-'));
export default defineConfig({
  testDir: './online-tests',
  workers: 1,
  fullyParallel: false,
  timeout: 90000,
  expect: { timeout: 15000 },
  use: {
    baseURL: 'http://127.0.0.1:4177',
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE },
    trace: 'off',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
  ],
  webServer: [
    {
      command: '../.venv/bin/python tests/start_online_backend.py',
      url: 'http://127.0.0.1:8005/api/health',
      env: { HNEFATAFL_DATA_DIR: dataDir, HNEFATAFL_PUBLIC_URL: 'http://127.0.0.1:4177' },
      reuseExistingServer: false,
      timeout: 60000,
    },
    {
      command: 'npm run dev -- --port 4177 --strictPort',
      url: 'http://127.0.0.1:4177',
      env: { VITE_API_TARGET: 'http://127.0.0.1:8005' },
      reuseExistingServer: false,
      timeout: 30000,
    },
  ],
});
