import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { StrictMode } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import NewGameForm, { GAME_SETUP_KEY } from './NewGameForm';
import { NotificationProvider, clearNotifications, getNotifications } from './notifications';
import type { AIProfile, Ruleset } from './types';
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
  [
    'mcts',
    'Seer',
    { time_limit: 2, iterations: 5000, exploration: 1.4, rollout_depth: 8, temperature: 0 },
  ],
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
const json = (value: unknown, ok = true) =>
  Promise.resolve({ ok, json: async () => value } as Response);
const fetcher = vi.fn((_path: string, _init?: RequestInit) => json({ id: 'created' }));
const show = (onCreated = vi.fn(), onClose = vi.fn(), isCurrentOperation?: () => boolean) =>
  render(
    <NotificationProvider>
      <NewGameForm
        rulesets={rulesets}
        profiles={profiles}
        isCurrentOperation={isCurrentOperation}
        onCreated={onCreated}
        onClose={onClose}
      />
    </NotificationProvider>,
  );
const showStrict = (onCreated = vi.fn(), onClose = vi.fn()) =>
  render(
    <StrictMode>
      <NotificationProvider>
        <NewGameForm
          rulesets={rulesets}
          profiles={profiles}
          onCreated={onCreated}
          onClose={onClose}
        />
      </NotificationProvider>
    </StrictMode>,
  );
beforeEach(() => {
  identity.admin = false;
  clearNotifications();
  localStorage.clear();
  fetcher.mockReset().mockImplementation(() => json({ id: 'created' }));
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
it('binds normal play to the account, hides mode/identity/config controls and sends only a preset', async () => {
  show();
  // Account identity stays server-enforced; the redundant card is gone.
  expect(screen.queryByText('Odin')).toBeNull();
  expect(screen.queryByText('Playing as your account')).toBeNull();
  expect(screen.queryByLabelText('Game mode')).toBeNull();
  expect(screen.queryByRole('button', { name: 'Pass & play' })).toBeNull();
  expect(screen.queryByRole('button', { name: 'AI vs AI' })).toBeNull();
  expect(screen.queryByRole('textbox')).toBeNull();
  expect(screen.queryByRole('spinbutton')).toBeNull();
  const opponent = screen.getByLabelText('Attackers AI profile');
  expect(
    within(opponent)
      .getAllByRole('option')
      .map((o) => o.textContent),
  ).toEqual(['Shield bearer', 'Shield maiden', 'Jarl', 'Seer']);
  fireEvent.change(opponent, { target: { value: 'alpha_beta' } });
  expect(screen.getByText('Time limit (seconds): 2 · Search depth: 5')).toBeVisible();
  fireEvent.change(opponent, { target: { value: 'mcts' } });
  expect(screen.getByText('Time limit (seconds): 2 · Search iterations: 5000')).toBeVisible();
  expect(
    screen.queryByText(/Exploration|Rollout|Temperature|heuristic_version|Best measured/),
  ).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({
    ruleset: 'tablut',
    mode: 'human-ai',
    human_side: 'defenders',
    players: { attackers: { profile_id: 'mcts' } },
  });
});
it('allows administrators to select two preset opponents without human or custom fields', async () => {
  identity.admin = true;
  show();
  fireEvent.click(screen.getByRole('button', { name: 'AI vs AI' }));
  expect(screen.queryByText('Playing as your account')).toBeNull();
  fireEvent.change(screen.getByLabelText('Attackers AI profile'), {
    target: { value: 'shield_maiden' },
  });
  fireEvent.change(screen.getByLabelText('Defenders AI profile'), {
    target: { value: 'alpha_beta' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body)).players).toEqual({
    attackers: { profile_id: 'shield_maiden' },
    defenders: { profile_id: 'alpha_beta' },
  });
});
it('remembers only a successful permitted setup and does not save cancellation or a failed creation', async () => {
  identity.admin = true;
  const created = vi.fn();
  const first = show(created);
  fireEvent.click(screen.getByRole('button', { name: 'AI vs AI' }));
  fireEvent.change(screen.getByLabelText('Attackers AI profile'), { target: { value: 'mcts' } });
  expect(localStorage.getItem(GAME_SETUP_KEY)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  await waitFor(() => expect(created).toHaveBeenCalledOnce());
  const saved = localStorage.getItem(GAME_SETUP_KEY);
  first.unmount();
  const close = vi.fn();
  const second = show(vi.fn(), close);
  expect(screen.getByRole('button', { name: 'AI vs AI' })).toHaveClass('active');
  expect(screen.getByLabelText('Attackers AI profile')).toHaveValue('mcts');
  fireEvent.click(screen.getByRole('button', { name: 'Human vs AI' }));
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(close).toHaveBeenCalledOnce();
  expect(localStorage.getItem(GAME_SETUP_KEY)).toBe(saved);
  second.unmount();
  fetcher.mockImplementation(() => json({ detail: 'Creation unavailable' }, false));
  const retried = vi.fn();
  show(retried);
  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Creation unavailable');
  expect(screen.getAllByRole('alert')).toHaveLength(1);
  expect(retried).not.toHaveBeenCalled();
  expect(localStorage.getItem(GAME_SETUP_KEY)).toBe(saved);
  // The failed attempt keeps the selections, so an explicit retry repeats them.
  const failedBody = fetcher.mock.calls[0][1]?.body;
  fetcher.mockImplementation(() => json({ id: 'created' }));
  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  await waitFor(() => expect(retried).toHaveBeenCalledOnce());
  expect(fetcher.mock.calls[1][1]?.body).toBe(failedBody);
});
it.each([
  null,
  42,
  [],
  {
    mode: 'human-human',
    people: { attackers: { name: 'Forged' } },
    computers: { attackers: { profile_id: 'neural', config: { model_id: 'x' } } },
  },
  { mode: 'ai-ai', profiles: { attackers: 'missing', defenders: 'mcts' } },
  { profiles: 42, computers: null },
])('sanitizes old or malformed preferences %j without restoring privilege or identity', (value) => {
  localStorage.setItem(GAME_SETUP_KEY, JSON.stringify(value));
  show();
  expect(screen.queryByRole('button', { name: 'AI vs AI' })).toBeNull();
  expect(screen.getByLabelText('Attackers AI profile')).toHaveValue('baseline');
  expect(screen.queryByText('Forged')).toBeNull();
});
it('starts a game even when preference storage is unavailable', async () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw Error('blocked');
  });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw Error('blocked');
  });
  const created = vi.fn();
  show(created);
  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  await waitFor(() => expect(created).toHaveBeenCalledOnce());
});

