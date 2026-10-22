import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { NewGame } from './App';
import { GAME_SETUP_KEY, restoreGameSetup } from './gameSetupPreference';
import type { AIProfile, Model, Ruleset } from './types';

const rulesets: Ruleset[] = [
  { id: 'tablut', name: 'Tablut', size: 9, description: '', rules_markdown: '' },
  { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
];
const profiles: AIProfile[] = [
  {
    id: 'baseline',
    name: 'Shield bearer',
    algorithm: 'baseline',
    difficulty: '',
    parameters: { time_limit: 1 },
    supported_rulesets: ['tablut', 'brandubh'],
  },
  {
    id: 'neural',
    name: 'Trained model',
    algorithm: 'neural',
    difficulty: '',
    parameters: { iterations: 2, time_limit: 0.1 },
    supported_rulesets: ['tablut', 'brandubh'],
  },
  {
    id: 'alpha_beta',
    name: 'Jarl',
    algorithm: 'alpha_beta',
    difficulty: '',
    parameters: { depth: 2, time_limit: 1 },
    supported_rulesets: ['tablut', 'brandubh'],
  },
  {
    id: 'mcts',
    name: 'Seer',
    algorithm: 'mcts',
    difficulty: '',
    parameters: { iterations: 80, time_limit: 1 },
    supported_rulesets: ['tablut', 'brandubh'],
  },
];
const models: Model[] = [
  {
    id: 'first',
    name: 'My first strategist',
    ruleset: 'tablut',
    evaluation: { wins: 9, losses: 1 },
  },
  { id: 'ulf', name: 'Ulf', ruleset: 'tablut', evaluation: { wins: 1, losses: 1 } },
  { id: 'freydis', name: 'Freydis', ruleset: 'tablut', evaluation: { wins: 1, losses: 1 } },
];
const players = [{ id: 'astrid', name: 'Astrid' }];
const options = { rulesets, profiles, models, players };
const json = (value: unknown, ok = true) =>
  Promise.resolve({ ok, json: async () => value } as Response);
beforeEach(() => {
  localStorage.clear();
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
const change = (element: HTMLElement, value: string) =>
  fireEvent.change(element, { target: { value } });

it('starts Ulf against Freydis with the selected IDs/names and restores the entire successful setup', async () => {
  const fetcher = vi.fn((_path: string, init?: RequestInit) =>
    json({ id: 'created', ...JSON.parse(String(init?.body)) }),
  );
  vi.stubGlobal('fetch', fetcher);
  const onCreated = vi.fn();
  const first = render(<NewGame {...options} onClose={vi.fn()} onCreated={onCreated} />);
  change(screen.getByLabelText('Attackers human player'), 'astrid');
  change(screen.getByLabelText('Defenders player name'), 'Liv');
  fireEvent.click(screen.getByRole('button', { name: 'Human vs AI' }));
  change(screen.getByLabelText('Your side'), 'attackers');
  fireEvent.click(screen.getByRole('button', { name: 'AI vs AI' }));
  change(screen.getByLabelText('Attackers trained model'), 'ulf');
  change(screen.getByLabelText('Defenders trained model'), 'freydis');
  const configurations = document.querySelectorAll('.player-config');
  change(within(configurations[0] as HTMLElement).getByLabelText('Search iterations'), '123');
  change(within(configurations[1] as HTMLElement).getByLabelText('Search iterations'), '87');
  change(within(configurations[0] as HTMLElement).getByLabelText('Time limit (seconds)'), '.5');
  change(within(configurations[1] as HTMLElement).getByLabelText('Time limit (seconds)'), '.25');
  expect(screen.queryByLabelText('Agent for move suggestions')).not.toBeInTheDocument();
  fireEvent.click(screen.getByLabelText('Use a fixed seed'));
  change(screen.getByLabelText('Game seed'), '213');
  fireEvent.click(screen.getByRole('button', { name: /To the table/ }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
  const submitted = JSON.parse(String(fetcher.mock.calls[0][1]?.body));
  expect(submitted.players.attackers).toMatchObject({
    name: 'Ulf',
    config: { model_id: 'ulf', iterations: 123, time_limit: 0.5 },
  });
  expect(submitted.players.defenders).toMatchObject({
    name: 'Freydis',
    config: { model_id: 'freydis', iterations: 87, time_limit: 0.25 },
  });
  expect(submitted.seed).toBe(213);
  expect(submitted.advisor).toBeUndefined();
  first.unmount();

  const changedModels = [...models].reverse().concat({
    id: 'new-best',
    name: 'Another strategist',
    ruleset: 'tablut',
    evaluation: { wins: 20, losses: 0 },
  });
  const second = render(
    <NewGame
      {...options}
      models={changedModels}
      initialRuleset="brandubh"
      onClose={vi.fn()}
      onCreated={vi.fn()}
    />,
  );
  expect(screen.getByRole('radio', { name: /Tablut/ })).toBeChecked();
  expect(screen.getByRole('button', { name: 'AI vs AI' })).toHaveClass('active');
  expect(screen.getByLabelText('Attackers trained model')).toHaveValue('ulf');
  expect(screen.getByLabelText('Defenders trained model')).toHaveValue('freydis');
  expect(
    screen.getAllByLabelText('Search iterations').map((input) => (input as HTMLInputElement).value),
  ).toEqual(['123', '87']);
  expect(
    screen
      .getAllByLabelText('Time limit (seconds)')
      .map((input) => (input as HTMLInputElement).value),
  ).toEqual(['0.5', '0.25']);
  expect(screen.queryByLabelText('Agent for move suggestions')).not.toBeInTheDocument();
  expect(screen.getByLabelText('Use a fixed seed')).toBeChecked();
  expect(screen.getByLabelText('Game seed')).toHaveValue(213);
  fireEvent.click(screen.getByRole('button', { name: 'Human vs AI' }));
  expect(screen.getByLabelText('Your side')).toHaveValue('attackers');
  expect(screen.getByLabelText('Attackers human player')).toHaveValue('astrid');
  const successful = localStorage.getItem(GAME_SETUP_KEY);
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(localStorage.getItem(GAME_SETUP_KEY)).toBe(successful);
  second.unmount();
});

it('sanitizes missing identities, corrupted budgets and obsolete hidden model references', () => {
  localStorage.setItem(
    GAME_SETUP_KEY,
    JSON.stringify({
      version: 1,
      ruleset: 'missing-board',
      mode: 'ai-ai',
      fixedSeed: true,
      seed: -1,
      advisorId: 'model:deleted',
      people: { attackers: { player_id: 'deleted-human', name: 'Old name' } },
      computers: {
        attackers: {
          kind: 'ai',
          name: 'Old model',
          config: { algorithm: 'neural', model_id: 'deleted' },
        },
        defenders: {
          kind: 'ai',
          config: {
            algorithm: 'mcts',
            iterations: 100,
            model_id: 'deleted',
            model_path: '/private/file',
            seed: 99,
          },
        },
      },
    }),
  );
  const restored = restoreGameSetup({ ...options, initialRuleset: 'tablut' });
  expect(restored.ruleset).toBe('tablut');
  expect(restored.computers.attackers).toMatchObject({
    name: 'My first strategist',
    config: { model_id: 'first' },
  });
  expect(restored.computers.defenders.config).toEqual({ algorithm: 'mcts', iterations: 100 });
  expect(restored.advisorId).toBe('');
  expect(restored.people.attackers).toEqual({ name: '', kind: 'human', config: {} });
  expect(restored.fixedSeed).toBe(false);
  localStorage.setItem(
    GAME_SETUP_KEY,
    JSON.stringify({
      version: 1,
      computers: { attackers: { kind: 'ai', config: { algorithm: 'mcts', iterations: '100' } } },
    }),
  );
  expect(restoreGameSetup(options).computers.attackers.config.model_id).toBe('first');
});

it('keeps the complete previous setup when another creation fails', async () => {
  const saved = {
    version: 1,
    ruleset: 'tablut',
    mode: 'human-human',
    fixedSeed: true,
    seed: 78,
    advisorId: 'profile:baseline',
  };
  localStorage.setItem(GAME_SETUP_KEY, JSON.stringify(saved));
  const previous = localStorage.getItem(GAME_SETUP_KEY);
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json({ detail: 'Creation failed.' }, false)),
  );
  render(<NewGame {...options} onClose={vi.fn()} onCreated={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'AI vs AI' }));
  fireEvent.click(screen.getByLabelText('Use a fixed seed'));
  fireEvent.click(screen.getByRole('button', { name: /To the table/ }));
  await screen.findByText('Creation failed.');
  expect(localStorage.getItem(GAME_SETUP_KEY)).toBe(previous);
});

it('keeps Shield bearer at one second and exposes search time only for Custom opponent', async () => {
  const fetcher = vi.fn((_path: string, init?: RequestInit) =>
    json({ id: 'created', ...JSON.parse(String(init?.body)) }),
  );
  vi.stubGlobal('fetch', fetcher);
  render(<NewGame {...options} onClose={vi.fn()} onCreated={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Human vs AI' }));
  change(screen.getByLabelText('Attackers AI profile'), 'baseline');
  expect(screen.queryByLabelText('Time limit (seconds)')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Attackers algorithm')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /To the table/ }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body)).players.attackers).toMatchObject({
    profile_id: 'baseline',
    config: { algorithm: 'baseline', time_limit: 1 },
  });
  await waitFor(() => expect(screen.getByRole('button', { name: /To the table/ })).toBeEnabled());
  change(screen.getByLabelText('Attackers AI profile'), '');
  expect(screen.getByLabelText('Attackers algorithm')).toHaveValue('baseline');
  change(screen.getByLabelText('Time limit (seconds)'), '0.25');
  fireEvent.click(screen.getByRole('button', { name: /To the table/ }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  const custom = JSON.parse(String(fetcher.mock.calls[1][1]?.body)).players.attackers;
  expect(custom.profile_id).toBeUndefined();
  expect(custom.config).toMatchObject({ algorithm: 'baseline', time_limit: 0.25 });
});
