import { expect, test } from '@playwright/test';

test('legacy-private fields require only per-field confirmation and unrelated edits stay private', async ({
  page,
  request,
}) => {
  expect((await request.post('http://127.0.0.1:8010/reset-login-attempts')).status()).toBe(204);
  expect((await request.post('http://127.0.0.1:8010/legacy-profile')).status()).toBe(204);
  const login = await page.request.post('/api/auth/login', {
    headers: { Origin: 'http://127.0.0.1:8009' },
    data: { identity: 'PersonalObserver', password: 'personal-browser-fixture-password' },
  });
  expect(login.ok()).toBe(true);
  const session = await login.json();
  const headers = { Origin: 'http://127.0.0.1:8009', 'X-CSRF-Token': session.csrf_token };
  const created = await page.request.post('/api/games', {
    headers,
    data: { ruleset: 'brandubh', human_side: 'attackers' },
  });
  expect(created.status()).toBe(201);
  const game = await created.json();
  const current = async () => (await page.request.get(`/api/games/${game.id}`)).json();
  async function save(expected: Record<string, unknown>) {
    const submitted = page.waitForRequest(
      (r) => new URL(r.url()).pathname === '/api/profile' && r.method() === 'PATCH',
    );
    await page.getByRole('button', { name: 'Save Profile' }).click();
    expect((await submitted).postDataJSON()).toEqual(expected);
    await expect(page.getByText('Profile saved.')).toBeVisible();
  }
  try {
    await page.goto('/profile');
    await expect(page.getByRole('textbox', { name: 'Display name' })).toHaveValue('Legacy secret');
    await expect(page.getByRole('checkbox')).toHaveCount(0);
    await expect(page.getByText('Leaderboard visibility', { exact: true })).toHaveCount(0);
    await expect(page.getByLabel('Date of birth')).toHaveValue('');
    await expect(page.getByText(/Previously saved birth year: 1990/)).toBeVisible();
    expect(game.players.attackers.name).toBe('PersonalObserver');
    await page.getByRole('textbox', { name: 'City', exact: true }).fill('Kyōto');
    await save({ city: 'Kyōto' });
    expect((await current()).players.attackers.name).toBe('PersonalObserver');
    let profile = await (await page.request.get('/api/profile')).json();
    expect(profile.legacy_private_fields).toEqual(['country_code', 'display_name']);
    expect(profile.legacy_birth_year).toBe(1990);
    const confirmation = page.getByRole('button', {
      name: /^(Publish saved display name on save|Display name will be public when saved\.)$/,
    });
    await confirmation.focus();
    await page.keyboard.press('Enter');
    await expect(confirmation).toBeFocused();
    await expect(confirmation).toHaveAttribute('aria-disabled', 'true');
    await save({ display_name: 'Legacy secret' });
    const fresh = await current();
    expect(fresh.players.attackers.name).toBe('Legacy secret');
    expect(fresh.players.attackers.username).toBe('PersonalObserver');
    expect(fresh.version).toBe(game.version);
    expect(fresh.board).toEqual(game.board);
    await expect(page.getByRole('button', { name: 'Publish saved country on save' })).toBeVisible();
    await page.getByLabel('Date of birth').fill('1990-03-15');
    await save({ date_of_birth: '1990-03-15' });
    profile = await (await page.request.get('/api/profile')).json();
    expect(profile.legacy_birth_year).toBeNull();
    expect(profile.legacy_private_fields).toEqual(['country_code']);
    await expect(page.getByText(/Previously saved birth year/)).toHaveCount(0);
    await page.getByRole('textbox', { name: 'Display name' }).fill('');
    await save({ display_name: null });
    expect((await current()).players.attackers.name).toBe('PersonalObserver');
  } finally {
    expect(
      (
        await page.request.patch('/api/profile', {
          headers,
          data: { display_name: null, country_code: null, city: null, date_of_birth: null },
        })
      ).ok(),
    ).toBe(true);
    expect((await page.request.delete(`/api/games/${game.id}?version=0`, { headers })).ok()).toBe(
      true,
    );
  }
});
