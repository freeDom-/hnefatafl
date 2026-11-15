import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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
    // Finish the restored session's passive effects before firing focus. Seeing
    // the profile in the DOM alone does not guarantee the listener is attached.
    await act(async () => {
      render(
        <Auth>
          <RememberedProfile />
        </Auth>,
      );
    });
    expect(await screen.findByText('Cached profile First')).toBeVisible();
    sessionStorage.setItem('hnefatafl-online-intent', 'first-account-request');
    // Another tab already wrote the shared cache before this tab refreshes.
    localStorage.setItem('hnefatafl-account-cache', 'Second');
    name = 'Second';
    await act(async () => {
      fireEvent(window, new Event('focus'));
    });
    expect(await screen.findByText('Cached profile Second')).toBeVisible();
    expect(screen.queryByText('Cached profile First')).toBeNull();
    expect(sessionStorage.getItem('hnefatafl-online-intent')).toBeNull();
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
    expect(await screen.findByRole('heading', { name: 'HNEFATAFL' })).toBeVisible();
    expect(screen.queryByText('Protected gameplay for Odin')).toBeNull();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Sign up / Register' })).toBeEnabled(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Sign up / Register' }));
    expect(screen.getByRole('status')).toHaveTextContent('Registration is not available yet.');
    expect(fetcher).toHaveBeenCalledTimes(3);
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
    await screen.findByRole('heading', { name: 'HNEFATAFL' });
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

it('does not restore an old session response after logout and clears the retained online intent immediately', async () => {
  const first = {
    account: { id: 'first', username: 'First', permissions: ['play'] },
    csrf_token: 'csrf',
  };
  let finishRefresh!: (response: Response) => void;
  let finishLogout!: (response: Response) => void;
  let reads = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) => {
      if (path === '/api/auth/me') {
        reads++;
        return reads === 1
          ? response(first)
          : new Promise<Response>((resolve) => {
              finishRefresh = resolve;
            });
      }
      if (path === '/api/auth/logout')
        return new Promise<Response>((resolve) => {
          finishLogout = resolve;
        });
      return response({ enabled: false });
    }),
  );
  await act(async () => {
    render(
      <Auth>
        <Protected />
      </Auth>,
    );
  });
  sessionStorage.setItem('hnefatafl-online-intent', 'protected-request');
  act(() => {
    fireEvent(window, new Event('focus'));
  });
  fireEvent.click(screen.getByRole('button', { name: 'Log out' }));
  await screen.findByText('Logging out…');
  expect(sessionStorage.getItem('hnefatafl-online-intent')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Log in' })).toBeNull();
  await act(async () => {
    finishRefresh(await response(first));
    finishLogout(await response({ logged_out: true }));
  });
  await screen.findByRole('heading', { name: 'HNEFATAFL' });
  expect(screen.queryByText('Protected gameplay for First')).toBeNull();
});

it('pauses periodic session reads while hidden, then revalidates and isolates the replacement account', async () => {
  vi.useFakeTimers();
  let name = 'First';
  const fetcher = vi.fn((_path: string) =>
    response({ account: { id: name, username: name, permissions: ['play'] }, csrf_token: 'csrf' }),
  );
  vi.stubGlobal('fetch', fetcher);
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
  try {
    await act(async () => {
      render(
        <Auth>
          <RememberedProfile />
        </Auth>,
      );
    });
    expect(screen.getByText('Cached profile First')).toBeVisible();
    const count = () =>
      fetcher.mock.calls.filter(([path]) => String(path) === '/api/auth/me').length;
    const initial = count();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });
    expect(count()).toBe(initial + 1);
    visibility.mockReturnValue('hidden');
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
      window.dispatchEvent(new Event('focus'));
    });
    name = 'Second';
    await act(async () => {
      await vi.advanceTimersByTimeAsync(180000);
    });
    expect(count()).toBe(initial + 1);
    visibility.mockReturnValue('visible');
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(count()).toBe(initial + 2);
    expect(screen.getByText('Cached profile Second')).toBeVisible();
    expect(screen.queryByText('Cached profile First')).toBeNull();
  } finally {
    cleanup();
    visibility.mockRestore();
    vi.useRealTimers();
  }
});

it('does not accept an aborted hidden session read and queues a fresh read on visibility', async () => {
  vi.useFakeTimers();
  let resolveOld!: (value: Response) => void;
  const old = new Promise<Response>((resolve) => {
    resolveOld = resolve;
  });
  let call = 0;
  const fetcher = vi.fn((_path: string, _options?: RequestInit) => {
    call++;
    return call === 2
      ? old
      : response({
          account: {
            id: call === 1 ? 'First' : 'Current',
            username: call === 1 ? 'First' : 'Current',
            permissions: ['play'],
          },
          csrf_token: 'csrf',
        });
  });
  vi.stubGlobal('fetch', fetcher);
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible');
  try {
    await act(async () => {
      render(
        <Auth>
          <RememberedProfile />
        </Auth>,
      );
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });
    const signal = fetcher.mock.calls[1][1]?.signal as AbortSignal;
    visibility.mockReturnValue('hidden');
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    expect(signal.aborted).toBe(true);
    visibility.mockReturnValue('visible');
    act(() => document.dispatchEvent(new Event('visibilitychange')));
    await act(async () => {
      resolveOld(
        await response({
          account: { id: 'Stale', username: 'Stale', permissions: ['play'] },
          csrf_token: 'csrf',
        }),
      );
      await old;
    });
    expect(screen.queryByText('Cached profile Stale')).toBeNull();
    expect(screen.getByText('Cached profile Current')).toBeVisible();
    expect(fetcher).toHaveBeenCalledTimes(3);
  } finally {
    cleanup();
    visibility.mockRestore();
    vi.useRealTimers();
  }
});
