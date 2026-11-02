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
const HUGIN = 'a'.repeat(24);
const MUNIN = 'b'.repeat(24);
const catalogue: Model[] = [
  { id: HUGIN, name: 'Hugin', ruleset: 'tablut' },
  { id: MUNIN, name: 'Munin', ruleset: 'brandubh' },
];
const HUGIN_LABEL = 'Hugin · #aaaaaaaa';
const DEFAULT_SEARCH = {
  legacy: false,
  transposition: true,
  move_ordering: true,
  symmetry_transposition: false,
  symmetry_pruning: false,
  table_capacity: 100000,
};

const json = (value: unknown, ok = true) =>
  Promise.resolve({
    ok,
    status: ok ? 200 : 500,
    statusText: '',
    json: async () => value,
  } as Response);
/** Model requests the test resolves by hand, so ordering can be controlled. */
const modelRequests: { resolve: (value: Response) => void; reject: (reason: unknown) => void }[] =
  [];
const fetcher = vi.fn((path: string, _init?: RequestInit) => {
  if (path === '/api/models') {
    return new Promise<Response>((resolve, reject) => modelRequests.push({ resolve, reject }));
  }
  return json({ id: 'created' });
});
/** The most recent creation request body actually sent to the API. */
const creationBody = () =>
  JSON.parse(
    String(fetcher.mock.calls.filter(([path]) => path === '/api/games').at(-1)?.[1]?.body),
  );
const gamesRequests = () => fetcher.mock.calls.filter(([path]) => path === '/api/games').length;
const modelsRequests = () => fetcher.mock.calls.filter(([path]) => path === '/api/models');
const optionText = (label: string) =>
  within(screen.getByLabelText(label))
    .getAllByRole('option')
    .map((option) => option.textContent);
const show = ({
  models = catalogue,
  onCreated = vi.fn(),
  onClose = vi.fn(),
}: { models?: Model[]; onCreated?: () => void; onClose?: () => void } = {}) =>
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
const openCustom = async () => {
  fireEvent.change(screen.getByLabelText('Attackers AI profile'), { target: { value: 'custom' } });
  await screen.findByLabelText('Attackers algorithm');
};
const chooseNeural = async () => {
  fireEvent.change(screen.getByLabelText('Attackers algorithm'), { target: { value: 'neural' } });
  await screen.findByLabelText('Attackers search wrapper');
};
const chooseWrapper = async (wrapper: string) => {
  fireEvent.change(screen.getByLabelText('Attackers search wrapper'), {
    target: { value: wrapper },
  });
  await waitFor(() =>
    expect(screen.getByLabelText('Attackers search wrapper')).toHaveValue(wrapper),
  );
};
const submit = async (expected: number) => {
  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  await waitFor(() => expect(gamesRequests()).toBe(expected));
  await waitFor(() => expect(screen.getByRole('button', { name: 'To the table' })).toBeEnabled());
};
const settleModels = async (value: unknown) => {
  await waitFor(() => expect(modelRequests.length).toBeGreaterThan(0));
  modelRequests.shift()?.resolve(await json(value));
};
const failModels = async (reason: unknown) => {
  await waitFor(() => expect(modelRequests.length).toBeGreaterThan(0));
  modelRequests.shift()?.reject(reason);
};

beforeEach(() => {
  identity.admin = false;
  clearNotifications();
  localStorage.clear();
  modelRequests.length = 0;
  fetcher.mockClear();
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

it('keeps sending a bare preset for the existing opponent options', async () => {
  identity.admin = true;
  show();
  fireEvent.change(screen.getByLabelText('Attackers AI profile'), { target: { value: 'mcts' } });
  await submit(1);
  expect(creationBody().players).toEqual({ attackers: { profile_id: 'mcts' } });
});

it('never persists a custom configuration as a browser preference', async () => {
  identity.admin = true;
  show();
  await openCustom();
  await submit(1);
  const saved = JSON.parse(localStorage.getItem('hnefatafl-new-game-options') ?? '{}');
  expect(saved.custom).toBeUndefined();
  expect(saved.profiles.attackers).toBe('baseline');
});

it('reuses the catalogue the dashboard already loaded without requesting it again', async () => {
  identity.admin = true;
  show();
  await openCustom();
  expect(modelsRequests()).toHaveLength(0);
  await chooseNeural();
  expect(optionText('Attackers trained model')).toContain(HUGIN_LABEL);
  expect(modelsRequests()).toHaveLength(0);
});

it('loads the catalogue lazily when the dashboard has none', async () => {
  identity.admin = true;
  show({ models: [] });
  expect(modelsRequests()).toHaveLength(0);
  await openCustom();
  expect(modelsRequests()).toHaveLength(1);
  await settleModels([catalogue[0]]);
  await chooseNeural();
  await waitFor(() => expect(optionText('Attackers trained model')).toContain(HUGIN_LABEL));
});

it('reports a failed catalogue load and recovers when the request is retried', async () => {
  identity.admin = true;
  show({ models: [] });
  await openCustom();
  await failModels(new Error('Catalogue offline'));
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('Catalogue offline');
  // A failed catalogue never blocks the dialog; it only withholds model choices.
  await chooseNeural();
  expect(screen.getByRole('button', { name: 'To the table' })).toBeEnabled();

  fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
  await settleModels([catalogue[0]]);
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  await waitFor(() => expect(optionText('Attackers trained model')).toContain(HUGIN_LABEL));
});

it('treats a malformed catalogue payload as a load failure', async () => {
  identity.admin = true;
  show({ models: [] });
  await openCustom();
  await settleModels({ models: 'unexpected' });
  expect(await screen.findByRole('alert')).toHaveTextContent('Trained models could not be loaded.');
});

it('aborts an in-flight catalogue request when the custom seat is left', async () => {
  identity.admin = true;
  show({ models: [] });
  await openCustom();
  await waitFor(() => expect(modelsRequests()).toHaveLength(1));
  const signal = modelsRequests()[0][1]?.signal;
  expect(signal?.aborted).toBe(false);
  fireEvent.change(screen.getByLabelText('Attackers AI profile'), {
    target: { value: 'baseline' },
  });
  await waitFor(() => expect(signal?.aborted).toBe(true));
  // A late response cannot repopulate a catalogue the dialog no longer needs.
  modelRequests.shift()?.resolve(await json([catalogue[0]]));
  expect(screen.queryByLabelText('Attackers algorithm')).toBeNull();
});

it('filters models by the selected battlefield and flags an incompatible selection', async () => {
  identity.admin = true;
  show();
  fireEvent.click(screen.getByRole('radio', { name: /Brandubh/ }));
  await openCustom();
  await chooseNeural();
  expect(screen.getByLabelText('Attackers trained model')).toHaveValue(MUNIN);

  fireEvent.click(screen.getByRole('radio', { name: /Tablut/ }));
  expect(optionText('Attackers trained model')).toContain('Munin · incompatible battlefield');
  expect(
    screen.getByText('Choose a compatible trained model and its single matching battlefield.'),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: 'To the table' })).toBeDisabled();
});

