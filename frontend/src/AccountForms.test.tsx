import { StrictMode } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import AccountForms from './AccountForms';
import { I18nProvider, setLanguage } from './i18n';

const token = 'a'.repeat(43);
const response = (value: unknown, status = 200) =>
  Promise.resolve({ ok: status < 400, status, json: async () => value } as Response);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  history.replaceState(null, '', '/');
  setLanguage('en');
  localStorage.clear();
});
function mount(fetcher: ReturnType<typeof vi.fn>) {
  vi.stubGlobal('fetch', fetcher);
  const onReset = vi.fn();
  render(
    <StrictMode>
      <I18nProvider>
        <AccountForms onLogin={vi.fn()} loginBusy={false} loginError="" onReset={onReset} />
      </I18nProvider>
    </StrictMode>,
  );
  return onReset;
}

it('does not consume a fragment token on GET or StrictMode mount and removes it promptly', async () => {
  history.replaceState(null, '', '/verify-email#token=' + token);
  const fetcher = vi.fn((path: string) =>
    response(
      path === '/api/auth/registration'
        ? { enabled: true, email_available: true }
        : { message: 'Email verified. You can log in.' },
    ),
  );
  mount(fetcher);
  expect(location.hash).toBe('');
  expect(fetcher.mock.calls.every(([path]) => path === '/api/auth/registration')).toBe(true);
  expect(screen.getByRole('heading', { name: 'Confirm your email' })).toHaveFocus();
  fireEvent.click(screen.getByRole('button', { name: 'Verify email' }));
  expect(await screen.findByRole('status')).toHaveTextContent('Email verified. You can log in.');
  const sent = fetcher.mock.calls.find(([path]) => path === '/api/auth/verify-email');
  expect(sent).toBeTruthy();
  expect(JSON.stringify(localStorage)).not.toContain(token);
});

it('registers without a session and displays verification guidance', async () => {
  history.replaceState(null, '', '/register');
  const fetcher = vi.fn((path: string) =>
    response(
      path === '/api/auth/registration'
        ? { enabled: true, email_available: true }
        : {
            message:
              'If the address is eligible, an email will arrive. Only the newest link works.',
          },
    ),
  );
  mount(fetcher);
  await screen.findByLabelText('Username');
  fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'Raven' } });
  fireEvent.change(screen.getByLabelText('Email address'), {
    target: { value: 'raven@example.test' },
  });
  for (const field of ['Password', 'Confirm password'])
    fireEvent.change(screen.getByLabelText(field, { exact: true }), {
      target: { value: 'test-password-never-store' },
    });
  expect(screen.getByLabelText('Password', { exact: true })).toHaveAttribute(
    'autocomplete',
    'new-password',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
  expect(await screen.findByRole('heading', { name: 'Verify your email' })).toBeVisible();
  expect(screen.getByRole('status')).toHaveTextContent('If the address is eligible');
  expect(fetcher.mock.calls.some(([path]) => path === '/api/auth/login')).toBe(false);
  expect(JSON.stringify(localStorage)).not.toContain('test-password-never-store');
});

it('handles an email fragment opened in the same tab as the pending-verification page', async () => {
  history.replaceState(null, '', '/verify-email');
  const fetcher = vi.fn((_path: string) => response({ enabled: true }));
  mount(fetcher);
  expect(screen.getByRole('heading', { name: 'Verify your email' })).toBeVisible();
  history.replaceState(null, '', '/verify-email#token=' + token);
  fireEvent(window, new HashChangeEvent('hashchange'));
  expect(screen.getByRole('heading', { name: 'Confirm your email' })).toBeVisible();
  expect(location.hash).toBe('');
  expect(fetcher.mock.calls.every(([path]) => path === '/api/auth/registration')).toBe(true);
});

it('resets only on explicit submit and clears the old client session', async () => {
  history.replaceState(null, '', '/reset-password#token=' + token);
  const fetcher = vi.fn((path: string) =>
    response(
      path === '/api/auth/registration'
        ? { enabled: true }
        : { message: 'Password reset. Log in with your new password.' },
    ),
  );
  const onReset = mount(fetcher);
  expect(onReset).not.toHaveBeenCalled();
  for (const field of ['Password', 'Confirm password'])
    fireEvent.change(screen.getByLabelText(field, { exact: true }), {
      target: { value: 'a-new-test-password' },
    });
  fireEvent.click(screen.getByRole('button', { name: 'Set new password' }));
  expect(await screen.findByRole('status')).toHaveTextContent(
    'Password reset. Log in with your new password.',
  );
  expect(onReset).toHaveBeenCalledOnce();
  expect(fetcher.mock.calls.some(([path]) => path === '/api/auth/login')).toBe(false);
});

it('offers recovery for invalid or replaced tokens without consuming on navigation', async () => {
  history.replaceState(null, '', '/verify-email#token=' + token);
  const fetcher = vi.fn((path: string) =>
    path === '/api/auth/registration'
      ? response({ enabled: true })
      : response({ detail: 'This link is invalid, expired or replaced. Request a new link.' }, 400),
  );
  mount(fetcher);
  fireEvent.click(screen.getByRole('button', { name: 'Verify email' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('invalid, expired or replaced');
  fireEvent.click(screen.getByRole('button', { name: 'Request a new link' }));
  expect(screen.getByLabelText('Email address')).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Verify your email' })).toHaveFocus();
});

it('localizes disabled registration and recovery controls into German', async () => {
  localStorage.setItem('hnefatafl-language', 'de');
  history.replaceState(null, '', '/register');
  mount(vi.fn(() => response({ enabled: false })));
  expect(await screen.findByRole('status')).toHaveTextContent(
    'Die Registrierung ist derzeit nicht verfügbar.',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Zurück zur Anmeldung' }));
  expect(screen.getByRole('button', { name: 'Passwort vergessen?' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Passwort vergessen?' }));
  expect(screen.getByLabelText('E-Mail-Adresse')).toBeVisible();
});

it('does not submit mismatched passwords and renders errors as text', async () => {
  history.replaceState(null, '', '/reset-password#token=' + token);
  const fetcher = vi.fn((_path: string) => response({ enabled: true }));
  mount(fetcher);
  fireEvent.change(screen.getByLabelText('Password', { exact: true }), {
    target: { value: 'first-test-password' },
  });
  fireEvent.change(screen.getByLabelText('Confirm password'), {
    target: { value: 'second-test-password' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Set new password' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Passwords do not match.');
  await waitFor(() =>
    expect(fetcher.mock.calls.every(([path]) => path === '/api/auth/registration')).toBe(true),
  );
});
