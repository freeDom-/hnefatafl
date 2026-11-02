import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import NewGame from './NewGame';
import { NotificationProvider, clearNotifications } from './notifications';
import type { AIProfile, Model, Ruleset } from './types';

const identity = vi.hoisted(() => ({ admin: false }));
vi.mock('./Auth', () => ({
  useAuth: () => ({
    account: {
      id: 'account-odin',
      username: 'Odin',
      permissions: identity.admin ? ['play', 'all_data'] : ['play'],
    },
  }),
}));

const rulesets: Ruleset[] = ['tablut', 'brandubh'].map((id) => ({
  id,
  name: id === 'tablut' ? 'Tablut' : 'Brandubh',
  size: 9,
  description: '',
  rules_markdown: '',
}));
const profiles: AIProfile[] = [
  ['baseline', 'Shield bearer', { time_limit: 1 }],
  ['shield_maiden', 'Shield maiden', { time_limit: 1, heuristic_version: 1 }],
  ['alpha_beta', 'Jarl', { time_limit: 2, depth: 5 }],
  ['mcts', 'Seer', { time_limit: 2, iterations: 5000, exploration: 1.4, rollout_depth: 8 }],
  ['neural', 'Trained model', { time_limit: 2 }],
].map(
  ([id, name, parameters]) =>
    ({
      id,
      name,
      algorithm: id,
      parameters,
      difficulty: '',
      supported_rulesets: ['tablut', 'brandubh'],
    }) as AIProfile,
);
const models: Model[] = [
  { id: 'a'.repeat(24), name: 'Hugin', ruleset: 'tablut' },
  { id: 'b'.repeat(24), name: 'Munin', ruleset: 'brandubh' },
];

const json = (value: unknown, ok = true) =>
  Promise.resolve({ ok, json: async () => value } as Response);
const fetcher = vi.fn((_path: string, _init?: RequestInit) =>
  _path.includes('/models') ? json(models) : json({ id: 'created' }),
);
/** The creation request body actually sent to the API. */
const creationBody = () =>
  JSON.parse(String(fetcher.mock.calls.find(([path]) => path === '/api/games')?.[1]?.body));
const show = (onCreated = vi.fn(), onClose = vi.fn()) =>
  render(
    <NotificationProvider>
      <NewGame
        rulesets={rulesets}
        profiles={profiles}
        models={models}
        onCreated={onCreated}
        onClose={onClose}
      />
    </NotificationProvider>,
  );

beforeEach(() => {
  identity.admin = false;
  clearNotifications();
  localStorage.clear();
  fetcher
    .mockReset()
    .mockImplementation((path: string) =>
      path.includes('/models') ? json(models) : json({ id: 'created' }),
    );
  vi.stubGlobal('fetch', fetcher);
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute('open', '');
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.removeAttribute('open');
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it('offers the custom opponent only to administrators in single-player mode', () => {
  show();
  expect(screen.queryByRole('option', { name: 'Custom opponent' })).toBeNull();
  cleanup();

  identity.admin = true;
  show();
  expect(screen.getByRole('option', { name: 'Custom opponent' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'AI vs AI' }));
  expect(screen.queryByRole('option', { name: 'Custom opponent' })).toBeNull();
});

it('loads models lazily and sends the configured agent for the custom opponent', async () => {
  identity.admin = true;
  show();
  expect(fetcher.mock.calls.some(([path]) => String(path).includes('/models'))).toBe(false);

  fireEvent.change(screen.getByLabelText('Attackers AI profile'), { target: { value: 'custom' } });
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([path]) => String(path).includes('/models'))).toBe(true),
  );

  const algorithm = screen.getByLabelText('Attackers algorithm');
  expect(
    within(algorithm)
      .getAllByRole('option')
      .map((o) => o.textContent),
  ).toEqual([
    'Tactical heuristic',
    'Shield maiden heuristic',
    'Alpha-beta search',
    'Monte Carlo tree search',
    'Neural network',
  ]);

  fireEvent.change(algorithm, { target: { value: 'alpha_beta' } });
  expect(screen.getByLabelText('Attackers search depth')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Attackers search depth'), { target: { value: '4' } });
  fireEvent.change(screen.getByLabelText('Attackers time limit (seconds)'), {
    target: { value: '0.5' },
  });

  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/games', expect.anything()));
  const body = creationBody();
  expect(body).toEqual({
    ruleset: 'tablut',
    mode: 'human-ai',
    human_side: 'defenders',
    players: {
      attackers: {
        config: {
          algorithm: 'alpha_beta',
          depth: 4,
          time_limit: 0.5,
          search: expect.anything(),
        },
      },
    },
  });
});

it('reveals the model and wrapper controls for a neural custom opponent', async () => {
  identity.admin = true;
  show();
  fireEvent.change(screen.getByLabelText('Attackers AI profile'), { target: { value: 'custom' } });
  fireEvent.change(await screen.findByLabelText('Attackers algorithm'), {
    target: { value: 'neural' },
  });

  const model = screen.getByLabelText('Attackers trained model');
  // Only models for the selected battlefield are offered.
  const modelOptions = within(model)
    .getAllByRole('option')
    .map((o) => o.textContent ?? '')
    .join(' ');
  expect(modelOptions).toContain('Hugin');
  expect(modelOptions).not.toContain('Munin');
  fireEvent.change(model, { target: { value: 'a'.repeat(24) } });
  fireEvent.change(screen.getByLabelText('Attackers search wrapper'), {
    target: { value: 'none' },
  });
  fireEvent.change(screen.getByLabelText('Attackers temperature'), { target: { value: '0.5' } });

  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/games', expect.anything()));
  const body = creationBody();
  expect(body.players.attackers.config).toMatchObject({
    algorithm: 'neural',
    model_id: 'a'.repeat(24),
    search_wrapper: 'none',
    temperature: 0.5,
  });
});

it('keeps sending a bare preset for the existing opponent options', async () => {
  identity.admin = true;
  show();
  fireEvent.change(screen.getByLabelText('Attackers AI profile'), { target: { value: 'mcts' } });
  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/games', expect.anything()));
  const body = creationBody();
  expect(body.players).toEqual({ attackers: { profile_id: 'mcts' } });
});

it('never persists a custom configuration as a browser preference', async () => {
  identity.admin = true;
  show();
  fireEvent.change(screen.getByLabelText('Attackers AI profile'), { target: { value: 'custom' } });
  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/games', expect.anything()));
  const saved = JSON.parse(localStorage.getItem('hnefatafl-new-game-options') ?? '{}');
  expect(saved.custom).toBeUndefined();
  expect(saved.profiles.attackers).toBe('baseline');
});
