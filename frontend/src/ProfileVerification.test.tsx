import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useState } from 'react';
import Auth, { useAuth } from './Auth';
import ProfilePage from './ProfilePage';
import { I18nProvider, setLanguage } from './i18n';

vi.unmock('./Auth');
const reply = (value: unknown, status = 200) =>
  ({ ok: status < 400, status, json: async () => value }) as Response;
function deferred() {
  let resolve!: (value: Response) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<Response>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function session(verified: boolean, name = 'Odin') {
  return {
    account: {
      id: name,
      username: name,
      email: name + '@example.test',
      role: 'USER',
      permissions: ['play', 'profile'],
      email_verified: verified,
    },
    csrf_token: 'csrf-' + name,
  };
}
function profile(verified: boolean, name = 'Odin') {
  return {
    ...session(verified, name).account,
    display_name: null,
    country_code: null,
    city: null,
    date_of_birth: null,
    legacy_birth_year: null,
    legacy_private_fields: [],
    country_codes: ['DE'],
  };
}
function Workspace({ initiallyOpen }: { initiallyOpen: boolean }) {
  const { account, logout } = useAuth();
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <>
      <output aria-label="Shared verification">
        {account?.username}:{String(account?.email_verified)}
      </output>
      <button onClick={() => setOpen((value) => !value)}>
        {open ? 'Hide profile' : 'Show profile'}
      </button>
      <button onClick={() => void logout()}>Session logout</button>
      {open && <ProfilePage />}
    </>
  );
}
function server(initiallyVerified = false) {
  const profiles: ReturnType<typeof deferred>[] = [];
  const refreshes: ReturnType<typeof deferred>[] = [];
  const profileSignals: AbortSignal[] = [];
  let reads = 0;
  const fetcher = vi.fn((path: string, options?: RequestInit) => {
    if (path === '/api/auth/me') {
      if (++reads === 1) return Promise.resolve(reply(session(initiallyVerified)));
      const waiting = deferred();
      refreshes.push(waiting);
      return waiting.promise;
    }
    if (path === '/api/profile') {
      const waiting = deferred();
      profiles.push(waiting);
      profileSignals.push(options!.signal as AbortSignal);
      return waiting.promise;
    }
    return Promise.resolve(
      reply(path === '/api/auth/logout' ? { logged_out: true } : { enabled: false }),
    );
  });
  vi.stubGlobal('fetch', fetcher);
  return { profiles, refreshes, profileSignals, fetcher, reads: () => reads };
}
async function mount(initiallyOpen = true) {
  let view!: ReturnType<typeof render>;
  await act(async () => {
    view = render(
      <I18nProvider>
        <Auth>
          <Workspace initiallyOpen={initiallyOpen} />
        </Auth>
      </I18nProvider>,
    );
  });
  await screen.findByLabelText('Shared verification');
  return view;
}
beforeEach(() => {
  history.replaceState(null, '', '/profile');
  localStorage.clear();
  setLanguage('en', false);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  history.replaceState(null, '', '/');
  setLanguage('en', false);
});

it.each([
  [false, true],
  [true, false],
])('merges fresh profile status %s → %s into Auth without another request', async (old, fresh) => {
  const api = server(old);
  await mount();
  await act(async () => api.profiles[0].resolve(reply(profile(fresh))));
  expect(screen.getByLabelText('Shared verification')).toHaveTextContent('Odin:' + fresh);
  if (fresh)
    expect(
      screen.queryByRole('button', { name: 'Send Verification Email' }),
    ).not.toBeInTheDocument();
  else expect(screen.getByRole('button', { name: 'Send Verification Email' })).toBeVisible();
  expect(api.reads()).toBe(1);
  expect(api.profiles).toHaveLength(1);
});

it.each([
  [false, true],
  [true, false],
])(
  'does not let an older auth response overwrite a newer profile observation %s → %s',
  async (old, fresh) => {
    const api = server(old);
    await mount(false);
    act(() => fireEvent(window, new Event('focus')));
    fireEvent.click(screen.getByRole('button', { name: 'Show profile' }));
    await act(async () => api.profiles[0].resolve(reply(profile(fresh))));
    await act(async () => api.refreshes[0].resolve(reply(session(old))));
    expect(screen.getByLabelText('Shared verification')).toHaveTextContent('Odin:' + fresh);
    expect(api.reads()).toBe(2);
    expect(api.profiles).toHaveLength(1);
  },
);

it('does not let an older profile response undo a later successful auth refresh', async () => {
  const api = server(false);
  await mount();
  act(() => fireEvent(window, new Event('focus')));
  await act(async () => api.refreshes[0].resolve(reply(session(true))));
  await act(async () => api.profiles[0].resolve(reply(profile(false))));
  expect(screen.getByLabelText('Shared verification')).toHaveTextContent('Odin:true');
  expect(screen.queryByRole('button', { name: 'Send Verification Email' })).not.toBeInTheDocument();
  expect(api.profiles).toHaveLength(1);
});