it('does not apply a creation completion after the dialog is dismissed', async () => {
  let finish!: (response: Response) => void;
  fetcher.mockImplementation(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  const created = vi.fn();
  const view = show(created);
  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  // The server still commits the game, but the dismissed dialog must not
  // navigate, reopen or persist preferences from the obsolete completion.
  view.unmount();
  await act(async () => {
    finish(await json({ id: 'created' }));
  });
  expect(created).not.toHaveBeenCalled();
  expect(localStorage.getItem(GAME_SETUP_KEY)).toBeNull();
  expect(getNotifications()).toEqual([]);
});

it('does not surface a stale creation error after dismissal', async () => {
  let fail!: (reason: unknown) => void;
  fetcher.mockImplementation(
    () =>
      new Promise<Response>((_, reject) => {
        fail = reject;
      }),
  );
  const created = vi.fn();
  const view = show(created);
  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  view.unmount();
  await act(async () => {
    fail(new Error('offline'));
  });
  expect(created).not.toHaveBeenCalled();
  expect(getNotifications()).toEqual([]);
});

it('does not persist preferences or complete when the operation is no longer current', async () => {
  let finish!: (response: Response) => void;
  fetcher.mockImplementation(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  const created = vi.fn();
  show(created, vi.fn(), () => false);
  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  await act(async () => {
    finish(await json({ id: 'created' }));
  });
  expect(created).not.toHaveBeenCalled();
  expect(localStorage.getItem(GAME_SETUP_KEY)).toBeNull();
  expect(getNotifications()).toEqual([]);
});

it('does not surface a stale creation error when the operation is no longer current', async () => {
  fetcher.mockImplementation(() => json({ detail: 'Creation unavailable' }, false));
  show(vi.fn(), vi.fn(), () => false);
  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  await waitFor(() => expect(getNotifications()).toEqual([]));
});

it('still applies creation when StrictMode re-runs the mount effect', async () => {
  const created = vi.fn();
  showStrict(created);
  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  await waitFor(() => expect(created).toHaveBeenCalledOnce());
});

it('still reports a failed creation when StrictMode re-runs the mount effect', async () => {
  fetcher.mockImplementation(() => json({ detail: 'The table is full.' }, false));
  showStrict();
  fireEvent.click(screen.getByRole('button', { name: 'To the table' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('The table is full.');
});
