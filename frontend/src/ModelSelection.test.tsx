import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App, { NewGame } from './App';
import { selectStrongestModel } from './modelSelection';
import type { AIProfile, Game, Model, Ruleset } from './types';

const model = (id: string, changes: Partial<Model> = {}): Model => ({
  id,
  name: id,
  ruleset: 'tablut',
  created_at: '2026-01-01T00:00:00Z',
  ...changes,
});
const measured = (id: string, wins: number, losses: number, changes: Partial<Model> = {}) =>
  model(id, {
    evaluation: { opponent: 'baseline', wins, losses, draws: 0, truncated: 0 },
    ...changes,
  });
const rulesets: Ruleset[] = [
  { id: 'tablut', name: 'Tablut', size: 9, description: '', rules_markdown: '' },
  { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
];
const profiles: AIProfile[] = [
  {
    id: 'baseline',
    name: 'Shield bearer',
    algorithm: 'baseline',
    difficulty: 'Beginner',
    parameters: { time_limit: 1, seed: 42 },
    supported_rulesets: ['tablut', 'brandubh'],
  },
  {
    id: 'neural',
    name: 'Trained model',
    algorithm: 'neural',
    difficulty: 'Trained',
    parameters: { iterations: 2, time_limit: 0.1, seed: 43 },
    supported_rulesets: ['tablut', 'brandubh'],
  },
  {
    id: 'alpha_beta',
    name: 'Jarl',
    algorithm: 'alpha_beta',
    difficulty: 'Intermediate',
    parameters: { depth: 2, time_limit: 1 },
    supported_rulesets: ['tablut', 'brandubh'],
  },
];
const initial: Game = {
  id: 'g1',
  ruleset: 'tablut',
  seed: 93,
  size: 3,
  board: ['A', '.', '.', '.', 'K', '.', '.', '.', 'D'],
  turn: 'attackers',
  ply: 0,
  winner: null,
  reason: null,
  moves: [],
  cursor: 0,
  total_moves: 0,
  legal_moves: [{ source: 0, target: 1 }],
  mode: 'human-human',
  players: {
    attackers: { name: 'Astrid', kind: 'human', config: {} },
    defenders: { name: 'Erik', kind: 'human', config: {} },
  },
  version: 0,
  captured: { attackers: 0, defenders: 0 },
  created_at: '2026-10-21T08:00:00Z',
};
const json = (value: unknown) => Promise.resolve({ ok: true, json: async () => value } as Response);

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
  vi.unstubAllGlobals();
});

describe('model recommendations', () => {
  it('compares compatible baseline scores and counts unfinished games as zero', () => {
    const candidates = [
      measured('wrong-board', 10, 0, { ruleset: 'brandubh' }),
      measured('steady', 3, 1),
      model('unfinished', { evaluation: { opponent: 'baseline', wins: 4, truncated: 20 } }),
      model('maiden-benchmark', { evaluation: { opponent: 'shield_maiden', wins: 10, losses: 0 } }),
    ];
    expect(selectStrongestModel(candidates, 'tablut')).toMatchObject({
      model: { id: 'steady' },
      score: 0.75,
      games: 4,
      basis: 'evaluation',
    });
    expect(candidates.map((candidate) => candidate.id)).toEqual([
      'wrong-board',
      'steady',
      'unfinished',
      'maiden-benchmark',
    ]);
    expect(selectStrongestModel(candidates, 'copenhagen')).toBeNull();
  });

  it('uses half a point for draws and sample count before creation date for ties', () => {
    const selected = selectStrongestModel(
      [
        measured('new-small', 1, 1, { created_at: '2026-10-21T08:00:00Z' }),
        model('old-large', { evaluation: { wins: 2, losses: 2, draws: 4, score: 0.5 } }),
      ],
      'tablut',
    );
    expect(selected).toMatchObject({ model: { id: 'old-large' }, score: 0.5, games: 8 });
  });

  it('rejects incompatible opponents, score-only claims and malformed evaluation metadata', () => {
    const candidates = [
      measured('honest', 0, 2),
      model('previous-model', { evaluation: { opponent: 'previous_model', wins: 20, score: 1 } }),
      model('score-only', { evaluation: { score: 1 } }),
      model('nan', { evaluation: { wins: 2, score: NaN } }),
      model('out-of-range', { evaluation: { wins: 2, score: 2 } }),
      model('inconsistent', { evaluation: { wins: 2, losses: 2, score: 1 } }),
      model('negative-count', { evaluation: { wins: 2, losses: -1 } }),
      model('fresh-unevaluated', { created_at: '2026-10-21T08:00:00Z', metrics: { loss: 0.001 } }),
    ];
    expect(selectStrongestModel(candidates, 'tablut')).toMatchObject({
      model: { id: 'honest' },
      score: 0,
      basis: 'evaluation',
    });
  });

  it('falls back to latest compatible model with deterministic ID ties', () => {
    const candidates = [
      model('old'),
      model('new-b', { created_at: '2026-10-21T08:00:00Z' }),
      model('new-a', { created_at: '2026-10-21T08:00:00Z' }),
      model('invalid-date', { created_at: 'not-a-date' }),
    ];
    expect(selectStrongestModel(candidates, 'tablut')).toMatchObject({
      model: { id: 'new-a' },
      basis: 'latest',
      score: null,
    });
  });
});