it('also merges fresh verification returned by a successful profile save', async () => {
  const api = server(false);
  await mount();
  await act(async () => api.profiles[0].resolve(reply(profile(false))));
  fireEvent.change(screen.getByRole('textbox', { name: 'City' }), { target: { value: 'Berlin' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save Profile' }));
  await act(async () => api.profiles[1].resolve(reply({ ...profile(true), city: 'Berlin' })));
  expect(screen.getByLabelText('Shared verification')).toHaveTextContent('Odin:true');
  expect(screen.getByText('Profile saved.')).toBeVisible();
  expect(api.reads()).toBe(1);
});

it.each(['network', 'server'])(
  'retains the session on %s refresh failure and accepts an earlier successful profile read',
  async (failure) => {
    const api = server(false);
    await mount();
    act(() => fireEvent(window, new Event('focus')));
    await act(async () => {
      if (failure === 'network') api.refreshes[0].reject(new TypeError('Network unavailable.'));
      else api.refreshes[0].resolve(reply({ detail: 'Unavailable' }, 503));
    });
    await act(async () => api.profiles[0].resolve(reply(profile(true))));
    expect(screen.getByLabelText('Shared verification')).toHaveTextContent('Odin:true');
    expect(api.reads()).toBe(2);
  },
);

it('preserves known verification and account security when optional profile loading fails', async () => {
  const api = server(true);
  await mount();
  await act(async () => api.profiles[0].reject(new TypeError('Network unavailable.')));
  expect(await screen.findByRole('alert')).toHaveTextContent('Network unavailable.');
  expect(screen.getByLabelText('Shared verification')).toHaveTextContent('Odin:true');
  expect(screen.getByRole('button', { name: 'Change Password' })).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Send Verification Email' })).not.toBeInTheDocument();
  expect(api.reads()).toBe(1);
});

it('coalesces invalidations during an auth refresh into one follow-up and observes another session’s confirmation', async () => {
  const api = server(false);
  await mount(false);
  act(() => {
    fireEvent(window, new Event('focus'));
    for (let i = 0; i < 3; i++) fireEvent(window, new Event('permissions-changed'));
  });
  expect(api.reads()).toBe(2);
  await act(async () => api.refreshes[0].resolve(reply(session(false))));
  await waitFor(() => expect(api.reads()).toBe(3));
  await act(async () => api.refreshes[1].resolve(reply(session(true))));
  expect(screen.getByLabelText('Shared verification')).toHaveTextContent('Odin:true');
  expect(api.reads()).toBe(3);
});

it('does not commit profile data after the page unmounts', async () => {
  const api = server(false);
  await mount();
  fireEvent.click(screen.getByRole('button', { name: 'Hide profile' }));
  expect(api.profileSignals[0].aborted).toBe(true);
  await act(async () => api.profiles[0].resolve(reply(profile(true))));
  expect(screen.getByLabelText('Shared verification')).toHaveTextContent('Odin:false');
  expect(api.reads()).toBe(1);
});

it('discards old profile data across an account switch', async () => {
  const api = server(false);
  await mount();
  act(() => fireEvent(window, new Event('focus')));
  await act(async () => api.refreshes[0].resolve(reply(session(false, 'Raven'))));
  await waitFor(() => expect(api.profiles).toHaveLength(2));
  await act(async () => api.profiles[0].resolve(reply(profile(true))));
  await act(async () => api.profiles[1].resolve(reply(profile(false, 'Raven'))));
  expect(screen.getByLabelText('Shared verification')).toHaveTextContent('Raven:false');
  expect(api.profileSignals[0].aborted).toBe(true);
});

it.each([401, 403])('still clears expired/revoked authentication on HTTP %i', async (status) => {
  const api = server(true);
  await mount();
  act(() => fireEvent(window, new Event('focus')));
  await act(async () =>
    api.refreshes[0].resolve(reply({ detail: 'Authentication required' }, status)),
  );
  expect(await screen.findByRole('heading', { name: 'HNEFATAFL' })).toBeVisible();
  expect(api.profileSignals[0].aborted).toBe(true);
  await act(async () => api.profiles[0].resolve(reply(profile(true))));
  expect(screen.queryByLabelText('Shared verification')).not.toBeInTheDocument();
});

it('does not restore verification after logout or Auth unmounting', async () => {
  const api = server(false);
  const view = await mount();
  act(() => fireEvent(window, new Event('focus')));
  fireEvent.click(screen.getByRole('button', { name: 'Session logout' }));
  await screen.findByRole('heading', { name: 'HNEFATAFL' });
  view.unmount();
  await act(async () => {
    api.profiles[0].resolve(reply(profile(true)));
    api.refreshes[0].resolve(reply(session(true)));
  });
  expect(screen.queryByLabelText('Shared verification')).not.toBeInTheDocument();
  expect(api.profileSignals[0].aborted).toBe(true);
});
