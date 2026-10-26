import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Tournaments from './Tournaments';
import type { AIProfile, Model, Ruleset } from './types';
import type { Tournament } from './tournamentTypes';
import { setLanguage } from './i18n';
import { notify } from './notifications';

const rulesets: Ruleset[] = [
  { id: 'tablut', name: 'Tablut', size: 9, description: '', rules_markdown: '' },
  { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
];
const profiles: AIProfile[] = [
  {
    id: 'shield_maiden',
    name: 'Shield maiden',
    algorithm: 'shield_maiden',
    difficulty: 'Intermediate',
    parameters: { time_limit: 1, heuristic_version: 1 },
    supported_rulesets: ['tablut', 'brandubh'],
  },
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
    id: 'mcts',
    name: 'Seer',
    algorithm: 'mcts',
    difficulty: '',
    parameters: { iterations: 80, temperature: 1 },
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
  setLanguage('en', false);
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
    fireEvent.change(setup.getByLabelText('Opponent 1 profile'), { target: { value: '' } });
    fireEvent.change(setup.getByLabelText('Opponent 1 time limit (seconds)'), {
      target: { value: '0.02' },
    });
    fireEvent.change(setup.getByLabelText('Opponent 2 profile'), { target: { value: '' } });
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
        { config: { algorithm: 'alpha_beta', depth: 5, time_limit: 0.15 } },
      ],
    });
  });

  it('configures neural wrappers and retains settings when switching search modes', async () => {
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
    show([{ id: 'raven-model', name: 'Raven model', ruleset: 'brandubh' }]);
    const create = screen.getByRole('button', { name: 'New tournament' });
    await waitFor(() => expect(create).toBeEnabled());
    fireEvent.click(create);
    const setup = within(screen.getByRole('dialog', { name: 'Set up a tournament' }));
    fireEvent.change(setup.getByLabelText('Opponent 2 profile'), { target: { value: 'baseline' } });
    expect(setup.queryByLabelText('Opponent 2 search wrapper')).not.toBeInTheDocument();
    const wrapper = setup.getByLabelText('Opponent 1 search wrapper');
    expect(wrapper).toHaveValue('mcts');
    fireEvent.change(setup.getByLabelText('Opponent 1 search iterations'), {
      target: { value: '93' },
    });
    fireEvent.change(wrapper, { target: { value: 'alpha_beta' } });
    expect(setup.queryByLabelText('Opponent 1 search iterations')).not.toBeInTheDocument();
    expect(setup.queryByLabelText('Opponent 1 exploration')).not.toBeInTheDocument();
    expect(setup.queryByLabelText('Opponent 1 temperature')).not.toBeInTheDocument();
    fireEvent.change(setup.getByLabelText('Opponent 1 search depth'), { target: { value: '3' } });
    fireEvent.change(wrapper, { target: { value: 'none' } });
    expect(setup.queryByLabelText('Opponent 1 time limit (seconds)')).not.toBeInTheDocument();
    expect(setup.queryByLabelText('Opponent 1 search depth')).not.toBeInTheDocument();
    fireEvent.change(setup.getByLabelText('Opponent 1 temperature'), { target: { value: '0.5' } });
    fireEvent.change(wrapper, { target: { value: 'mcts' } });
    expect(setup.getByLabelText('Opponent 1 search iterations')).toHaveValue(93);
    expect(setup.getByLabelText('Opponent 1 temperature')).toHaveValue(0.5);
    fireEvent.change(wrapper, { target: { value: 'alpha_beta' } });
    expect(setup.getByLabelText('Opponent 1 search depth')).toHaveValue(3);
    fireEvent.click(setup.getByRole('button', { name: 'Start tournament' }));
    await waitFor(() => expect(submitted).toBeDefined());
    expect(submitted).toMatchObject({
      players: [
        {
          config: {
            algorithm: 'neural',
            model_id: 'raven-model',
            search_wrapper: 'alpha_beta',
            depth: 3,
          },
        },
        { config: { algorithm: 'baseline' } },
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
    expect(setup.getByLabelText('Opponent 1 trained model')).toHaveValue('raven-model');
    expect(start).toBeEnabled();
    fireEvent.change(setup.getByLabelText('Opponent 1 trained model'), {
      target: { value: '' },
    });
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

  it('suggests the strongest compatible model while preserving manual opponents across model updates', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => json([])),
    );
    const weak: Model = {
      id: 'weak1111',
      name: 'Raven',
      ruleset: 'brandubh',
      version: 2,
      training_config: { iterations: 20, channels: 32, blocks: 2 },
      evaluation: { wins: 1, losses: 3 },
    };
    const strong: Model = {
      ...weak,
      id: 'strong22',
      version: 8,
      evaluation: { wins: 3, losses: 1 },
    };
    const { rerender } = show([weak, strong]);
    const create = screen.getByRole('button', { name: 'New tournament' });
    await waitFor(() => expect(create).toBeEnabled());
    fireEvent.click(create);
    const setup = within(screen.getByRole('dialog', { name: 'Set up a tournament' }));
    expect(setup.getByLabelText('Opponent 1 trained model')).toHaveValue('strong22');
    expect(setup.getByLabelText('Opponent 2 trained model')).toHaveValue('strong22');
    const labels = within(setup.getByLabelText('Opponent 1 trained model'))
      .getAllByRole('option')
      .map((option) => option.textContent);
    expect(labels).toContain('Raven · iteration 2/20 · 32 channels · 2 blocks · #weak1111');
    expect(labels).toContain('Raven · iteration 8/20 · 32 channels · 2 blocks · #strong22');
    fireEvent.change(setup.getByLabelText('Opponent 1 trained model'), {
      target: { value: 'weak1111' },
    });
    fireEvent.change(setup.getByLabelText('Opponent 2 profile'), { target: { value: 'baseline' } });
    const newer = { ...strong, id: 'newest33', evaluation: { wins: 10, losses: 0 } };
    rerender(
      <Tournaments
        rulesets={rulesets}
        profiles={profiles}
        models={[weak, strong, newer]}
        onError={vi.fn()}
      />,
    );
    expect(setup.getByLabelText('Opponent 1 trained model')).toHaveValue('weak1111');
    expect(setup.getByLabelText('Opponent 2 profile')).toHaveValue('baseline');
    fireEvent.click(setup.getByRole('button', { name: 'Add opponent' }));
    expect(setup.getByLabelText('Opponent 3 trained model')).toHaveValue('newest33');
  });

  it('prefills mythological names, rerolls without repeating the current title, and localizes setup', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => json([])),
    );
    show();
    const create = screen.getByRole('button', { name: 'New tournament' });
    await waitFor(() => expect(create).toBeEnabled());
    fireEvent.click(create);
    const field = screen.getByLabelText('Tournament name') as HTMLInputElement;
    const original = field.value;
    expect(original).toMatch(/’s /);
    fireEvent.click(screen.getByRole('button', { name: 'Suggest tournament name' }));
    expect(field.value).not.toBe(original);
    fireEvent.change(field, { target: { value: 'My own tournament' } });
    act(() => setLanguage('de', false));
    expect(screen.getByRole('dialog', { name: 'Turnier einrichten' })).toBeInTheDocument();
    expect(screen.getByLabelText('Turniername')).toHaveValue('My own tournament');
    fireEvent.click(screen.getByRole('button', { name: 'Turniernamen vorschlagen' }));
    expect(field.value).toMatch(
      /^(Odins|Freyjas|Thors|Tyrs|Skadis|Heimdalls|Baldrs|Njörds|Idunns|Friggs|Hugins|Munins) /,
    );
    expect(screen.getByRole('button', { name: 'Turnier starten' })).toBeEnabled();
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
    fireEvent.change(setup.getByLabelText('Opponent 4 profile'), {
      target: { value: 'alpha_beta' },
    });
    expect(setup.getByText('12', { selector: '.tournament-run-count strong' })).toBeInTheDocument();
    fireEvent.click(setup.getByRole('checkbox', { name: 'Tablut 9 × 9' }));
    expect(setup.getByText('24', { selector: '.tournament-run-count strong' })).toBeInTheDocument();
    fireEvent.click(setup.getByRole('button', { name: 'Remove opponent 3' }));
    expect(setup.getByLabelText('Opponent 3 profile')).toHaveValue('alpha_beta');
    expect(setup.queryByLabelText('Opponent 4 profile')).not.toBeInTheDocument();
    expect(setup.getByText('12', { selector: '.tournament-run-count strong' })).toBeInTheDocument();
    fireEvent.click(setup.getByRole('button', { name: 'Remove opponent 3' }));
    expect(setup.queryAllByRole('button', { name: /^Remove opponent/ })).toHaveLength(0);
    fireEvent.click(setup.getByRole('button', { name: 'Add opponent' }));
    fireEvent.click(setup.getByRole('button', { name: 'Add opponent' }));
    fireEvent.change(setup.getByLabelText('Opponent 3 profile'), { target: { value: 'baseline' } });
    fireEvent.change(setup.getByLabelText('Opponent 4 profile'), {
      target: { value: 'alpha_beta' },
    });
    fireEvent.click(setup.getByRole('button', { name: 'Start tournament' }));
    await waitFor(() => expect(submitted).toBeDefined());
    expect(submitted).toMatchObject({
      games: 2,
      rulesets: ['brandubh', 'tablut'],
      players: [
        { profile_id: 'baseline' },
        { profile_id: 'alpha_beta' },
        { profile_id: 'baseline' },
        { profile_id: 'alpha_beta' },
      ],
    });
  });

  it('uses model names without aliases and shows saved network and training budgets', async () => {
    const model: Model = {
      id: 'raven-model',
      name: 'Raven model',
      ruleset: 'brandubh',
      rune: 'ᚱ',
      iteration: 4,
      architecture: { channels: 32, blocks: 2 },
      training_config: {
        iterations: 20,
        games: 16,
        mcts_iterations: 128,
        mcts_time_limit: 2,
        optimizer: 'adamw',
        learning_rate: 0.001,
        seed: 42,
      },
    };
    const tournament = saved('model');
    tournament.config.players[0] = {
      name: 'Old confusing alias',
      model_name: model.name,
      model,
      rune: 'ᚠ',
      agent_rune: 'ᚱ',
      profile_name: 'Trained model',
      config: { algorithm: 'neural', model_id: model.id, iterations: 5 },
    };
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => json(path === '/api/tournaments' ? [tournament] : tournament)),
    );
    const { container } = show([model]);
    await screen.findByRole('heading', { name: 'Challenge model' });
    const standing = within(container.querySelector('.rival-1')! as HTMLElement);
    expect(standing.getByRole('heading', { name: 'Raven model' })).toBeInTheDocument();
    expect(standing.queryByText('Old confusing alias')).not.toBeInTheDocument();
    expect(standing.getByLabelText('Model network and training')).toHaveTextContent(
      '32 channels · 2 blocks',
    );
    expect(standing.getByLabelText('Model network and training')).toHaveTextContent(
      '128 MCTS iterations',
    );
    expect(standing.getByLabelText('Model network and training')).toHaveTextContent(
      '16 games / iteration',
    );
    expect(container.querySelector('.rival-1 .tournament-avatar')).toHaveTextContent('ᚱ');
    fireEvent.click(screen.getByRole('button', { name: 'New tournament' }));
    const setup = within(screen.getByRole('dialog', { name: 'Set up a tournament' }));
    expect(setup.queryByLabelText('Opponent 1 name')).not.toBeInTheDocument();
    expect(setup.queryByRole('button', { name: /Suggest opponent/ })).not.toBeInTheDocument();
    expect(setup.getByRole('button', { name: 'Suggest tournament name' })).toHaveTextContent('⚄');
    expect(setup.getByLabelText('Opponent 1 trained model')).toHaveValue('raven-model');
    expect(setup.getAllByLabelText('Model network and training')).toHaveLength(2);
    expect(setup.getAllByLabelText('Model network and training')[0]).toHaveTextContent(
      '4 / 20 planned',
    );
  });

  it('names the actual third/fourth opponents in partial standings, battlefield wins and replay', async () => {
    const tournament = saved('last-pair');
    tournament.status = 'cancelled';
    tournament.config.players.push(
      { name: 'Eagle', config: { algorithm: 'baseline' } },
      { name: 'Fox', config: { algorithm: 'baseline' } },
    );
    tournament.config.players[2].rune = 'ᚠ';
    tournament.config.players[2].agent_rune = 'ᚱ';
    tournament.config.players[3].rune = 'ᚢ';
    tournament.config.players[3].agent_rune = 'ᚱ';
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
    expect(match.getByText('Eagle', { exact: false })).toHaveTextContent('ᚱEagle vs ᚱFox');
    expect(container.querySelector('.rival-3 .tournament-avatar')).toHaveTextContent('ᚱ');
    const wins = container.querySelectorAll('.tournament-battlefield-wins li');
    expect(wins).toHaveLength(4);
    expect(wins[3]).toHaveTextContent('Fox1 wins');
    fireEvent.click(screen.getByRole('button', { name: 'Match log' }));
    fireEvent.click(screen.getByRole('button', { name: 'Replay game 1' }));
    const replay = await screen.findByRole('dialog', { name: 'Replay game 1' });
    expect(replay).toHaveTextContent('ᚱEagle · Attackers vs ᚱFox · Defenders');
    expect(replay).toHaveTextContent('Fox wins');
  });
});