const setupProps = {
  rulesets,
  profiles,
  models: [measured('weak', 1, 3), measured('strong', 3, 1)],
  players: [],
  onClose: vi.fn(),
  onCreated: vi.fn(),
};

describe('new game defaults', () => {
  it('configures all neural search wrappers without losing hidden search settings', async () => {
    const fetcher = vi.fn((_path: string, _options?: RequestInit) => json(initial));
    vi.stubGlobal('fetch', fetcher);
    render(<NewGame {...setupProps} />);
    fireEvent.click(screen.getByRole('button', { name: 'Human vs AI' }));
    const wrapper = screen.getByLabelText('Attackers search wrapper');
    expect(wrapper).toHaveValue('mcts');
    fireEvent.change(screen.getByLabelText('Search iterations'), { target: { value: '73' } });
    fireEvent.change(screen.getByLabelText('Temperature'), { target: { value: '0.7' } });
    fireEvent.change(wrapper, { target: { value: 'alpha_beta' } });
    expect(screen.queryByLabelText('Search iterations')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Exploration')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Temperature')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Time limit (seconds)')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Search depth'), { target: { value: '4' } });
    fireEvent.change(wrapper, { target: { value: 'none' } });
    expect(screen.queryByLabelText('Search depth')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Time limit (seconds)')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Temperature')).toHaveValue(0.7);
    fireEvent.click(screen.getByRole('button', { name: /To the table/ }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
    expect(
      JSON.parse(String(fetcher.mock.calls[0][1]?.body)).players.attackers.config,
    ).toMatchObject({
      algorithm: 'neural',
      model_id: 'strong',
      search_wrapper: 'none',
      temperature: 0.7,
    });
    fireEvent.change(wrapper, { target: { value: 'mcts' } });
    expect(screen.getByLabelText('Search iterations')).toHaveValue(73);
    fireEvent.change(wrapper, { target: { value: 'alpha_beta' } });
    expect(screen.getByLabelText('Search depth')).toHaveValue(4);
    await waitFor(() => expect(screen.getByRole('button', { name: /To the table/ })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: /To the table/ }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(
      JSON.parse(String(fetcher.mock.calls[1][1]?.body)).players.attackers.config,
    ).toMatchObject({
      algorithm: 'neural',
      search_wrapper: 'alpha_beta',
      depth: 4,
    });
    fireEvent.change(screen.getByLabelText('Attackers algorithm'), { target: { value: 'mcts' } });
    expect(screen.queryByLabelText('Attackers search wrapper')).not.toBeInTheDocument();
  });

  it('preselects the best compatible model and omits inherited seeds unless explicitly fixed', async () => {
    const fetcher = vi.fn((_path: string, _options?: RequestInit) => json(initial));
    vi.stubGlobal('fetch', fetcher);
    render(<NewGame {...setupProps} />);
    fireEvent.click(screen.getByRole('button', { name: 'Human vs AI' }));
    expect(screen.getByRole('combobox', { name: 'Attackers algorithm' })).toHaveValue('neural');
    expect(screen.getByRole('combobox', { name: 'Attackers trained model' })).toHaveValue('strong');
    expect(screen.getByText(/Best measured model selected: strong/)).toBeInTheDocument();
    expect(screen.queryByLabelText('Game seed')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /To the table/ }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
    const randomRequest = JSON.parse(String(fetcher.mock.calls[0][1]?.body));
    expect(randomRequest.seed).toBeUndefined();
    expect(randomRequest.players.attackers.config.seed).toBeUndefined();
    expect(randomRequest.players.attackers.config.model_id).toBe('strong');
    await waitFor(() => expect(screen.getByRole('button', { name: /To the table/ })).toBeEnabled());
    fireEvent.click(screen.getByRole('checkbox', { name: 'Use a fixed seed' }));
    fireEvent.change(screen.getByLabelText('Game seed'), { target: { value: '12345' } });
    fireEvent.click(screen.getByRole('button', { name: /To the table/ }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body)).seed).toBe(12345);
  });

  it('removes an automatic neural model when a traditional profile or algorithm is chosen', async () => {
    const fetcher = vi.fn((_path: string, _options?: RequestInit) => json(initial));
    vi.stubGlobal('fetch', fetcher);
    render(<NewGame {...setupProps} />);
    fireEvent.click(screen.getByRole('button', { name: 'Human vs AI' }));
    fireEvent.change(screen.getByRole('combobox', { name: 'Attackers AI profile' }), {
      target: { value: 'alpha_beta' },
    });
    fireEvent.click(screen.getByRole('button', { name: /To the table/ }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
    const profileRequest = JSON.parse(String(fetcher.mock.calls[0][1]?.body));
    expect(profileRequest.players.attackers.config).toEqual({
      algorithm: 'alpha_beta',
      depth: 5,
      time_limit: 2,
    });
    await waitFor(() => expect(screen.getByRole('button', { name: /To the table/ })).toBeEnabled());
    fireEvent.change(screen.getByRole('combobox', { name: 'Attackers AI profile' }), {
      target: { value: '' },
    });
    fireEvent.change(screen.getByRole('combobox', { name: 'Attackers algorithm' }), {
      target: { value: 'neural' },
    });
    expect(screen.getByRole('combobox', { name: 'Attackers trained model' })).toHaveValue('strong');
    fireEvent.change(screen.getByRole('combobox', { name: 'Attackers algorithm' }), {
      target: { value: 'mcts' },
    });
    fireEvent.click(screen.getByRole('button', { name: /To the table/ }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    const customRequest = JSON.parse(String(fetcher.mock.calls[1][1]?.body));
    expect(customRequest.players.attackers.config.algorithm).toBe('mcts');
    expect(customRequest.players.attackers.config.model_id).toBeUndefined();
  });

  it('updates automatic choices when models arrive but preserves a manual choice until battlefield changes', () => {
    const { rerender } = render(<NewGame {...setupProps} models={[]} />);
    fireEvent.click(screen.getByRole('button', { name: 'Human vs AI' }));
    expect(screen.getByRole('combobox', { name: 'Attackers AI profile' })).toHaveValue('baseline');
    expect(screen.queryByRole('combobox', { name: 'Attackers algorithm' })).not.toBeInTheDocument();
    rerender(<NewGame {...setupProps} />);
    expect(screen.getByRole('combobox', { name: 'Attackers trained model' })).toHaveValue('strong');
    fireEvent.change(screen.getByRole('combobox', { name: 'Attackers AI profile' }), {
      target: { value: 'baseline' },
    });
    const changedModels = [
      ...setupProps.models,
      measured('stronger', 10, 0),
      measured('brandubh-model', 1, 1, { ruleset: 'brandubh' }),
    ];
    rerender(<NewGame {...setupProps} models={changedModels} />);
    expect(screen.getByRole('combobox', { name: 'Attackers AI profile' })).toHaveValue('baseline');
    fireEvent.click(screen.getByRole('radio', { name: /Brandubh/ }));
    expect(screen.getByRole('combobox', { name: 'Attackers trained model' })).toHaveValue(
      'brandubh-model',
    );
    fireEvent.change(screen.getByRole('combobox', { name: 'Your side' }), {
      target: { value: 'attackers' },
    });
    expect(screen.getByRole('combobox', { name: 'Defenders trained model' })).toHaveValue(
      'brandubh-model',
    );
  });
});

it('clears the current game when deleted and ignores its late in-flight move response', async () => {
  localStorage.setItem('hnefatafl-game', initial.id);
  let finish!: (response: Response) => void;
  let deleted = false;
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, options?: RequestInit) => {
      if (path === '/api/rulesets') return json(rulesets);
      if (path === '/api/ai/profiles') return json(profiles);
      if (path === '/api/players' || path === '/api/models') return json([]);
      if (options?.method === 'DELETE') {
        deleted = true;
        return json({ deleted: true, id: initial.id });
      }
      if (path.startsWith('/api/games?')) return json(deleted ? [] : [initial]);
      if (path.startsWith('/api/stats?')) return json({ total_games: deleted ? 0 : 1 });
      if (path === '/api/games/g1/moves')
        return new Promise<Response>((resolve) => {
          finish = resolve;
        });
      if (path === '/api/games/g1') return json(initial);
      throw Error(`Unexpected request ${path}`);
    }),
  );
  render(<App />);
  fireEvent.click(await screen.findByRole('gridcell', { name: 'a3, attacker' }));
  fireEvent.click(screen.getByRole('gridcell', { name: 'b3, empty, legal move' }));
  fireEvent.click(screen.getByRole('button', { name: 'Hall of records' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Delete game g1' }));
  fireEvent.click(
    within(screen.getByRole('dialog', { name: 'Delete recorded game?' })).getByRole('button', {
      name: 'Delete',
    }),
  );
  await waitFor(() => expect(localStorage.getItem('hnefatafl-game')).toBeNull());
  await act(async () => finish(await json({ ...initial, version: 1 })));
  fireEvent.click(screen.getByRole('button', { name: 'Play' }));
  expect(screen.getByRole('heading', { name: 'The king awaits.' })).toBeInTheDocument();
  expect(screen.queryByRole('grid')).not.toBeInTheDocument();
});
