import { act, cleanup, render, renderHook, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import AgentAnalysis, { useGameAnalysis } from './AgentAnalysis';
import { advisorOptions } from './AdvisorSelect';
import type { Game } from './types';
import type { PositionAnalysis } from './analysisTypes';

const game = {
  id: 'one',
  version: 0,
  cursor: 0,
  ply: 0,
  size: 7,
  turn: 'attackers',
  ruleset: 'brandubh',
  board: ['A'],
  winner: null,
  reason: null,
  moves: [],
  total_moves: 0,
  legal_moves: [],
  mode: 'ai-ai',
  captured: { attackers: 0, defenders: 0 },
  created_at: '',
  analysis_enabled: true,
  players: {
    attackers: { name: 'Hugin', kind: 'ai', config: { algorithm: 'baseline' } },
    defenders: { name: 'Munin', kind: 'ai', config: { algorithm: 'alpha_beta' } },
  },
} as Game;
const report: PositionAnalysis = {
  cursor: 0,
  ply: 0,
  side_to_move: 'attackers',
  position_key: 'position-one',
  created_at: 'now',
  hint: null,
  evaluations: [
    {
      agent_id: 'attackers',
      name: 'Hugin',
      role: 'attackers',
      config: {},
      source: 'heuristic',
      win_probability: 0.7,
      estimated: true,
      calibrated: false,
    },
    {
      agent_id: 'defenders',
      name: 'Munin',
      role: 'defenders',
      config: {},
      source: 'heuristic',
      win_probability: 0.3,
      estimated: true,
      calibrated: false,
    },
  ],
};
const response = (value: unknown) => ({ ok: true, json: async () => value }) as Response;
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('compares both agents from the same current-player perspective and identifies estimates', () => {
  render(
    <AgentAnalysis game={game} report={report} pending={false} failed={false} retry={vi.fn()} />,
  );
  expect(screen.getByText('For the current player: Attackers')).toBeInTheDocument();
  expect(screen.getByRole('meter', { name: 'Hugin estimate for Attackers' })).toHaveAttribute(
    'aria-valuenow',
    '70',
  );
  expect(screen.getByRole('meter', { name: 'Munin estimate for Attackers' })).toHaveAttribute(
    'aria-valuenow',
    '30',
  );
  expect(screen.getByText(/not calibrated winning probabilities/)).toBeInTheDocument();
});

it('ignores a delayed result after switching games and reuses a recorded replay snapshot', async () => {
  let finish!: (value: Response) => void;
  const fetcher = vi.fn(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  vi.stubGlobal('fetch', fetcher);
  const view = renderHook(({ current }) => useGameAnalysis(current, true), {
    initialProps: { current: game },
  });
  expect(view.result.current.pending).toBe(true);
  const saved = { ...report, position_key: 'saved-position' };
  view.rerender({ current: { ...game, id: 'two', analysis: saved } });
  expect(view.result.current.report).toEqual(saved);
  await act(async () => finish(response(report)));
  expect(view.result.current.report).toEqual(saved);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(view.result.current.pending).toBe(false);
});

it('allows play after a failed analysis and never invents an estimate', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
  const view = renderHook(() => useGameAnalysis(game, true));
  await waitFor(() => expect(view.result.current.failed).toBe(true));
  expect(view.result.current.pending).toBe(false);
  expect(view.result.current.report).toBeNull();
});

it('offers compatible advisor models and keeps the no-advisor selection separate', () => {
  const options = advisorOptions(
    [],
    [
      { id: 'seven', name: 'Same name', ruleset: 'brandubh' },
      { id: 'nine', name: 'Same name', ruleset: 'tablut' },
    ],
    'brandubh',
  );
  expect(options.map((option) => option.id)).toEqual(['model:seven']);
  expect(options[0].advisor.config.model_id).toBe('seven');
});
