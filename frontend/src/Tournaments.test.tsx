import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Tournaments from './Tournaments';
import type { AIProfile, Model, Ruleset } from './types';
import type { Tournament } from './tournamentTypes';

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
    parameters: {},
    supported_rulesets: [],
  },
  {
    id: 'alpha_beta',
    name: 'Jarl',
    algorithm: 'alpha_beta',
    difficulty: '',
    parameters: { depth: 2 },
    supported_rulesets: [],
  },
  {
    id: 'neural',
    name: 'Trained model',
    algorithm: 'neural',
    difficulty: '',
    parameters: { iterations: 40 },
    supported_rulesets: [],
  },
];
const saved = (id: string): Tournament => ({
  id,
  name: `Challenge ${id}`,
  status: 'completed',
  created_at: '2026-10-21T08:00:00Z',
  config: {
    players: [
      { name: 'Raven', config: { algorithm: 'baseline', time_limit: 0.1 } },
      { name: 'Wolf', config: { algorithm: 'alpha_beta', depth: 2 } },
    ],
    rulesets: ['brandubh'],
    games: 2,
    max_plies: 1,
    seed: 19,
  },
  progress: { completed_games: 2, total_games: 2 },
});
const json = (value: unknown) => Promise.resolve({ ok: true, json: async () => value } as Response);
beforeEach(() => {
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
const show = (models: Model[] = []) =>
  render(<Tournaments rulesets={rulesets} profiles={profiles} models={models} onError={vi.fn()} />);

describe('tournament setup and saved selection', () => {
  it('submits balanced game counts and independent opponent settings', async () => {
    let submitted: Record<string, unknown> | undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string, options?: RequestInit) => {
        if (options?.method === 'POST') {
          submitted = JSON.parse(String(options.body));
          return json(saved('new'));
        }
        return json(path === '/api/tournaments' ? [] : saved('new'));
      }),
    );
    show();
    const newButton = screen.getByRole('button', { name: 'New tournament' });
    await waitFor(() => expect(newButton).toBeEnabled());
    fireEvent.click(newButton);
    const setup = within(screen.getByRole('dialog', { name: 'Set up a tournament' }));
    expect(setup.getAllByRole('checkbox').map((box) => box.getAttribute('aria-label'))).toEqual([
      'Brandubh 7 × 7',
      'Tablut 9 × 9',
    ]);
    fireEvent.change(setup.getByLabelText('Tournament name'), {
      target: { value: 'Ravens at dawn' },
    });
    fireEvent.change(setup.getByLabelText('Opponent 1 time limit (seconds)'), {
      target: { value: '0.02' },
    });
    fireEvent.change(setup.getByLabelText('Opponent 2 time limit (seconds)'), {
      target: { value: '0.15' },
    });
    fireEvent.change(setup.getByLabelText('Games per battlefield'), { target: { value: '4' } });
    fireEvent.click(setup.getByRole('button', { name: 'Start tournament' }));
    await waitFor(() => expect(submitted).toBeDefined());
    expect(submitted).toMatchObject({
      name: 'Ravens at dawn',
      games: 4,
      rulesets: ['brandubh'],
      players: [
        { config: { algorithm: 'baseline', time_limit: 0.02 } },
        { config: { algorithm: 'alpha_beta', depth: 2, time_limit: 0.15 } },
      ],
    });
  });

  it('requires a single compatible battlefield before allowing trained models', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => json([])),
    );
    show([{ id: 'raven-model', name: 'Raven model', ruleset: 'brandubh' }]);
    const newButton = screen.getByRole('button', { name: 'New tournament' });
    await waitFor(() => expect(newButton).toBeEnabled());
    fireEvent.click(newButton);
    const setup = within(screen.getByRole('dialog', { name: 'Set up a tournament' }));
    fireEvent.change(setup.getByLabelText('Opponent 1 profile'), { target: { value: 'neural' } });
    const start = setup.getByRole('button', { name: 'Start tournament' });
    expect(start).toBeDisabled();
    fireEvent.change(setup.getByLabelText('Opponent 1 trained model'), {
      target: { value: 'raven-model' },
    });
    expect(start).toBeEnabled();
    fireEvent.click(setup.getByRole('checkbox', { name: 'Tablut 9 × 9' }));
    expect(start).toBeDisabled();
    expect(
      setup.getByText('Choose a compatible trained model and its single matching battlefield.'),
    ).toBeInTheDocument();
  });

  it('keeps the latest selected result when an earlier detail request finishes late', async () => {
    let finish!: (response: Response) => void;
    let firstA = true;
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => {
        if (path === '/api/tournaments') return json([saved('a'), saved('b')]);
        if (path === '/api/tournaments/a' && firstA) {
          firstA = false;
          return new Promise<Response>((resolve) => {
            finish = resolve;
          });
        }
        return json(saved('b'));
      }),
    );
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'View tournament Challenge b' }));
    await screen.findByRole('heading', { name: 'Challenge b' });
    await act(async () => finish(await json(saved('a'))));
    expect(screen.getByRole('heading', { name: 'Challenge b' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Challenge a' })).not.toBeInTheDocument();
  });

  it('configures two to four opponents and scales the schedule by pairings and battlefields', async () => {
    let submitted: Record<string, unknown> | undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string, options?: RequestInit) => {
        if (options?.method === 'POST') {
          submitted = JSON.parse(String(options.body));
          return json(saved('four'));
        }
        return json(path === '/api/tournaments' ? [] : saved('four'));
      }),
    );
    show();
    const create = screen.getByRole('button', { name: 'New tournament' });
    await waitFor(() => expect(create).toBeEnabled());
    fireEvent.click(create);
    const setup = within(screen.getByRole('dialog', { name: 'Set up a tournament' }));
    expect(setup.queryAllByRole('button', { name: /^Remove opponent/ })).toHaveLength(0);
    fireEvent.click(setup.getByRole('button', { name: 'Add opponent' }));
    expect(setup.getByText('games across 3 pairings')).toBeInTheDocument();
    expect(setup.getByText('6', { selector: '.tournament-run-count strong' })).toBeInTheDocument();
    fireEvent.click(setup.getByRole('button', { name: 'Add opponent' }));
    expect(setup.getByRole('button', { name: 'Add opponent' })).toBeDisabled();
    expect(setup.getByText('12', { selector: '.tournament-run-count strong' })).toBeInTheDocument();
    fireEvent.click(setup.getByRole('checkbox', { name: 'Tablut 9 × 9' }));
    expect(setup.getByText('24', { selector: '.tournament-run-count strong' })).toBeInTheDocument();
    fireEvent.click(setup.getByRole('button', { name: 'Remove opponent 3' }));
    expect(setup.getByLabelText('Opponent 3 name')).toHaveValue('Strategist 4');
    expect(setup.queryByLabelText('Opponent 4 name')).not.toBeInTheDocument();
    expect(setup.getByText('12', { selector: '.tournament-run-count strong' })).toBeInTheDocument();
    fireEvent.click(setup.getByRole('button', { name: 'Remove opponent 3' }));
    expect(setup.queryAllByRole('button', { name: /^Remove opponent/ })).toHaveLength(0);
    fireEvent.click(setup.getByRole('button', { name: 'Add opponent' }));
    fireEvent.click(setup.getByRole('button', { name: 'Add opponent' }));
    fireEvent.change(setup.getByLabelText('Opponent 3 name'), { target: { value: 'Odin' } });
    fireEvent.change(setup.getByLabelText('Opponent 4 name'), { target: { value: 'Freya' } });
    fireEvent.click(setup.getByRole('button', { name: 'Start tournament' }));
    await waitFor(() => expect(submitted).toBeDefined());
    expect(submitted).toMatchObject({
      games: 2,
      rulesets: ['brandubh', 'tablut'],
      players: [{ name: 'Shield bearer' }, { name: 'Jarl' }, { name: 'Odin' }, { name: 'Freya' }],
    });
  });

  it('names the actual third/fourth opponents in partial standings, battlefield wins and replay', async () => {
    const tournament = saved('last-pair');
    tournament.status = 'cancelled';
    tournament.config.players.push(
      { name: 'Eagle', config: { algorithm: 'baseline' } },
      { name: 'Fox', config: { algorithm: 'baseline' } },
    );
    tournament.progress = { completed_games: 1, total_games: 12 };
    tournament.result = {
      scores: [0, 0, 0, 1],
      standings: tournament.config.players.map((_, player) => ({
        player,
        wins: player === 3 ? 1 : 0,
        losses: player === 2 ? 1 : 0,
        draws: 0,
        truncated: 0,
        games: player >= 2 ? 1 : 0,
        score: player === 3 ? 1 : 0,
      })),
      by_ruleset: { brandubh: { wins: [0, 0, 0, 1], draws: 0, truncated: 0, games: 1 } },
      games: [
        {
          ruleset: 'brandubh',
          winner: 'defenders',
          outcome: 'completed',
          reason: 'king_escaped',
          plies: 0,
          moves: [],
          attacker_player: 2,
          defender_player: 3,
          average_decision_time: 0,
          total_decision_time: 0,
        },
      ],
      truncated: 0,
      average_game_length: 0,
      average_decision_time: 0,
    };
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => {
        if (path.endsWith('/replay'))
          return json({
            validated: true,
            states: [
              {
                ruleset: 'brandubh',
                board: Array(49).fill('.'),
                turn: 'attackers',
                ply: 0,
                winner: 'defenders',
                reason: 'king_escaped',
              },
            ],
          });
        return json(path === '/api/tournaments' ? [tournament] : tournament);
      }),
    );
    const { container } = show();
    await screen.findByRole('heading', { name: 'Challenge last-pair' });
    expect(screen.getByText('Fox leads on points')).toBeInTheDocument();
    expect(container.querySelectorAll('.tournament-standing')).toHaveLength(4);
    const match = within(container.querySelector('.tournament-match')! as HTMLElement);
    expect(match.getByText('Fox wins')).toBeInTheDocument();
    expect(match.getByText('Eagle', { exact: false })).toHaveTextContent('Eagle vs Fox');
    const wins = container.querySelectorAll('.tournament-battlefield-wins li');
    expect(wins).toHaveLength(4);
    expect(wins[3]).toHaveTextContent('Fox1 wins');
    fireEvent.click(screen.getByRole('button', { name: 'Replay game 1' }));
    const replay = await screen.findByRole('dialog', { name: 'Replay game 1' });
    expect(replay).toHaveTextContent('Eagle · Attackers vs Fox · Defenders');
    expect(replay).toHaveTextContent('Fox wins');
  });
});
