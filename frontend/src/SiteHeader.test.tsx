import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import GuestNavigation from './GuestNavigation';
import SiteHeader from './SiteHeader';
vi.unmock('./Auth');

const account = {
  id: 'archer',
  username: 'Archer',
  email: 'archer@example.test',
  role: 'USER',
  permissions: ['play'],
};
const response = (value: unknown, status = 200) =>
  ({ ok: status === 200, status, json: async () => value }) as Response;
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  history.replaceState(null, '', '/');
  localStorage.clear();
});

it('uses the shared authenticated navigation without a Guest play or login action', () => {
  render(<SiteHeader account={account} />);
  expect(screen.getByRole('button', { name: 'Dashboard' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Hall of records' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'AI workshop' })).toBeNull();
  expect(screen.queryByRole('link', { name: /Guest play|Log in/ })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Dashboard' }));
  expect(location.pathname).toBe('/dashboard');
});

it('shows only Log in and shared utilities for anonymous guest navigation', async () => {
  history.replaceState(null, '', '/guest');
  const fetcher = vi.fn(async (_path: string) =>
    response({ detail: 'Authentication required' }, 401),
  );
  vi.stubGlobal('fetch', fetcher);
  render(<GuestNavigation />);
  const login = await screen.findByRole('link', { name: 'Log in' });
  expect(login).toHaveAttribute('href', '/login');
  expect(login).toHaveClass('header-link', 'button', 'secondary');
  expect(
    screen.queryByRole('link', { name: /Guest play|Account single-player|Online multiplayer/ }),
  ).toBeNull();
  expect(screen.getByRole('button', { name: 'Appearance settings' })).toBeVisible();
  expect(fetcher.mock.calls.every(([path]) => path === '/api/auth/me')).toBe(true);
  fireEvent.click(login);
  expect(location.pathname).toBe('/login');
});

it('waits for the guest header session before offering login and supports logout in place', async () => {
  history.replaceState(null, '', '/guest');
  localStorage.setItem('hnefatafl-account-cache', 'prior-account');
  localStorage.setItem('hnefatafl-game', 'retained-selection');
  let accept!: (value: Response) => void;
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) =>
      path === '/api/auth/me'
        ? new Promise<Response>((resolve) => {
            accept = resolve;
          })
        : Promise.resolve(response({ logged_out: true })),
    ),
  );
  render(<GuestNavigation />);
  expect(screen.queryByRole('link', { name: 'Log in' })).toBeNull();
  await act(async () => {
    accept(response({ account, csrf_token: 'csrf' }));
  });
  expect(await screen.findByText('Archer')).toBeVisible();
  expect(localStorage.getItem('hnefatafl-game')).toBe('retained-selection');
  expect(localStorage.getItem('hnefatafl-account-cache')).toBe('prior-account');
  expect(screen.queryByRole('link', { name: /Guest play|Log in/ })).toBeNull();
  expect(screen.getByRole('button', { name: 'Dashboard' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Log out' }));
  await waitFor(() => expect(screen.getByRole('link', { name: 'Log in' })).toBeVisible());
  expect(location.pathname).toBe('/guest');
  expect(localStorage.getItem('hnefatafl-game')).toBeNull();
  expect(screen.queryByRole('heading', { name: 'Log in' })).toBeNull();
});
