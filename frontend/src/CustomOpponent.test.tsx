import { useEffect } from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import NewGame from './NewGame';
import { setLanguage } from './i18n';
import { NotificationProvider, clearNotifications } from './notifications';
import { useModelCatalogue } from './useModelCatalogue';
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
const CATALOGUE = [hugin(), munin()];

const json = (value: unknown) =>
  Promise.resolve({ ok: true, status: 200, statusText: '', json: async () => value } as Response);
type CatalogueRequest = { settle: (value: unknown) => void; fail: (reason: unknown) => void };
const requests: CatalogueRequest[] = [];
const catalogueResponse = (signal: AbortSignal | null | undefined) =>
  new Promise<Response>((resolve, reject) => {
    requests.push({
      settle: (value) =>
        resolve({ ok: true, status: 200, statusText: '', json: async () => value } as Response),
      fail: reject,
    });
    // A transport that observes cancellation, like the real fetch.
    signal?.addEventListener('abort', () =>
      reject(new DOMException('Request cancelled', 'AbortError')),
    );
  });
/** A transport that cannot be cancelled: the catalogue owner must still order responses. */
const uncancellableResponse = () =>
  new Promise<Response>((resolve) => {
    requests.push({
      settle: (value) =>
        resolve({ ok: true, status: 200, statusText: '', json: async () => value } as Response),
      fail: () => undefined,
    });
  });
let cancellable = true;
const fetcher = vi.fn((path: string, init?: RequestInit) => {
  if (path !== '/api/models') return json({ id: 'created' });
  return (cancellable ? catalogueResponse : uncancellableResponse)(init?.signal);
});
const modelRequests = () => fetcher.mock.calls.filter(([path]) => path === '/api/models').length;
const settleModels = async (value: unknown) => {
  await waitFor(() => expect(requests.length).toBeGreaterThan(0));
  const request = requests.shift()!;
  await act(async () => {
    request.settle(value);
  });
};
const failModels = async (reason: unknown) => {
  await waitFor(() => expect(requests.length).toBeGreaterThan(0));
  const request = requests.shift()!;
  await act(async () => {
    request.fail(reason);
  });
};
/**
 * The dashboard owns the catalogue: this harness wires the real hook exactly as
 * `App.tsx` does, and optionally loads it before the dialog opens, the way
 * entering a model-aware page would.
 */
function Harness({
  seed = false,
  onCreated,
  onClose,
}: {
  seed?: boolean;
  onCreated: () => void;
  onClose: () => void;
}) {
  const { models, refresh } = useModelCatalogue();
  useEffect(() => {
    if (!seed) return;
    void refresh().catch(() => undefined);
  }, [seed, refresh]);
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
const show = ({ seed = false, onCreated = vi.fn(), onClose = vi.fn() } = {}) =>
  render(<Harness seed={seed} onCreated={onCreated} onClose={onClose} />);
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
  cancellable = true;
  setLanguage('en', false);
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
  setLanguage('en', false);
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
  expect(modelRequests()).toBe(0);
});

it('keeps sending a bare preset for the existing opponent options', async () => {
  identity.admin = true;
  show();
  fireEvent.change(screen.getByLabelText('Attackers AI profile'), { target: { value: 'mcts' } });
  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith('/api/games', expect.anything()));
  expect(creationBody().players).toEqual({ attackers: { profile_id: 'mcts' } });
  expect(modelRequests()).toBe(0);
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

it('shows a catalogue the dashboard already loaded and requests nothing more', async () => {
  identity.admin = true;
  show({ seed: true });
  await settleModels(CATALOGUE);
  await openCustom();
  await chooseNeural();
  expect(optionText('Attackers trained model')).toContain(HUGIN_LABEL);
  expect(modelRequests()).toBe(1);
  expect(screen.getByRole('button', { name: 'Refresh models' })).toBeEnabled();
});

it('loads the catalogue once when the dashboard has none', async () => {
  identity.admin = true;
  show();
  expect(modelRequests()).toBe(0);
  await openCustom();
  expect(modelRequests()).toBe(1);
  await settleModels([hugin()]);
  await chooseNeural();
  await waitFor(() => expect(optionText('Attackers trained model')).toContain(HUGIN_LABEL));
});

it('makes a newly registered model available after refreshing', async () => {
  identity.admin = true;
  show();
  await openCustom();
  await settleModels([hugin()]);
  await chooseNeural();
  expect(optionText('Attackers trained model')).not.toContain(SLEIPNIR_LABEL);

  fireEvent.click(screen.getByRole('button', { name: 'Refresh models' }));
  expect(screen.getByRole('button', { name: 'Refreshing models…' })).toBeDisabled();
  // The previous catalogue stays selectable while the refresh runs.
  expect(optionText('Attackers trained model')).toContain(HUGIN_LABEL);
  await settleModels([hugin(), sleipnir]);
  await waitFor(() => expect(optionText('Attackers trained model')).toContain(SLEIPNIR_LABEL));
});

it('reflects renamed and deleted models after refreshing', async () => {
  identity.admin = true;
  show();
  fireEvent.click(screen.getByRole('radio', { name: /Brandubh/ }));
  await openCustom();
  await settleModels(CATALOGUE);
  await chooseNeural();
  expect(screen.getByLabelText('Attackers trained model')).toHaveValue(MUNIN);

  fireEvent.click(screen.getByRole('button', { name: 'Refresh models' }));
  await settleModels([hugin(), munin('Munin the Wise')]);
  await waitFor(() =>
    expect(optionText('Attackers trained model')).toContain('Munin the Wise · #bbbbbbbb'),
  );

  // A model removed from the registry is reported and blocks an invalid game.
  fireEvent.click(screen.getByRole('button', { name: 'Refresh models' }));
  await settleModels([hugin()]);
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
  await settleModels(CATALOGUE);
  await chooseNeural();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh models' }));
  await failModels(new Error('Catalogue offline'));

  expect(await screen.findByRole('alert')).toHaveTextContent('Catalogue offline');
  expect(optionText('Attackers trained model')).toContain(HUGIN_LABEL);

  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await settleModels([hugin(), sleipnir]);
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  await waitFor(() => expect(optionText('Attackers trained model')).toContain(SLEIPNIR_LABEL));
});

it('reports a malformed catalogue without discarding the models already loaded', async () => {
  identity.admin = true;
  show();
  await openCustom();
  await settleModels(CATALOGUE);
  await chooseNeural();

  fireEvent.click(screen.getByRole('button', { name: 'Refresh models' }));
  await settleModels({ models: [] });
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('The server returned an unreadable model catalogue.');
  expect(optionText('Attackers trained model')).toContain(HUGIN_LABEL);

  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await settleModels([hugin(), sleipnir]);
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  await waitFor(() => expect(optionText('Attackers trained model')).toContain(SLEIPNIR_LABEL));
});

it('translates a malformed catalogue error while keeping the loaded models and retry', async () => {
  setLanguage('de', false);
  identity.admin = true;
  show();
  fireEvent.change(screen.getByLabelText('KI-Profil der Angreifer'), {
    target: { value: 'custom' },
  });
  await screen.findByLabelText('Angreifer algorithmus');
  await settleModels(CATALOGUE);
  fireEvent.change(screen.getByLabelText('Angreifer algorithmus'), {
    target: { value: 'neural' },
  });
  await screen.findByLabelText('Angreifer suchverfahren');

  fireEvent.click(screen.getByRole('button', { name: 'Modelle aktualisieren' }));
  await settleModels({ models: [] });
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('Der Server hat einen unlesbaren Modellkatalog zurückgegeben.');
  expect(alert).not.toHaveTextContent('unreadable model catalogue');
  expect(optionText('Angreifer trainiertes Modell')).toContain(HUGIN_LABEL);

  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  await settleModels([hugin(), sleipnir]);
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  await waitFor(() => expect(optionText('Angreifer trainiertes Modell')).toContain(SLEIPNIR_LABEL));
});

it('treats an empty catalogue response as an empty registry, not a failure', async () => {
  identity.admin = true;
  show();
  await openCustom();
  await settleModels([]);
  await chooseNeural();
  await waitFor(() =>
    expect(screen.getByText('Train or import a model in the AI workshop first.')).toBeVisible(),
  );
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.getByRole('button', { name: 'Refresh models' })).toBeEnabled();
});

