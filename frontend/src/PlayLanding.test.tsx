import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import PlayLanding from './PlayLanding';

const identity = vi.hoisted(() => ({ id: 'archer', permissions: ['play'], email_verified: true }));
vi.mock('./Auth', () => ({ useAuth: () => ({ account: identity }) }));
const fetcher = vi.fn();
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute('open', '');
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.removeAttribute('open');
  });
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
it('shows the continue list and the three equal-start play modes', async () => {
  const onlineId = 'online-' + 'b'.repeat(32);
  fetcher.mockImplementation((url: string) =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: async () => ({
        games: url.includes('/personal/online')
          ? [{ id: onlineId, status: 'active', ruleset: 'tablut', your_turn: true }]
          : url.includes('/personal/local')
            ? [{ id: 'local-running', status: 'in_progress', ruleset: 'brandubh' }]
            : [
                {
                  id: 'own-single',
                  status: 'in_progress',
                  ruleset: 'copenhagen',
                  mode: 'human-ai',
                },
              ],
      }),
    }),
  );
  // An open creation dialog suppresses the automatic resume, so the hierarchy stays visible.
  render(<PlayLanding onNewSingle={vi.fn()} creatingSingle />);
  await screen.findByRole('heading', { name: 'Your turn / Continue' });
  expect(screen.getByRole('heading', { name: 'Play online' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Local 1v1' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Play vs AI' })).toBeVisible();
  const continueLinks = screen.getAllByRole('link', { name: /^Continue/ });
  expect(continueLinks.map((link) => link.getAttribute('href'))).toEqual([
    `/online/games/${onlineId}`,
    '/games/local-running',
    '/games/own-single',
  ]);
  // Only the server-confirmed turn is presented as awaiting this account.
  expect(continueLinks[0].closest('li')).toHaveTextContent('Your turn');
  expect(continueLinks[2].closest('li')).not.toHaveTextContent('Your turn');
});
it('creates a local 1v1 game with server-owned seat labels', async () => {
  fetcher.mockImplementation((url: string, options?: RequestInit) => {
    if (options?.method === 'POST')
      return Promise.resolve({
        ok: true,
        status: 201,
        json: async () => ({ id: 'local-created' }),
      } as Response);
    if (url === '/api/rulesets')
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => [{ id: 'tablut', name: 'Tablut', size: 9 }],
      } as Response);
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => ({ games: [] }),
    } as Response);
  });
  render(<PlayLanding onNewSingle={vi.fn()} creatingSingle={false} />);
  await screen.findByText('No games are currently running.');
  fireEvent.click(screen.getByRole('button', { name: 'New local 1v1 game' }));
  await screen.findByRole('dialog', { name: 'Start a local 1v1 game' });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Start game' })).toBeEnabled());
  fireEvent.change(screen.getByLabelText('Attackers seat label'), { target: { value: 'Odin' } });
  fireEvent.change(screen.getByLabelText('Defenders seat label'), { target: { value: 'Jarl' } });
  fireEvent.click(screen.getByRole('button', { name: 'Start game' }));
  await waitFor(() => expect(location.pathname).toBe('/games/local-created'));
  const call = fetcher.mock.calls.find(([, options]) => options?.method === 'POST');
  expect(call![0]).toBe('/api/games/local');
  expect(JSON.parse(String(call![1]?.body))).toEqual({
    ruleset: 'tablut',
    attackers_label: 'Odin',
    defenders_label: 'Jarl',
  });
});
it('requires both local seat labels before creating the game', async () => {
  fetcher.mockImplementation((url: string) =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: async () =>
        url === '/api/rulesets' ? [{ id: 'tablut', name: 'Tablut', size: 9 }] : { games: [] },
    }),
  );
  render(<PlayLanding onNewSingle={vi.fn()} creatingSingle={false} />);
  await screen.findByText('No games are currently running.');
  fireEvent.click(screen.getByRole('button', { name: 'New local 1v1 game' }));
  await screen.findByRole('dialog', { name: 'Start a local 1v1 game' });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Start game' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Start game' }));
  expect(await screen.findByText('Both local seats need a label.')).toBeVisible();
  expect(fetcher.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false);
});