const withMatches = (id: string): Tournament => {
  const tournament = saved(id);
  tournament.result = {
    scores: [1, 1],
    standings: tournament.config.players.map((_, player) => ({
      player,
      wins: 1,
      losses: 1,
      draws: 0,
      truncated: 0,
      games: 2,
      score: 1,
    })),
    by_ruleset: { brandubh: { wins: [1, 1], draws: 0, truncated: 0, games: 2 } },
    games: [
      {
        ruleset: 'brandubh',
        winner: 'attackers',
        outcome: 'completed',
        reason: 'king_captured',
        plies: 12,
        moves: [],
        attacker_player: 0,
        defender_player: 1,
        average_decision_time: 0.01,
        total_decision_time: 0.12,
      },
      {
        ruleset: 'brandubh',
        winner: 'attackers',
        outcome: 'completed',
        reason: 'king_captured',
        plies: 20,
        moves: [],
        attacker_player: 1,
        defender_player: 0,
        average_decision_time: 0.02,
        total_decision_time: 0.4,
      },
    ],
    truncated: 0,
    average_game_length: 16,
    average_decision_time: 0.015,
  };
  return tournament;
};

describe('tournament match log disclosure', () => {
  it('starts collapsed and expands and collapses the recorded matches', async () => {
    const tournament = withMatches('log');
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => json(path === '/api/tournaments' ? [tournament] : tournament)),
    );
    show();
    await screen.findByRole('heading', { name: 'Challenge log' });
    const toggle = screen.getByRole('button', { name: 'Match log' });
    const panel = document.getElementById('tournament-match-log');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(toggle).toHaveAttribute('aria-controls', 'tournament-match-log');
    expect(panel).toHaveAttribute('hidden');
    expect(screen.getByText('Raven wins')).not.toBeVisible();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(panel).not.toHaveAttribute('hidden');
    expect(screen.getByText('Raven wins')).toBeVisible();
    expect(screen.getByText('Wolf wins')).toBeVisible();
    expect(screen.getAllByRole('button', { name: /^Replay game/ })).toHaveLength(2);

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByText('Raven wins')).not.toBeVisible();
  });

  it('collapses the match log when a different tournament is selected', async () => {
    const first = withMatches('first');
    const second = withMatches('second');
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => {
        if (path === '/api/tournaments') return json([first, second]);
        return json(path === '/api/tournaments/first' ? first : second);
      }),
    );
    show();
    await screen.findByRole('heading', { name: 'Challenge first' });
    const toggle = screen.getByRole('button', { name: 'Match log' });
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('Raven wins')).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: 'View tournament Challenge second' }));
    await screen.findByRole('heading', { name: 'Challenge second' });
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Match log' })).toHaveAttribute(
        'aria-expanded',
        'false',
      ),
    );
    expect(screen.getByText('Raven wins')).not.toBeVisible();
  });

  it('keeps the match log open when the selected tournament data refreshes', async () => {
    const tournament = withMatches('running');
    tournament.status = 'running';
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => {
        if (path === '/api/tournaments/running/cancel') return json({});
        return json(path === '/api/tournaments' ? [tournament] : tournament);
      }),
    );
    show();
    await screen.findByRole('heading', { name: 'Challenge running' });
    fireEvent.click(screen.getByRole('button', { name: 'Match log' }));
    expect(screen.getByRole('button', { name: 'Match log' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );

    fireEvent.click(screen.getByRole('button', { name: 'Stop tournament' }));
    await screen.findByText('Tournament stopped. All finished games have been saved.');
    expect(screen.getByRole('button', { name: 'Match log' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    expect(screen.getByText('Raven wins')).toBeVisible();
  });
});

it('labels the destructive tournament action as deleting tournament data', async () => {
  const tournament = saved('delete');
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) => json(path === '/api/tournaments' ? [tournament] : tournament)),
  );
  show();
  await screen.findByRole('heading', { name: 'Challenge delete' });
  const button = screen.getByRole('button', { name: 'Delete tournament data' });
  expect(button).toHaveClass('danger-text');
  fireEvent.click(button);
  expect(
    screen.getByRole('dialog', { name: 'Delete tournament Challenge delete?' }),
  ).toBeInTheDocument();
});