it('hides a custom seat when the human takes that side and aborts its request', async () => {
  identity.admin = true;
  show();
  await openCustom();
  expect(requests).toHaveLength(1);
  const signal = fetcher.mock.calls.find(([path]) => path === '/api/models')?.[1]?.signal;
  expect(signal?.aborted).toBe(false);

  chooseHumanSide('attackers');
  await waitFor(() => expect(signal?.aborted).toBe(true));
  expect(screen.queryByLabelText('Attackers algorithm')).toBeNull();
  expect(screen.getByLabelText('Defenders AI profile')).toHaveValue('baseline');
});

it('restores an edited custom configuration when the side is handed back', async () => {
  identity.admin = true;
  show({ seed: true });
  await settleModels(CATALOGUE);
  await openCustom();
  await chooseNeural();
  expect(screen.getByLabelText('Attackers trained model')).toHaveValue(HUGIN);

  chooseHumanSide('attackers');
  expect(screen.queryByLabelText('Attackers algorithm')).toBeNull();
  chooseHumanSide('defenders');
  await waitFor(() => expect(screen.getByLabelText('Attackers algorithm')).toHaveValue('neural'));
  expect(screen.getByLabelText('Attackers trained model')).toHaveValue(HUGIN);
  expect(modelRequests()).toBe(1);
});

it('does not request the catalogue again when switching between a custom seat and a preset', async () => {
  identity.admin = true;
  show();
  await openCustom();
  await settleModels([]);
  fireEvent.change(screen.getByLabelText('Attackers AI profile'), { target: { value: 'mcts' } });
  expect(screen.queryByLabelText('Attackers algorithm')).toBeNull();
  fireEvent.change(screen.getByLabelText('Attackers AI profile'), { target: { value: 'custom' } });
  await screen.findByLabelText('Attackers algorithm');
  expect(modelRequests()).toBe(1);
});

it('never lets a superseded response replace the newer catalogue', async () => {
  identity.admin = true;
  cancellable = false;
  show();
  await openCustom();
  await chooseNeural();
  await waitFor(() => expect(requests).toHaveLength(1));

  // Hiding the seat supersedes the first request; this transport cannot be cancelled.
  chooseHumanSide('attackers');
  chooseHumanSide('defenders');
  await waitFor(() => expect(screen.getByLabelText('Attackers algorithm')).toBeVisible());

  fireEvent.click(screen.getByRole('button', { name: 'Refresh models' }));
  await waitFor(() => expect(requests).toHaveLength(2));
  await act(async () => {
    requests[1].settle([hugin()]);
  });
  await waitFor(() => expect(optionText('Attackers trained model')).toContain(HUGIN_LABEL));

  // The older response arrives last with different data and must be dropped.
  await act(async () => {
    requests[0].settle([munin()]);
  });
  await waitFor(() => expect(optionText('Attackers trained model')).toContain(HUGIN_LABEL));
  expect(optionText('Attackers trained model').join(' ')).not.toContain('Munin');
  expect(screen.queryByRole('alert')).toBeNull();
});