it('exposes the alpha-beta optimizations for a neural opponent using the alpha-beta wrapper', async () => {
  identity.admin = true;
  show();
  await openCustom();
  expect(screen.queryByText('Alpha-beta optimizations')).toBeNull();
  await chooseNeural();
  await chooseWrapper('alpha_beta');
  const panel = screen.getByText('Alpha-beta optimizations').closest('fieldset')!;
  // A model-backed search cannot reuse symmetric positions.
  expect(within(panel).getByLabelText('Attackers symmetry move pruning')).toBeDisabled();
  expect(within(panel).getByLabelText('Attackers d4-canonical transpositions')).toBeDisabled();
  expect(
    within(within(panel).getByLabelText('Attackers search preset'))
      .getAllByRole('option')
      .map((option) => option.textContent),
  ).toEqual(['Legacy alpha-beta', 'Optimized default', 'Custom']);
  // The heuristic belongs to a model-free search only.
  expect(screen.queryByLabelText('Attackers heuristic')).toBeNull();
  expect(screen.getByLabelText('Attackers search depth')).toBeInTheDocument();
});

it('leaves the standalone alpha-beta agent unchanged', async () => {
  identity.admin = true;
  show();
  fireEvent.change(screen.getByLabelText('Attackers AI profile'), {
    target: { value: 'alpha_beta' },
  });
  fireEvent.change(screen.getByLabelText('Attackers AI profile'), { target: { value: 'custom' } });
  await screen.findByLabelText('Attackers algorithm');
  const panel = screen.getByText('Alpha-beta optimizations').closest('fieldset')!;
  expect(within(panel).getByLabelText('Attackers symmetry move pruning')).toBeEnabled();
  expect(
    within(within(panel).getByLabelText('Attackers search preset'))
      .getAllByRole('option')
      .map((option) => option.textContent),
  ).toContain('Full symmetry');
  expect(screen.getByLabelText('Attackers heuristic')).toHaveValue('shield_maiden');
  // A custom seat starts from the Jarl preset it replaced.
  expect(screen.getByLabelText('Attackers search depth')).toHaveValue(5);
});

it('serializes search presets and manual optimizations for a neural alpha-beta opponent', async () => {
  identity.admin = true;
  show();
  await openCustom();
  await chooseNeural();
  await chooseWrapper('alpha_beta');
  fireEvent.change(screen.getByLabelText('Attackers search preset'), {
    target: { value: 'custom' },
  });
  fireEvent.click(screen.getByLabelText('Attackers improved move ordering'));
  fireEvent.change(screen.getByLabelText('Attackers search depth'), { target: { value: '4' } });
  await submit(1);
  expect(creationBody().players.attackers.config).toEqual({
    algorithm: 'neural',
    model_id: HUGIN,
    search_wrapper: 'alpha_beta',
    time_limit: 1,
    depth: 4,
    search: { ...DEFAULT_SEARCH, move_ordering: false },
  });

  // The legacy preset collapses every optimized flag to its deterministic shape.
  fireEvent.change(screen.getByLabelText('Attackers search preset'), {
    target: { value: 'legacy' },
  });
  await submit(2);
  expect(creationBody().players.attackers.config.search).toEqual({
    ...DEFAULT_SEARCH,
    legacy: true,
  });
});

it('manages the nested search configuration when the wrapper changes', async () => {
  identity.admin = true;
  show();
  await openCustom();
  await chooseNeural();
  await chooseWrapper('alpha_beta');
  await submit(1);
  expect(creationBody().players.attackers.config.search).toEqual(DEFAULT_SEARCH);

  // Switching away from alpha-beta drops the nested settings it would reject.
  await chooseWrapper('mcts');
  expect(screen.queryByText('Alpha-beta optimizations')).toBeNull();
  await submit(2);
  expect(creationBody().players.attackers.config).not.toHaveProperty('search');
  expect(creationBody().players.attackers.config.search_wrapper).toBe('mcts');

  // Returning to alpha-beta starts from the tournament defaults again.
  await chooseWrapper('alpha_beta');
  await submit(3);
  expect(creationBody().players.attackers.config.search).toEqual(DEFAULT_SEARCH);
});