it('keeps Shield bearer, Jarl and Seer presets fixed while exposing independent custom search controls', async () => {
  let submitted: Record<string, unknown> | undefined;
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, options?: RequestInit) => {
      if (options?.method === 'POST') {
        submitted = JSON.parse(String(options.body));
        return json(saved('presets'));
      }
      return json(path === '/api/tournaments' ? [] : saved('presets'));
    }),
  );
  show();
  const create = screen.getByRole('button', { name: 'New tournament' });
  await waitFor(() => expect(create).toBeEnabled());
  fireEvent.click(create);
  const setup = within(screen.getByRole('dialog', { name: 'Set up a tournament' }));
  expect(setup.queryByLabelText('Opponent 2 time limit (seconds)')).not.toBeInTheDocument();
  expect(setup.queryByLabelText('Opponent 2 search depth')).not.toBeInTheDocument();
  expect(setup.queryByLabelText('Opponent 1 time limit (seconds)')).not.toBeInTheDocument();
  expect(setup.getByText('Tactical heuristic · 1s')).toBeInTheDocument();
  fireEvent.click(setup.getByRole('button', { name: 'Add opponent' }));
  expect(setup.queryByLabelText('Opponent 3 time limit (seconds)')).not.toBeInTheDocument();
  fireEvent.change(setup.getByLabelText('Opponent 1 profile'), { target: { value: 'alpha_beta' } });
  fireEvent.change(setup.getByLabelText('Opponent 2 profile'), { target: { value: 'mcts' } });
  expect(setup.queryByLabelText('Opponent 1 time limit (seconds)')).not.toBeInTheDocument();
  expect(setup.queryByLabelText('Opponent 2 search iterations')).not.toBeInTheDocument();
  fireEvent.click(setup.getByRole('button', { name: 'Start tournament' }));
  await waitFor(() => expect(submitted).toBeDefined());
  expect(submitted).toMatchObject({
    players: [
      { profile_id: 'alpha_beta', config: { algorithm: 'alpha_beta', depth: 5, time_limit: 2 } },
      {
        profile_id: 'mcts',
        config: {
          algorithm: 'mcts',
          iterations: 5000,
          temperature: 0,
          exploration: 1.4,
          time_limit: 2,
        },
      },
      { profile_id: 'baseline', config: { algorithm: 'baseline', time_limit: 1 } },
    ],
  });
});

