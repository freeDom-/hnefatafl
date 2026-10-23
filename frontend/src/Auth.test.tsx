import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Auth, { useAuth } from './Auth';
import { useState } from 'react';
vi.unmock('./Auth');

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function Protected() {
  const { account, logout } = useAuth();
  return (
    <>
      <p>Protected gameplay for {account?.username}</p>
      <button onClick={() => void logout()}>Log out</button>
    </>
  );
}
function RememberedProfile() {
  const { account } = useAuth();
  const [name] = useState(account?.username);
  return <p>Cached profile {name}</p>;
}
const response = (value: unknown, status = 200) =>
  Promise.resolve({ ok: status === 200, status, json: async () => value } as Response);
describe('authenticated application boundary', () => {
  it('discards protected component state when another tab changes accounts', async () => {
    let name = 'First';
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        response({
          account: { id: name, username: name, permissions: ['play'] },
          csrf_token: 'csrf',
        }),
      ),
    );
    render(
      <Auth>
        <RememberedProfile />
      </Auth>,
    );
    expect(await screen.findByText('Cached profile First')).toBeVisible();
    name = 'Second';
    fireEvent(window, new Event('focus'));
    expect(await screen.findByText('Cached profile Second')).toBeVisible();
    expect(screen.queryByText('Cached profile First')).toBeNull();
  });
  it('restores sessions, clears protected state on logout, and keeps registration unavailable', async () => {
    const fetcher = vi.fn((path: string) =>
      path === '/api/auth/logout'
        ? response({ logged_out: true })
        : response({ account: { username: 'Odin', permissions: ['play'] }, csrf_token: 'csrf' }),
    );
    vi.stubGlobal('fetch', fetcher);
    render(
      <Auth>
        <Protected />
      </Auth>,
    );
    expect(await screen.findByText('Protected gameplay for Odin')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Log out' }));
    expect(await screen.findByRole('heading', { name: 'Log in' })).toBeVisible();
    expect(screen.queryByText('Protected gameplay for Odin')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Sign up / Register' }));
    expect(screen.getByRole('status')).toHaveTextContent('Registration is not available yet.');
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('requires login before mounting gameplay and keeps passwords out of storage', async () => {
    const fetcher = vi.fn((path: string) =>
      path === '/api/auth/me'
        ? response({ detail: 'Authentication required' }, 401)
        : response({ account: { username: 'Raven', permissions: ['play'] }, csrf_token: 'csrf' }),
    );
    vi.stubGlobal('fetch', fetcher);
    render(
      <Auth>
        <Protected />
      </Auth>,
    );
    await screen.findByRole('heading', { name: 'Log in' });
    expect(screen.queryByText(/Protected gameplay/)).toBeNull();
    fireEvent.change(screen.getByLabelText('Username or email'), { target: { value: 'Raven' } });
    const password = screen.getByLabelText('Password');
    expect(password).toHaveAttribute('type', 'password');
    fireEvent.change(password, { target: { value: 'never-store-this-password' } });
    fireEvent.click(screen.getByRole('button', { name: 'Log in' }));
    await waitFor(() => expect(screen.getByText('Protected gameplay for Raven')).toBeVisible());
    expect(JSON.stringify(localStorage)).not.toContain('never-store-this-password');
  });
});
