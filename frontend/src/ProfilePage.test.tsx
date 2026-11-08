import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import ProfilePage, { type Profile } from './ProfilePage';
import { api } from './api';
import { AppearanceProvider } from './Appearance';
import { APPEARANCE_KEY } from './appearanceConfig';
import { I18nProvider, setLanguage } from './i18n';
import userEvent from '@testing-library/user-event';

vi.mock('./api', async (original) => ({
  ...(await original<typeof import('./api')>()),
  api: vi.fn(),
}));
const identity = vi.hoisted(() => ({
  id: 'fixture',
  username: 'Odin',
  email: 'odin@example.test',
  email_verified: undefined as boolean | undefined,
}));
vi.mock('./Auth', () => {
  const observeVerification = () => () => {};
  return { useAuth: () => ({ account: identity, observeVerification }) };
});
const initial: Profile = {
  username: 'Odin',
  email: 'odin@example.test',
  email_verified: false,
  display_name: null,
  country_code: null,
  city: null,
  birth_year: null,
  show_display_name: false,
  show_country: false,
  show_city: false,
  show_age: false,
  country_codes: ['DE', 'JP', 'SE'],
};
const apiMock = vi.mocked(api);
let stored: Profile;
beforeEach(() => {
  identity.email_verified = undefined;
  stored = { ...initial };
  localStorage.clear();
  history.replaceState(null, '', '/profile');
  setLanguage('en', false);
  apiMock.mockReset();
  apiMock.mockImplementation(async (path, body) => {
    if (path === '/profile') {
      stored = { ...stored, ...(body as Partial<Profile>) };
      return stored;
    }
    return {
      message:
        path === '/profile/password'
          ? 'Password changed. Other sessions have been signed out.'
          : 'If the address is eligible, an email will arrive. Only the newest link works.',
    };
  });
});
afterEach(() => {
  cleanup();
  setLanguage('en', false);
  vi.restoreAllMocks();
});
async function page() {
  const view = render(
    <I18nProvider>
      <AppearanceProvider>
        <ProfilePage />
      </AppearanceProvider>
    </I18nProvider>,
  );
  await screen.findByRole('textbox', { name: /^(Display name|Anzeigename)$/ });
  return view;
}
it('loads once, starts with private empty fields and has appearance settings without another language selector', async () => {
  await page();
  expect(screen.getByRole('textbox', { name: 'Display name' })).toHaveValue('');
  expect(screen.getByRole('combobox', { name: 'Country' })).toHaveValue('');
  expect(screen.getAllByRole('checkbox')).toHaveLength(4);
  screen.getAllByRole('checkbox').forEach((checkbox) => expect(checkbox).not.toBeChecked());
  expect(screen.getByRole('radio', { name: 'Follow device' })).toBeChecked();
  expect(screen.queryByRole('combobox', { name: 'Language' })).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: 'City' }), { target: { value: 'Dresden' } });
  expect(apiMock.mock.calls.filter(([path]) => path === '/profile')).toHaveLength(1);
});
it('saves optional Unicode fields and independent visibility then restores the saved values on reload', async () => {
  const view = await page();
  fireEvent.change(screen.getByRole('textbox', { name: 'Display name' }), {
    target: { value: '  Þór 東京  ' },
  });
  fireEvent.change(screen.getByRole('textbox', { name: 'City' }), {
    target: { value: '  Łódź  ' },
  });
  fireEvent.change(screen.getByRole('combobox', { name: 'Country' }), { target: { value: 'DE' } });
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Year of birth' }), {
    target: { value: '1990' },
  });
  fireEvent.click(screen.getByRole('checkbox', { name: 'Share country' }));
  fireEvent.click(screen.getByRole('button', { name: 'Save Profile' }));
  await screen.findByText('Profile saved.');
  expect(apiMock).toHaveBeenCalledWith(
    '/profile',
    {
      display_name: 'Þór 東京',
      city: 'Łódź',
      country_code: 'DE',
      birth_year: 1990,
      show_display_name: false,
      show_country: true,
      show_city: false,
      show_age: false,
    },
    'PATCH',
    expect.any(AbortSignal),
  );
  view.unmount();
  await page();
  expect(screen.getByRole('textbox', { name: 'Display name' })).toHaveValue('Þór 東京');
  expect(screen.getByRole('checkbox', { name: 'Share country' })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: 'Share city' })).not.toBeChecked();
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Year of birth' }), {
    target: { value: '' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save Profile' }));
  await waitFor(() => expect(stored.birth_year).toBeNull());
});
it('keeps changes on save failure and prevents duplicate submissions while saving', async () => {
  await page();
  let reject!: (error: Error) => void;
  apiMock.mockImplementationOnce(
    () =>
      new Promise((_resolve, fail) => {
        reject = fail;
      }),
  );
  fireEvent.change(screen.getByRole('textbox', { name: 'City' }), { target: { value: 'Kyōto' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save Profile' }));
  const saving = screen.getByRole('button', { name: 'Saving…' });
  expect(saving).toBeDisabled();
  fireEvent.submit(saving.closest('form')!);
  expect(apiMock.mock.calls.filter(([, body]) => body)).toHaveLength(1);
  await act(async () => reject(new Error('Try again later.')));
  expect(await screen.findByRole('alert')).toHaveTextContent('Try again later.');
  expect(screen.getByRole('textbox', { name: 'City' })).toHaveValue('Kyōto');
  expect(screen.getByRole('button', { name: 'Save Profile' })).toBeEnabled();
});
it('validates password confirmation and policy before sending, then clears sensitive inputs after success', async () => {
  await page();
  const current = screen.getByLabelText('Current password');
  const next = screen.getByLabelText('New password');
  const confirm = screen.getByLabelText('Confirm new password');
  fireEvent.change(current, { target: { value: 'current-password' } });
  fireEvent.change(next, { target: { value: 'new-password-123' } });
  fireEvent.change(confirm, { target: { value: 'mismatch' } });
  const button = screen.getByRole('button', { name: 'Change Password' });
  fireEvent.submit(button.closest('form')!);
  expect(screen.getByRole('alert')).toHaveTextContent('Passwords do not match.');
  expect(apiMock).toHaveBeenCalledTimes(1);
  fireEvent.change(next, { target: { value: 'aaaaaaaaaaaa' } });
  fireEvent.change(confirm, { target: { value: 'aaaaaaaaaaaa' } });
  fireEvent.submit(button.closest('form')!);
  expect(screen.getByRole('alert')).toHaveTextContent('Use 12–256 characters');
  expect(apiMock).toHaveBeenCalledTimes(1);
  fireEvent.change(next, { target: { value: 'new-password-123' } });
  fireEvent.change(confirm, { target: { value: 'new-password-123' } });
  fireEvent.click(button);
  await screen.findByText('Password changed. Other sessions have been signed out.');
  expect(current).toHaveValue('');
  expect(next).toHaveValue('');
  expect(confirm).toHaveValue('');
  expect(apiMock).toHaveBeenCalledWith(
    '/profile/password',
    {
      current_password: 'current-password',
      new_password: 'new-password-123',
      confirm_password: 'new-password-123',
    },
    undefined,
    expect.any(AbortSignal),
  );
});
it('shows backend password failures without losing form values', async () => {
  await page();
  apiMock.mockRejectedValueOnce(new Error('Current password is incorrect.'));
  fireEvent.change(screen.getByLabelText('Current password'), { target: { value: 'wrong' } });
  for (const label of ['New password', 'Confirm new password'])
    fireEvent.change(screen.getByLabelText(label), { target: { value: 'replacement-password' } });
  fireEvent.click(screen.getByRole('button', { name: 'Change Password' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Current password is incorrect.');
  expect(screen.getByLabelText('Current password')).toHaveValue('wrong');
});
it('targets the verification section and reuses resend with the authenticated email', async () => {
  history.replaceState(null, '', '/profile#email-verification');
  await page();
  expect(document.getElementById('email-verification')).toHaveFocus();
  expect(screen.getByText('Not verified')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Send Verification Email' }));
  await screen.findByText(
    'If the address is eligible, an email will arrive. Only the newest link works.',
  );
  expect(apiMock).toHaveBeenCalledWith(
    '/auth/verification/resend',
    { email: initial.email },
    undefined,
    expect.any(AbortSignal),
  );
});
it('omits verification actions for verified accounts', async () => {
  stored.email_verified = true;
  await page();
  expect(screen.getByText('Verified')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Send Verification Email' })).not.toBeInTheDocument();
});

it('updates verification status when the shared account state refreshes after confirmation', async () => {
  identity.email_verified = false;
  const view = await page();
  expect(screen.getByText('Not verified')).toBeVisible();
  identity.email_verified = true;
  view.rerender(
    <I18nProvider>
      <AppearanceProvider>
        <ProfilePage />
      </AppearanceProvider>
    </I18nProvider>,
  );
  expect(screen.getByText('Verified')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Send Verification Email' })).not.toBeInTheDocument();
});
it('allows retry after loading errors while retaining access to account security and appearance', async () => {
  apiMock.mockRejectedValueOnce(new Error('Network unavailable.'));
  render(<ProfilePage />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Network unavailable.');
  expect(screen.getByRole('button', { name: 'Change Password' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByRole('textbox', { name: 'Display name' })).toHaveValue('');
});

it('does not interrupt a password change when retrying optional profile data', async () => {
  apiMock.mockRejectedValueOnce(new Error('Network unavailable.'));
  render(<ProfilePage />);
  await screen.findByRole('alert');
  let complete!: (result: { message: string }) => void;
  apiMock.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  fireEvent.change(screen.getByLabelText('Current password'), {
    target: { value: 'current-password' },
  });
  for (const label of ['New password', 'Confirm new password'])
    fireEvent.change(screen.getByLabelText(label), { target: { value: 'replacement-password' } });
  fireEvent.click(screen.getByRole('button', { name: 'Change Password' }));
  const signal = apiMock.mock.calls.find(([path]) => path === '/profile/password')![3]!;
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await screen.findByRole('textbox', { name: 'Display name' });
  expect(signal.aborted).toBe(false);
  await act(async () =>
    complete({ message: 'Password changed. Other sessions have been signed out.' }),
  );
  expect(screen.getByText('Password changed. Other sessions have been signed out.')).toBeVisible();
  expect(screen.getByLabelText('Current password')).toHaveValue('');
});
it('localizes the profile and country names and preserves theme preferences through remounts', async () => {
  const view = await page();
  fireEvent.click(screen.getByRole('radio', { name: 'Wood' }));
  expect(document.documentElement).toHaveAttribute('data-color-mode', 'wood');
  expect(JSON.parse(localStorage.getItem(APPEARANCE_KEY)!).mode).toBe('wood');
  act(() => setLanguage('de'));
  expect(screen.getByRole('heading', { name: 'Mein Profil' })).toBeVisible();
  expect(screen.getByRole('option', { name: 'Deutschland' })).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: 'Land teilen' })).toBeVisible();
  view.unmount();
  await page();
  expect(document.documentElement).toHaveAttribute('data-color-mode', 'wood');
});

it.each(['en', 'de'] as const)(
  'rejects spoofing controls with a safe localized form error in %s',
  async (language) => {
    await page();
    act(() => setLanguage(language));
    const unsafe = 'unsafe-identity-' + String.fromCodePoint(0x202e) + 'spoof';
    fireEvent.change(
      screen.getByRole('textbox', { name: language === 'de' ? 'Anzeigename' : 'Display name' }),
      { target: { value: unsafe } },
    );
    fireEvent.click(
      screen.getByRole('button', { name: language === 'de' ? 'Profil speichern' : 'Save Profile' }),
    );
    const error = screen.getByRole('alert');
    expect(error).toHaveTextContent(
      language === 'de' ? 'Text-Richtungszeichen' : 'text direction controls',
    );
    expect(error).not.toHaveTextContent('unsafe-identity');
    expect(apiMock.mock.calls.filter(([path, body]) => path === '/profile' && body)).toHaveLength(
      0,
    );
  },
);

it.each(['en', 'de'] as const)(
  'accepts pasted non-BMP boundaries and reports oversize text in %s without truncation',
  async (language) => {
    await page();
    act(() => setLanguage(language));
    const user = userEvent.setup();
    const name = screen.getByRole('textbox', { name: /^(Display name|Anzeigename)$/ });
    const city = screen.getByRole('textbox', { name: /^(City|Stadt)$/ });
    const fullName = '😀'.repeat(80);
    const fullCity = '👩\u200d🚀'.repeat(33) + 'x';
    await user.click(name);
    await user.paste(fullName);
    await user.click(city);
    await user.paste(fullCity);
    expect(name).toHaveValue(fullName);
    expect(city).toHaveValue(fullCity);
    const save = screen.getByRole('button', { name: /^(Save Profile|Profil speichern)$/ });
    await user.click(save);
    await waitFor(() => expect(stored.display_name).toBe(fullName));
    expect(stored.city).toBe(fullCity);
    const writes = apiMock.mock.calls.filter(([, body]) => body).length;
    await user.click(name);
    await user.type(name, 'x');
    await user.click(save);
    expect(screen.getByRole('alert')).toHaveTextContent(
      language === 'de' ? '80 Zeichen' : '80 characters',
    );
    expect(name).toHaveValue(fullName + 'x');
    expect(apiMock.mock.calls.filter(([, body]) => body)).toHaveLength(writes);
  },
);

it('accepts keyboard-entered combining sequences and counts their NFC result', async () => {
  await page();
  const user = userEvent.setup();
  const name = screen.getByRole('textbox', { name: 'Display name' });
  const raw = 'e\u0301'.repeat(80);
  await user.type(name, raw);
  expect(name).toHaveValue(raw);
  await user.click(screen.getByRole('button', { name: 'Save Profile' }));
  await waitFor(() => expect(stored.display_name).toBe('é'.repeat(80)));
  expect(name).toHaveValue('é'.repeat(80));
});

it('pastes and submits 256-code-point passwords without truncation or normalization', async () => {
  await page();
  const user = userEvent.setup();
  const password = 'e\u0301😀🔑'.repeat(64);
  for (const label of ['Current password', 'New password', 'Confirm new password']) {
    await user.click(screen.getByLabelText(label));
    await user.paste(password);
    expect(screen.getByLabelText(label)).toHaveValue(password);
  }
  await user.click(screen.getByRole('button', { name: 'Change Password' }));
  await screen.findByText('Password changed. Other sessions have been signed out.');
  expect(apiMock).toHaveBeenCalledWith(
    '/profile/password',
    {
      current_password: password,
      new_password: password,
      confirm_password: password,
    },
    undefined,
    expect.any(AbortSignal),
  );
});

it.each(['en', 'de'] as const)(
  'validates short and oversized Unicode passwords in %s',
  async (language) => {
    await page();
    act(() => setLanguage(language));
    const field = (name: string) =>
      document.querySelector<HTMLInputElement>(`input[name="${name}"]`)!;
    fireEvent.change(field('current_password'), { target: { value: 'current-password' } });
    const button = screen.getByRole('button', { name: /^(Change Password|Passwort ändern)$/ });
    for (const password of ['😀😁😂😃😁😂', '😀😁😂😃'.repeat(64) + 'x']) {
      for (const name of ['new_password', 'confirm_password'])
        fireEvent.change(field(name), { target: { value: password } });
      fireEvent.click(button);
      expect(screen.getByRole('alert')).toHaveTextContent(
        language === 'de' ? '12–256 Zeichen' : '12–256 characters',
      );
      expect(apiMock.mock.calls.filter(([path]) => path === '/profile/password')).toHaveLength(0);
      expect(field('new_password')).toHaveValue(password);
    }
    fireEvent.change(field('current_password'), { target: { value: '😀'.repeat(257) } });
    for (const name of ['new_password', 'confirm_password'])
      fireEvent.change(field(name), { target: { value: 'replacement-password' } });
    fireEvent.click(button);
    expect(screen.getByRole('alert')).toHaveTextContent(
      language === 'de' ? 'höchstens 256 Zeichen' : 'at most 256 characters',
    );
    expect(apiMock.mock.calls.filter(([path]) => path === '/profile/password')).toHaveLength(0);
  },
);
