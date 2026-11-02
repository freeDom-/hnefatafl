import { useCallback, useState } from 'react';
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
const SLEIPNIR = 'c'.repeat(24);
const hugin = (name = 'Hugin'): Model => ({ id: HUGIN, name, ruleset: 'tablut' });
const munin = (name = 'Munin'): Model => ({ id: MUNIN, name, ruleset: 'brandubh' });
const sleipnir: Model = { id: SLEIPNIR, name: 'Sleipnir', ruleset: 'tablut' };
const HUGIN_LABEL = 'Hugin · #aaaaaaaa';
const SLEIPNIR_LABEL = 'Sleipnir · #cccccccc';

const json = (value: unknown) =>
  Promise.resolve({ ok: true, status: 200, statusText: '', json: async () => value } as Response);
type CatalogueRequest = {
  resolve: (value: Model[]) => void;
  reject: (reason: unknown) => void;
  signal?: AbortSignal;
};
const requests: CatalogueRequest[] = [];
/** A catalogue request the test settles by hand; abort behaves like the real client. */
const deferredCatalogue = (signal?: AbortSignal) =>
  new Promise<Model[]>((resolve, reject) => {
    requests.push({ resolve, reject, signal });
    signal?.addEventListener('abort', () =>
      reject(new DOMException('Request cancelled', 'AbortError')),
    );
  });
const fetcher = vi.fn((path: string, _init?: RequestInit) =>
  path === '/api/models' ? json([]) : json({ id: 'created' }),
);
const settle = async (value: Model[]) => {
  await waitFor(() => expect(requests.length).toBeGreaterThan(0));
  requests.shift()?.resolve(value);
};
const fail = async (reason: unknown) => {
  await waitFor(() => expect(requests.length).toBeGreaterThan(0));
  requests.shift()?.reject(reason);
};
/** The dashboard owns the catalogue, so the harness mirrors App's wiring. */
function Harness({
  initial,
  load,
  onCreated,
  onClose,
}: {
  initial: Model[];
  load: (signal?: AbortSignal) => Promise<Model[]>;
  onCreated: () => void;
  onClose: () => void;
}) {
  const [models, setModels] = useState(initial);
  const refresh = useCallback(
    async (signal?: AbortSignal) => setModels(await load(signal)),
    [load],
  );
  return (
    <NotificationProvider>
      <NewGame
        rulesets={rulesets}
        profiles={profiles}
        models={models}
        onRefreshModels={refresh}
        onCreated={onCreated}
        onClose={onClose}
      />
    </NotificationProvider>
  );
}
const show = ({
  initial = [hugin(), munin()],
  load = deferredCatalogue,
  onCreated = vi.fn(),
  onClose = vi.fn(),
}: {
  initial?: Model[];
  load?: (signal?: AbortSignal) => Promise<Model[]>;
  onCreated?: () => void;
  onClose?: () => void;
} = {}) =>
  render(<Harness initial={initial} load={load} onCreated={onCreated} onClose={onClose} />);
const optionText = (label: string) =>
  within(screen.getByLabelText(label))
    .getAllByRole('option')
    .map((option) => option.textContent);
const openCustom = async () => {
  fireEvent.change(screen.getByLabelText('Attackers AI profile'), { target: { value: 'custom' } });
  await screen.findByLabelText('Attackers algorithm');
};
const chooseNeural = async () => {
  fireEvent.change(screen.getByLabelText('Attackers algorithm'), { target: { value: 'neural' } });
  await screen.findByLabelText('Attackers search wrapper');
};
const chooseHumanSide = (side: string) =>
  fireEvent.change(screen.getByLabelText('Your side'), { target: { value: side } });
const creationBody = () =>
  JSON.parse(
    String(fetcher.mock.calls.filter(([path]) => path === '/api/games').at(-1)?.[1]?.body),
  );

beforeEach(() => {
  identity.admin = false;
  clearNotifications();
  localStorage.clear();
  requests.length = 0;
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
  expect(requests).toHaveLength(0);
});

it('keeps sending a bare preset for the existing opponent options', async () => {
  identity.admin = true;
  show();
  fireEvent.change(screen.getByLabelText('Attackers AI profile'), { target: { value: 'mcts' } });
  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/games', expect.anything()));
  expect(creationBody().players).toEqual({ attackers: { profile_id: 'mcts' } });
  expect(requests).toHaveLength(0);
});

it('never persists a custom configuration as a browser preference', async () => {
  identity.admin = true;
  show();
  await openCustom();
  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/games', expect.anything()));
  const saved = JSON.parse(localStorage.getItem('hnefatafl-new-game-options') ?? '{}');
  expect(saved.custom).toBeUndefined();
  expect(saved.profiles.attackers).toBe('baseline');
});

it('shows the dashboard catalogue immediately and requests nothing', async () => {
  identity.admin = true;
  show();
  await openCustom();
  await chooseNeural();
  expect(optionText('Attackers trained model')).toContain(HUGIN_LABEL);
  expect(requests).toHaveLength(0);
  expect(screen.getByRole('button', { name: 'Refresh models' })).toBeEnabled();
});

it('loads the catalogue once when the dashboard has none', async () => {
  identity.admin = true;
  show({ initial: [] });
  expect(requests).toHaveLength(0);
  await openCustom();
  expect(requests).toHaveLength(1);
  await settle([hugin()]);
  await chooseNeural();
  await waitFor(() => expect(optionText('Attackers trained model')).toContain(HUGIN_LABEL));
});

