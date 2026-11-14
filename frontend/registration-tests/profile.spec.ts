import { test, expect, type Locator } from '@playwright/test';
import { openDestination, openProfile, logoutFromAccount } from '../tests/play-helpers';

test.use({ screenshot: 'only-on-failure' });
test.beforeEach(async ({ request }) => {
  expect((await request.post('http://127.0.0.1:8004/reset-account-limits')).status()).toBe(204);
});

test('account menu, private profile, verification, password and device preferences work together', async ({
  page,
  request,
}, info) => {
  test.setTimeout(180000);
  const username = `Profile${info.project.name}`;
  const email = `${username.toLowerCase()}@example.test`;
  const password = 'profile-browser-test-password';
  const changedPassword = 'e\u0301😀🔑'.repeat(64);
  const origin = 'http://127.0.0.1:4175';
  const registered = await request.post('/api/auth/register', {
    headers: { Origin: origin },
    data: { username, email, password },
  });
  expect(registered.status()).toBe(202);
  await page.goto('/profile');
  await expect(page.getByRole('combobox', { name: 'Language' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Account menu for/ })).toHaveCount(0);
  await page.getByLabel('Username or email').fill(username);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'My Profile', exact: true })).toBeVisible();
  const trigger = page.getByRole('button', { name: `Account menu for ${username}` });
  const language = page.getByRole('combobox', { name: 'Language' });
  const header = page.locator('.arena-topbar, .site-header');
  await expect(header).toHaveCount(1);
  await expect(header.getByRole('combobox', { name: 'Language' })).toBeVisible();
  await expect(header.getByRole('button', { name: `Account menu for ${username}` })).toBeVisible();
  const languageBox = await language.boundingBox();
  const accountBox = await trigger.boundingBox();
  expect(languageBox!.x + languageBox!.width).toBeLessThanOrEqual(accountBox!.x + 1);
  expect(
    Math.abs(languageBox!.y + languageBox!.height / 2 - accountBox!.y - accountBox!.height / 2),
  ).toBeLessThan(32);
  await expect(page.getByRole('combobox', { name: 'Language' })).toHaveCount(1);
  await expect(
    page.locator('.profile-page').getByRole('combobox', { name: 'Language' }),
  ).toHaveCount(0);
  await expect(
    page.locator('.site-header').getByRole('button', { name: 'Appearance settings' }),
  ).toHaveCount(0);
  await trigger.focus();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('menuitem', { name: 'My Profile' })).toBeFocused();
  await page.keyboard.press('End');
  await expect(page.getByRole('menuitem', { name: 'Log out' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await trigger.click();
  await page.getByRole('heading', { name: 'My Profile', exact: true }).click();
  await expect(page.getByRole('menu')).toHaveCount(0);
  await expect(page.getByRole('textbox', { name: 'Display name' })).toHaveValue('');
  await expect(page.getByRole('checkbox')).toHaveCount(0);
  await expect(page.getByText('Leaderboard visibility', { exact: true })).toHaveCount(0);
  await page.getByRole('textbox', { name: 'Display name' }).fill('  Þór 東京  ');
  await page.getByRole('textbox', { name: 'City', exact: true }).fill('Łódź');
  await page.getByRole('combobox', { name: 'Country', exact: true }).selectOption('DE');
  await page.getByLabel('Date of birth', { exact: true }).fill('1990-03-15');
  await page.getByRole('button', { name: 'Save Profile' }).click();
  await expect(page.getByText('Profile saved.')).toBeVisible();
  await page.reload();
  await expect(page.getByRole('textbox', { name: 'Display name' })).toHaveValue('Þór 東京');
  await expect(page.getByRole('combobox', { name: 'Country', exact: true })).toHaveValue('DE');
  await expect(page.getByLabel('Date of birth', { exact: true })).toHaveValue('1990-03-15');
  await openDestination(page, 'dashboard');
  await page.getByRole('link', { name: 'Verify your email in your profile.' }).click();
  await expect(page).toHaveURL(/\/profile#email-verification$/);
  await expect(page.locator('#email-verification')).toBeFocused();
  await page.getByRole('button', { name: 'Send Verification Email' }).click();
  await expect(
    page.getByText('If the address is eligible, an email will arrive. Only the newest link works.'),
  ).toBeVisible();
  const mailbox = `http://127.0.0.1:8004/?email=${email}&purpose=verify`;
  // Registration and resend rotate the same purpose-bound token; consume the newest delivered link.
  await expect
    .poll(async () => (await (await request.get(mailbox)).json()).length)
    .toBeGreaterThanOrEqual(1);
  let verified = false;
  await expect
    .poll(async () => {
      const messages = await (await request.get(mailbox)).json();
      const link = messages.at(-1).link as string;
      const token = new URLSearchParams(new URL(link).hash.slice(1)).get('token');
      const result = await request.post('/api/auth/verify-email', {
        headers: { Origin: origin },
        data: { token },
      });
      verified = result.ok();
      return verified;
    })
    .toBe(true);
  // Confirmation from another session is observed by the existing focus refresh.
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect(page.getByText('Verified', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Send Verification Email' })).toHaveCount(0);
  await openDestination(page, 'dashboard');
  await expect(page.locator('.dashboard-verification-warning')).toHaveCount(0);
  await openProfile(page);
  await page.getByLabel('Current password').fill('incorrect-password');
  await page.getByLabel('New password', { exact: true }).fill(changedPassword);
  await page.getByLabel('Confirm new password').fill(changedPassword);
  await page.getByRole('button', { name: 'Change Password' }).click();
  await expect(page.getByText('Current password is incorrect.')).toBeVisible();
  await page.getByLabel('Current password').fill(password);
  await page.getByRole('button', { name: 'Change Password' }).click();
  await expect(
    page.getByText('Password changed. Other sessions have been signed out.'),
  ).toBeVisible();
  await expect(page.getByLabel('Current password')).toHaveValue('');
  await expect(page.getByLabel('New password', { exact: true })).toHaveValue('');
  await expect(page.getByLabel('Confirm new password')).toHaveValue('');
  await page.getByRole('radio', { name: 'Iron & Ash', exact: true }).check();
  await language.selectOption('de');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('lang', 'de');
  await expect(page.locator('html')).toHaveAttribute('data-ui-theme', 'iron-ash');
  await expect(page.getByRole('option', { name: 'Deutschland', exact: true })).toBeAttached();
  await logoutFromAccount(page);
  await expect(page.getByRole('combobox', { name: 'Sprache' })).toBeVisible();
  await page.goto('/profile');
  await expect(page.locator('.profile-page')).toHaveCount(0);
  expect((await page.request.get('/api/profile')).status()).toBe(401);
  await page.getByRole('combobox', { name: 'Sprache' }).selectOption('en');
  await page.getByLabel('Username or email').fill(username);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Invalid username/email or password.');
  await page.getByLabel('Password', { exact: true }).fill(changedPassword);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.locator('.profile-page')).toBeVisible();
  expect((await page.request.get('/api/profile')).status()).toBe(200);
});

test('header and profile fit narrow and wide screens in every theme and language', async ({
  page,
  request,
}, info) => {
  test.setTimeout(180000);
  const username = `LongUsernameForResponsiveProfile${info.project.name}`.padEnd(64, 'W');
  const password = 'responsive-profile-password';
  const result = await request.post('/api/auth/register', {
    headers: { Origin: 'http://127.0.0.1:4175' },
    data: { username, email: `${username.toLowerCase()}@example.test`, password },
  });
  expect(result.status()).toBe(202);
  await page.goto('/profile');
  await page.getByLabel('Username or email').fill(username);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.locator('.profile-page')).toBeVisible();
  for (const width of [320, 375, 390, 760, 768, 1440, 1920]) {
    await page.setViewportSize({ width, height: 900 });
    for (const language of ['en', 'de']) {
      await page.locator('.language-select select').selectOption(language);
      for (const mode of ['light', 'dark', 'wood', 'system']) {
        await page.locator(`input[name$='-color-mode'][value='${mode}']`).check();
        await page.locator('.account-trigger').scrollIntoViewIfNeeded();
        await expect(page.locator('.language-select select')).toBeVisible();
        await page.locator('.account-trigger').click();
        await expect(page.getByRole('menuitem').first()).toBeVisible();
        await expect(page.getByRole('menuitem')).toHaveCount(2);
        const identity = page.locator('.account-menu-identity');
        if (width <= 760) {
          await expect(identity).toBeVisible();
          await expect(identity).toHaveText(username);
          const nameBounds = await identity.boundingBox();
          expect(nameBounds!.width).toBeGreaterThan(1);
          expect(nameBounds!.x).toBeGreaterThanOrEqual(0);
          expect(nameBounds!.x + nameBounds!.width).toBeLessThanOrEqual(width);
        } else await expect(identity).toBeHidden();
        await page.keyboard.press('ArrowDown');
        await expect(page.getByRole('menuitem').last()).toBeFocused();
        await page.keyboard.press('Home');
        await expect(page.getByRole('menuitem').first()).toBeFocused();
        const bounds = await page.locator('.account-dropdown').boundingBox();
        expect(bounds!.x).toBeGreaterThanOrEqual(0);
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
        const overflow = await page.evaluate(() => ({
          width: innerWidth,
          scrollWidth: document.documentElement.scrollWidth,
          elements: [...document.querySelectorAll('body *')]
            .filter((node) => {
              const rect = node.getBoundingClientRect();
              return rect.width > 0 && (rect.right > innerWidth + 1 || rect.left < -1);
            })
            .slice(0, 10)
            .map((node) => ({
              tag: node.tagName,
              class: node.className,
              right: node.getBoundingClientRect().right,
            })),
        }));
        expect(
          overflow.scrollWidth,
          JSON.stringify({ language, mode, ...overflow }),
        ).toBeLessThanOrEqual(width);
        await page.keyboard.press('Escape');
      }
    }
    await page.screenshot({ path: info.outputPath(`profile-${width}.png`), fullPage: true });
  }
});

test('native typing and paste retain Unicode profile and password boundaries', async ({
  page,
  context,
}, info) => {
  test.setTimeout(180000);
  const username = `UnicodeInput${info.project.name}`;
  const password = 'e\u0301😀🔑'.repeat(64);
  const replacement = '😀😁😂😃'.repeat(64);
  await page.goto('/register');
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  async function paste(field: Locator, value: string) {
    await field.fill('');
    await field.focus();
    await page.evaluate((text) => navigator.clipboard.writeText(text), value);
    await page.keyboard.press('Control+V');
    await expect(field).toHaveValue(value);
  }
  await page.getByLabel('Username', { exact: true }).fill(username);
  await page.getByLabel('Email address').fill(`${username.toLowerCase()}@example.test`);
  for (const label of ['Password', 'Confirm password'])
    await paste(page.getByLabel(label, { exact: true }), '😀😁😂😃😁😂');
  await page.getByRole('button', { name: 'Create account', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Use 12–256 characters');
  for (const label of ['Password', 'Confirm password'])
    await paste(page.getByLabel(label, { exact: true }), password);
  await page.getByRole('button', { name: 'Create account', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Verify your email', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Back to log in' }).click();
  await page.getByLabel('Username or email').fill(username);
  await paste(page.getByLabel('Password', { exact: true }), password);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await openProfile(page);
  const name = page.locator('input[name="display_name"]');
  const city = page.locator('input[name="city"]');
  const rawName = 'e\u0301😀'.repeat(40);
  const normalized = rawName.normalize('NFC');
  await name.pressSequentially(rawName);
  await expect(name).toHaveValue(rawName);
  await paste(city, '🏰'.repeat(100));
  await page.getByRole('button', { name: 'Save Profile' }).click();
  await expect(page.getByText('Profile saved.', { exact: true })).toBeVisible();
  await expect(name).toHaveValue(normalized);
  await expect(city).toHaveValue('🏰'.repeat(100));
  const saved = await (await page.request.get('/api/profile')).json();
  expect(saved.display_name).toBe(normalized);
  expect(saved.city).toBe('🏰'.repeat(100));
  await name.focus();
  await page.keyboard.insertText('😀');
  const oversized = normalized + '😀';
  for (const language of ['en', 'de']) {
    await page.locator('.language-select select').selectOption(language);
    await page.getByRole('button', { name: /^(Save Profile|Profil speichern)$/ }).click();
    await expect(page.getByRole('alert')).toContainText(
      language === 'de' ? '80 Zeichen' : '80 characters',
    );
    await expect(name).toHaveValue(oversized);
    expect((await (await page.request.get('/api/profile')).json()).display_name).toBe(normalized);
  }
  await name.fill(normalized);
  await page.getByRole('button', { name: 'Profil speichern' }).click();
  await expect(page.getByText('Profil gespeichert.', { exact: true })).toBeVisible();
  await paste(page.locator('input[name="current_password"]'), password);
  for (const field of ['new_password', 'confirm_password'])
    await paste(page.locator(`input[name="${field}"]`), replacement);
  // The existing account/profile scenario changes to a boundary-length Unicode
  // password and checks old/new login plus session revocation. Keep this case
  // focused on native typing/paste rather than charging that shared IP budget
  // for a duplicate password-change operation.
  await logoutFromAccount(page);
  await page.locator('input[name="identity"]').fill(username);
  await paste(page.locator('input[name="password"]'), password);
  await page.getByRole('button', { name: 'Anmelden', exact: true }).click();
  await expect(page.locator('.profile-page')).toBeVisible();
});
