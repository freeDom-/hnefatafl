import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import PlayLanding from './PlayLanding';

const identity = vi.hoisted(() => ({ id: 'archer', permissions: ['play'], email_verified: true }));
vi.mock('./Auth', () => ({ useAuth: () => ({ account: identity }) }));
const fetcher = vi.fn();
beforeEach(() => {
  identity.email_verified = true;
  history.replaceState(null, '', '/play');
  localStorage.clear();
  fetcher.mockReset();
  fetcher.mockResolvedValue({ ok: true, status: 200, json: async () => ({ games: [] }) });
  vi.stubGlobal('fetch', fetcher);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
  history.replaceState(null, '', '/');
});

it('shows Dashboard discovery and the shared creation actions when no games are running', async () => {
  const create = vi.fn();
  render(<PlayLanding onNewSingle={create} creatingSingle={false} />);
  await screen.findByText('No games are currently running.');
  expect(screen.getByRole('link', { name: 'Find online games on Dashboard' })).toHaveAttribute(
    'href',
    '/dashboard',
  );
  fireEvent.click(screen.getByRole('button', { name: 'New single-player game' }));
  expect(create).toHaveBeenCalledOnce();
});
it('resumes the last selected game only when it appears in the current account list', async () => {
  const route = '/online/games/online-' + 'a'.repeat(32);
  localStorage.setItem('hnefatafl-play-route', route);
  fetcher.mockImplementation((url: string) =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: async () => ({
        games: url.includes('/personal/online')
          ? [{ id: 'online-' + 'a'.repeat(32), status: 'active' }]
          : [{ id: 'own-single', status: 'in_progress' }],
      }),
    }),
  );
  render(<PlayLanding onNewSingle={vi.fn()} creatingSingle={false} />);
  await waitFor(() => expect(location.pathname).toBe(route));
  expect(fetcher.mock.calls.every(([url]) => url.startsWith('/api/personal/'))).toBe(true);
});
it('does not resume a finished or inaccessible cached game and disables online creation for an unverified email', async () => {
  identity.email_verified = false;
  localStorage.setItem('hnefatafl-play-route', '/games/another-account');
  render(<PlayLanding onNewSingle={vi.fn()} creatingSingle={false} />);
  await screen.findByText('No games are currently running.');
  expect(location.pathname).toBe('/play');
  expect(screen.getByRole('button', { name: 'New online game' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'New single-player game' })).toBeEnabled();
});
