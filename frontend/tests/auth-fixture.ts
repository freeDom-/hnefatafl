import { test as base, expect } from '@playwright/test';
import type { APIRequestContext } from '@playwright/test';
export type { APIRequestContext, Page } from '@playwright/test';

// Existing browser regressions exercise a real administrator session. Dedicated
// authentication.spec.ts uses the raw fixture to test the actual login boundary.
type AdminSession = {
  state: Awaited<ReturnType<APIRequestContext['storageState']>>;
  csrf: string;
  id: string;
};
export const test = base.extend<object, { adminSession: AdminSession }>({
  adminSession: [
    async ({ playwright }, use) => {
      const initial = await playwright.request.newContext();
      const result = await initial.post('http://127.0.0.1:8001/api/auth/login', {
        headers: { Origin: 'http://127.0.0.1:8001' },
        data: { identity: 'BrowserAdmin', password: 'browser-fixture-only-password' },
      });
      expect(result.ok()).toBe(true);
      const session = await result.json();
      await use({
        state: await initial.storageState(),
        csrf: session.csrf_token,
        id: session.account.id,
      });
      await initial.dispose();
    },
    { scope: 'worker' },
  ],
  request: async ({ playwright, adminSession }, use) => {
    const context = await playwright.request.newContext({
      storageState: adminSession.state,
      extraHTTPHeaders: { Origin: 'http://127.0.0.1:8001', 'X-CSRF-Token': adminSession.csrf },
    });
    await use(context);
    await context.dispose();
  },
  page: async ({ page, request, adminSession }, use) => {
    await page.context().addCookies((await request.storageState()).cookies);
    await page.addInitScript(
      (id) => localStorage.setItem('hnefatafl-account-cache', id),
      adminSession.id,
    );
    await use(page);
  },
});
export { expect };