it('lets custom tournament opponents select algorithms and edit search settings', async () => {
  let submitted: Record<string, unknown> | undefined;
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, options?: RequestInit) => {
      if (options?.method === 'POST') {
        submitted = JSON.parse(String(options.body));
        return json(saved('custom'));
      }
      return json(path === '/api/tournaments' ? [] : saved('custom'));
    }),
  );
  show();
  const create = screen.getByRole('button', { name: 'New tournament' });
  await waitFor(() => expect(create).toBeEnabled());
  fireEvent.click(create);
  const setup = within(screen.getByRole('dialog', { name: 'Set up a tournament' }));
  fireEvent.change(setup.getByLabelText('Opponent 2 profile'), { target: { value: '' } });
  fireEvent.change(setup.getByLabelText('Opponent 2 algorithm'), { target: { value: 'mcts' } });
  fireEvent.change(setup.getByLabelText('Opponent 2 search iterations'), {
    target: { value: '17' },
  });
  fireEvent.change(setup.getByLabelText('Opponent 2 time limit (seconds)'), {
    target: { value: '0.15' },
  });
  fireEvent.click(setup.getByRole('button', { name: 'Start tournament' }));
  await waitFor(() => expect(submitted).toBeDefined());
  const players = (
    submitted as { players: { profile_id?: string; config: Record<string, unknown> }[] }
  ).players;
  expect(players[1].profile_id).toBeUndefined();
  expect(players[1].config).toMatchObject({ algorithm: 'mcts', iterations: 17, time_limit: 0.15 });
});