it('makes a newly registered model available after refreshing', async () => {
  identity.admin = true;
  show();
  await openCustom();
  await chooseNeural();
  expect(optionText('Attackers trained model')).not.toContain(SLEIPNIR_LABEL);

  fireEvent.click(screen.getByRole('button', { name: 'Refresh models' }));
  expect(screen.getByRole('button', { name: 'Refreshing models…' })).toBeDisabled();
  // The previous catalogue stays selectable while the refresh runs.
  expect(optionText('Attackers trained model')).toContain(HUGIN_LABEL);
  await settle([hugin(), sleipnir]);
  await waitFor(() => expect(optionText('Attackers trained model')).toContain(SLEIPNIR_LABEL));
});

it('reflects renamed and deleted models after refreshing', async () => {
  identity.admin = true;
  show();
  fireEvent.click(screen.getByRole('radio', { name: /Brandubh/ }));
  await openCustom();
  await chooseNeural();
  expect(screen.getByLabelText('Attackers trained model')).toHaveValue(MUNIN);

  fireEvent.click(screen.getByRole('button', { name: 'Refresh models' }));
  await settle([hugin(), munin('Munin the Wise')]);
  await waitFor(() =>
    expect(optionText('Attackers trained model')).toContain('Munin the Wise · #bbbbbbbb'),
  );

  // A model removed from the registry is reported and blocks an invalid game.
  fireEvent.click(screen.getByRole('button', { name: 'Refresh models' }));
  await settle([hugin()]);
  await waitFor(() => expect(optionText('Attackers trained model')).toContain('Unavailable model'));
  expect(
    screen.getByText('Choose a compatible trained model and its single matching battlefield.'),
  ).toBeVisible();
  expect(screen.getByRole('button', { name: 'To the table' })).toBeDisabled();
});

it('keeps the previous catalogue selectable when a refresh fails and retries', async () => {
  identity.admin = true;
  show();
  await openCustom();
  await chooseNeural();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh models' }));
  await fail(new Error('Catalogue offline'));

  expect(await screen.findByRole('alert')).toHaveTextContent('Catalogue offline');
  expect(optionText('Attackers trained model')).toContain(HUGIN_LABEL);

  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await settle([hugin(), sleipnir]);
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  await waitFor(() => expect(optionText('Attackers trained model')).toContain(SLEIPNIR_LABEL));
});

it('treats an empty catalogue response as an empty registry, not a failure', async () => {
  identity.admin = true;
  show({ initial: [] });
  await openCustom();
  await chooseNeural();
  await settle([]);
  await waitFor(() =>
    expect(screen.getByText('Train or import a model in the AI workshop first.')).toBeVisible(),
  );
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.getByRole('button', { name: 'Refresh models' })).toBeEnabled();
});

it('hides a custom seat when the human takes that side and aborts its request', async () => {
  identity.admin = true;
  show({ initial: [] });
  await openCustom();
  expect(requests).toHaveLength(1);
  const signal = requests[0].signal;
  expect(signal?.aborted).toBe(false);

  chooseHumanSide('attackers');
  await waitFor(() => expect(signal?.aborted).toBe(true));
  expect(screen.queryByLabelText('Attackers algorithm')).toBeNull();
  expect(screen.getByLabelText('Defenders AI profile')).toHaveValue('baseline');
});

it('restores an edited custom configuration when the side is handed back', async () => {
  identity.admin = true;
  show();
  await openCustom();
  await chooseNeural();
  expect(screen.getByLabelText('Attackers trained model')).toHaveValue(HUGIN);

  chooseHumanSide('attackers');
  expect(screen.queryByLabelText('Attackers algorithm')).toBeNull();
  chooseHumanSide('defenders');
  await waitFor(() => expect(screen.getByLabelText('Attackers algorithm')).toHaveValue('neural'));
  expect(screen.getByLabelText('Attackers trained model')).toHaveValue(HUGIN);
  expect(requests).toHaveLength(0);
});

it('does not request the catalogue again when switching between a custom seat and a preset', async () => {
  identity.admin = true;
  show({ initial: [] });
  await openCustom();
  await settle([]);
  fireEvent.change(screen.getByLabelText('Attackers AI profile'), { target: { value: 'mcts' } });
  expect(screen.queryByLabelText('Attackers algorithm')).toBeNull();
  fireEvent.change(screen.getByLabelText('Attackers AI profile'), { target: { value: 'custom' } });
  await screen.findByLabelText('Attackers algorithm');
  expect(requests).toHaveLength(0);
});

it('ignores a response from a superseded request', async () => {
  identity.admin = true;
  // A caller that ignores the abort signal still cannot make a stale response
  // look like the current catalogue state.
  const stale: CatalogueRequest[] = [];
  const ignoreAbort = vi.fn(
    (signal?: AbortSignal) =>
      new Promise<Model[]>((resolve, reject) => {
        stale.push({ resolve, reject, signal });
      }),
  );
  show({ initial: [], load: ignoreAbort });
  await openCustom();
  await chooseNeural();
  await waitFor(() => expect(stale).toHaveLength(1));

  // Hiding the seat supersedes the first request without settling it.
  chooseHumanSide('attackers');
  chooseHumanSide('defenders');
  await waitFor(() => expect(screen.getByLabelText('Attackers algorithm')).toBeVisible());

  fireEvent.click(screen.getByRole('button', { name: 'Refresh models' }));
  await waitFor(() => expect(stale).toHaveLength(2));
  stale[1].resolve([hugin()]);
  await waitFor(() => expect(optionText('Attackers trained model')).toContain(HUGIN_LABEL));

  // The stale failure arrives last and must not surface an error.
  stale[0].reject(new Error('Catalogue offline'));
  await waitFor(() => expect(optionText('Attackers trained model')).toContain(HUGIN_LABEL));
  expect(screen.queryByRole('alert')).toBeNull();
});
