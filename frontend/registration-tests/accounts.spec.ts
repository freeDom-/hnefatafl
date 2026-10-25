import { test, expect, type APIRequestContext } from '@playwright/test';

const origin = 'http://127.0.0.1:4175';
const headers = { Origin: origin };
const password = 'browser-registration-test-password';
async function mail(request: APIRequestContext, email: string, purpose: string, count = 1) {
  const endpoint = `http://127.0.0.1:8004/?email=${encodeURIComponent(email)}&purpose=${purpose}`;
  await expect
    .poll(async () => (await (await request.get(endpoint)).json()).length)
    .toBeGreaterThanOrEqual(count);
  const messages = await (await request.get(endpoint)).json();
  return messages.at(-1).link as string;
}

test('register, explicitly verify, then log in with no session from registration', async ({
  page,
  request,
  playwright,
}, info) => {
  const name = `Register${info.project.name}`;
  const email = `${name.toLowerCase()}@example.test`;
  await page.goto('/');
  await page.getByRole('button', { name: 'Sign up / Register' }).click();
  await page.getByLabel('Username', { exact: true }).fill(name);
  await page.getByLabel('Email address').fill(email);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByLabel('Confirm password').fill(password);
  await page.getByRole('button', { name: 'Create account' }).click();
  await expect(page.getByRole('heading', { name: 'Verify your email' })).toBeVisible();
  expect((await page.request.get('/api/auth/me')).status()).toBe(401);
  const link = await mail(request, email, 'verify');
  const requested: string[] = [];
  page.on('request', (request) => requested.push(request.url()));
  await page.goto(link);
  await expect(page.getByRole('heading', { name: 'Confirm your email' })).toBeVisible();
  expect(new URL(page.url()).hash).toBe('');
  const scanner = await playwright.request.newContext();
  const before = await scanner.post(origin + '/api/auth/login', {
    headers,
    data: { identity: name, password },
  });
  expect((await before.json()).account.email_verified).toBe(false);
  await scanner.dispose();
  await page.getByRole('button', { name: 'Verify email', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('Email verified. You can log in.');
  await page.getByRole('button', { name: 'Back to log in' }).click();
  await page.getByLabel('Username or email').fill(name);
  await page.getByLabel('Password', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Log out', exact: true })).toBeVisible();
  expect((await (await page.request.get('/api/auth/me')).json()).account.email_verified).toBe(true);
  const token = new URLSearchParams(new URL(link).hash.slice(1)).get('token')!;
  expect(requested.every((url) => !url.includes(token))).toBe(true);
});

test('password recovery rejects old sessions and requires login with the new password', async ({
  page,
  request,
  playwright,
}, info) => {
  const name = `Recover${info.project.name}`;
  const email = `${name.toLowerCase()}@example.test`;
  expect(
    (
      await request.post('/api/auth/register', {
        headers,
        data: { username: name, email, password },
      })
    ).status(),
  ).toBe(202);
  const oldOne = await playwright.request.newContext();
  const oldTwo = await playwright.request.newContext();
  for (const context of [oldOne, oldTwo])
    expect(
      (
        await context.post(origin + '/api/auth/login', {
          headers,
          data: { identity: name, password },
        })
      ).ok(),
    ).toBe(true);
  await page.goto('/forgot-password');
  await page.getByLabel('Email address').fill(email);
  await page.getByRole('button', { name: 'Send email' }).click();
  await expect(page.getByRole('status')).toContainText('If the address is eligible');
  const link = await mail(request, email, 'reset');
  await page.goto(link);
  await expect(page.getByRole('heading', { name: 'Choose a new password' })).toBeVisible();
  expect(new URL(page.url()).hash).toBe('');
  expect((await oldOne.get(origin + '/api/auth/me')).ok()).toBe(true);
  const replacement = 'browser-new-recovery-password';
  await page.getByLabel('Password', { exact: true }).fill(replacement);
  await page.getByLabel('Confirm password').fill(replacement);
  await page.getByRole('button', { name: 'Set new password' }).click();
  await expect(page.getByRole('status')).toHaveText(
    'Password reset. Log in with your new password.',
  );
  for (const context of [oldOne, oldTwo]) {
    expect((await context.get(origin + '/api/auth/me')).status()).toBe(401);
    await context.dispose();
  }
  expect((await page.request.get('/api/auth/me')).status()).toBe(401);
  await page.getByRole('button', { name: 'Back to log in' }).click();
  await page.getByLabel('Username or email').fill(name);
  await page.getByLabel('Password', { exact: true }).fill(replacement);
  await page.getByRole('button', { name: 'Log in', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Log out', exact: true })).toBeVisible();
});

test('German registration and resend replace older verification links', async ({
  page,
  request,
}, info) => {
  const name = `Deutsch${info.project.name}`;
  const email = `${name.toLowerCase()}@example.test`;
  await page.goto('/');
  await page.getByRole('combobox', { name: 'Language' }).selectOption('de');
  await page.getByRole('button', { name: 'Registrieren', exact: true }).click();
  await page.getByLabel('Benutzername', { exact: true }).fill(name);
  await page.getByLabel('E-Mail-Adresse').fill(email);
  await page.getByLabel('Passwort', { exact: true }).fill(password);
  await page.getByLabel('Passwort bestätigen').fill(password);
  await page.getByRole('button', { name: 'Konto erstellen' }).click();
  await expect(page.getByRole('status')).toContainText('Wenn die Adresse berechtigt ist');
  const oldLink = await mail(request, email, 'verify');
  await page.getByLabel('E-Mail-Adresse').fill(email);
  await page.getByRole('button', { name: 'E-Mail senden' }).click();
  const link = await mail(request, email, 'verify', 2);
  const oldToken = new URLSearchParams(new URL(oldLink).hash.slice(1)).get('token');
  expect(
    (await request.post('/api/auth/verify-email', { headers, data: { token: oldToken } })).status(),
  ).toBe(400);
  await page.goto(link);
  await expect(page.getByRole('heading', { name: 'E-Mail bestätigen' })).toBeVisible();
  await page.getByRole('button', { name: 'E-Mail bestätigen', exact: true }).click();
  await expect(page.getByRole('status')).toHaveText('E-Mail bestätigt. Du kannst dich anmelden.');
});