it('snapshots Shield maiden as a fixed tournament preset', async () => {
  const fetcher = vi.fn((path: string, init?: RequestInit) =>
    json(init?.method === 'POST' || path !== '/api/tournaments' ? saved('maiden') : []),
  );
  vi.stubGlobal('fetch', fetcher);
  render(<Tournaments rulesets={rulesets} profiles={profiles} models={[]} onError={vi.fn()} />);
  const newButton = screen.getByRole('button', { name: 'New tournament' });
  await waitFor(() => expect(newButton).toBeEnabled());
  fireEvent.click(newButton);
  const setup = within(screen.getByRole('dialog'));
  fireEvent.change(setup.getByLabelText('Opponent 1 profile'), {
    target: { value: 'shield_maiden' },
  });
  fireEvent.change(setup.getByLabelText('Opponent 2 profile'), { target: { value: 'baseline' } });
  expect(setup.queryByLabelText('Opponent 1 algorithm')).not.toBeInTheDocument();
  fireEvent.click(setup.getByRole('button', { name: 'Start tournament' }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true),
  );
  const sent = JSON.parse(
    String(fetcher.mock.calls.find(([, init]) => init?.method === 'POST')![1]?.body),
  );
  expect(sent.players[0]).toMatchObject({
    profile_id: 'shield_maiden',
    config: { algorithm: 'shield_maiden', time_limit: 1, heuristic_version: 1 },
  });
});

