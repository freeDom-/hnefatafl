import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { selectStrongestModel } from './modelSelection';
import type { AIProfile, Game, Model, Ruleset } from './types';

// Exercise App's retained training-cleanup callback; Hall is now read-only.
vi.mock('./Training', () => ({
  default: ({ onGamesDeleted }: { onGamesDeleted: (ids: string[]) => void }) => (
    <button onClick={() => onGamesDeleted(['g1'])}>Discard fixture model games</button>
  ),
}));

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

it('training cleanup clears the current game and ignores its late in-flight move response', async () => {
  history.replaceState(null, '', '/games/g1');
  localStorage.setItem('hnefatafl-game', initial.id);
  let finish!: (response: Response) => void;
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) => {
      if (path === '/api/rulesets') return json(rulesets);
      if (path === '/api/ai/profiles') return json(profiles);
      if (path === '/api/players' || path === '/api/models') return json([]);
      if (path.startsWith('/api/personal/')) return json({ games: [], totals: {}, overview: {} });
      if (path.startsWith('/api/history/')) return json({ games: [], totals: [] });
      if (path.startsWith('/api/online/games?')) return json({ games: [] });
      if (path === '/api/online/availability') return json({ enabled: false, eligible: false });
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
  fireEvent.click(screen.getByRole('button', { name: 'AI workshop' }));
  fireEvent.click(screen.getByRole('button', { name: 'Discard fixture model games' }));
  await waitFor(() => expect(localStorage.getItem('hnefatafl-game')).toBeNull());
  await act(async () => finish(await json({ ...initial, version: 1 })));
  fireEvent.click(screen.getByRole('button', { name: 'Dashboard' }));
  expect(screen.getByRole('heading', { name: 'Dashboard' })).toBeInTheDocument();
  expect(screen.queryByRole('grid')).not.toBeInTheDocument();
});