const importedDocument = {
  name: 'Imported saga',
  rulesets: ['brandubh'],
  players: [
    { profile_id: 'baseline', config: { algorithm: 'baseline', time_limit: 1 } },
    { config: { algorithm: 'mcts', iterations: 33, time_limit: 7.5 } },
  ],
  games: 4,
  max_plies: 123,
  seed: 9,
};
const importedFile = (value: unknown) =>
  new File([typeof value === 'string' ? value : JSON.stringify(value)], 'tournament.json', {
    type: 'application/json',
  });

it('imports a tournament configuration and prefills an editable form without creating', async () => {
  const fetcher = vi.fn((path: string, _init?: RequestInit) =>
    json(path === '/api/tournaments' ? [] : saved('imported')),
  );
  vi.stubGlobal('fetch', fetcher);
  render(<Tournaments rulesets={rulesets} profiles={profiles} models={[]} onError={vi.fn()} />);
  const create = screen.getByRole('button', { name: 'New tournament' });
  await waitFor(() => expect(create).toBeEnabled());
  fireEvent.click(create);
  const setup = within(screen.getByRole('dialog', { name: 'Set up a tournament' }));
  fireEvent.change(setup.getByLabelText('Import configuration'), {
    target: { files: [importedFile(importedDocument)] },
  });
  await waitFor(() => expect(setup.getByDisplayValue('Imported saga')).toBeInTheDocument());
  expect(setup.getByLabelText('Games per battlefield')).toHaveValue(4);
  expect(setup.getByLabelText('Move limit per game')).toHaveValue(123);
  expect(setup.getByLabelText('Tournament seed')).toHaveValue(9);
  // The custom opponent is kept and remains editable before starting.
  expect(setup.getByLabelText('Opponent 2 search iterations')).toHaveValue(33);
  // Import only fills the form; nothing is created until the user submits.
  expect(fetcher.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
});

it('reports malformed imports and leaves the form unchanged', async () => {
  const error = vi.spyOn(notify, 'error');
  const fetcher = vi.fn((path: string, _init?: RequestInit) =>
    json(path === '/api/tournaments' ? [] : saved('unchanged')),
  );
  vi.stubGlobal('fetch', fetcher);
  render(<Tournaments rulesets={rulesets} profiles={profiles} models={[]} onError={vi.fn()} />);
  const create = screen.getByRole('button', { name: 'New tournament' });
  await waitFor(() => expect(create).toBeEnabled());
  fireEvent.click(create);
  const setup = within(screen.getByRole('dialog', { name: 'Set up a tournament' }));
  const nameInput = setup.getByLabelText('Tournament name') as HTMLInputElement;
  const originalName = nameInput.value;
  fireEvent.change(setup.getByLabelText('Import configuration'), {
    target: { files: [importedFile('{not json')] },
  });
  await waitFor(() => expect(error).toHaveBeenCalled());
  expect(nameInput.value).toBe(originalName);
  expect(setup.getByLabelText('Games per battlefield')).toHaveValue(2);
  expect(fetcher.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
});

it('reports unresolved model references and keeps the current participants', async () => {
  const error = vi.spyOn(notify, 'error');
  const fetcher = vi.fn((path: string, _init?: RequestInit) =>
    json(path === '/api/tournaments' ? [] : saved('unchanged')),
  );
  vi.stubGlobal('fetch', fetcher);
  render(<Tournaments rulesets={rulesets} profiles={profiles} models={[]} onError={vi.fn()} />);
  const create = screen.getByRole('button', { name: 'New tournament' });
  await waitFor(() => expect(create).toBeEnabled());
  fireEvent.click(create);
  const setup = within(screen.getByRole('dialog', { name: 'Set up a tournament' }));
  fireEvent.change(setup.getByLabelText('Import configuration'), {
    target: {
      files: [
        importedFile({
          ...importedDocument,
          players: [
            { profile_id: 'baseline', config: { algorithm: 'baseline', time_limit: 1 } },
            { config: { algorithm: 'neural', model_id: 'missing-model' } },
          ],
        }),
      ],
    },
  });
  await waitFor(() => expect(error).toHaveBeenCalled());
  expect(setup.queryByDisplayValue('Imported saga')).not.toBeInTheDocument();
  expect(setup.getByLabelText('Games per battlefield')).toHaveValue(2);
});

it('downloads an exported tournament result document', async () => {
  const createObjectURL = vi.fn(() => 'blob:tournament');
  const revokeObjectURL = vi.fn();
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL });
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  const fetcher = vi.fn((path: string) => {
    if (path === '/api/tournaments/export/export')
      return Promise.resolve({ ok: true, blob: async () => new Blob(['{}']) } as Response);
    return json(path === '/api/tournaments' ? [saved('export')] : saved('export'));
  });
  vi.stubGlobal('fetch', fetcher);
  render(<Tournaments rulesets={rulesets} profiles={profiles} models={[]} onError={vi.fn()} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Export results' }));
  await waitFor(() => expect(createObjectURL).toHaveBeenCalled());
  expect(click).toHaveBeenCalled();
  expect(fetcher.mock.calls.some(([path]) => path === '/api/tournaments/export/export')).toBe(true);
});

it('reports tournament export failures through the shared error handler', async () => {
  const error = vi.fn();
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) =>
      path === '/api/tournaments/export/export'
        ? Promise.resolve({
            ok: false,
            json: async () => ({ detail: 'The saved tournament results could not be exported.' }),
          } as Response)
        : json(path === '/api/tournaments' ? [saved('export')] : saved('export')),
    ),
  );
  render(<Tournaments rulesets={rulesets} profiles={profiles} models={[]} onError={error} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Export results' }));
  await waitFor(() =>
    expect(error).toHaveBeenCalledWith('The saved tournament results could not be exported.'),
  );
});
