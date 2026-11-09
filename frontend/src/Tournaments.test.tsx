import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Tournaments, { DETAIL_RETRY_DELAYS } from './Tournaments';
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
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  pollingHandlers.length = 0;
  retryTimers.clear();
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
        return json(path === '/api/tournaments?summary=true' ? [] : saved('new'));
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
      'Randomize starting positions',
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

  it('exposes alpha-beta optimization presets only for custom alpha-beta opponents', async () => {
    let submitted: Record<string, unknown> | undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string, options?: RequestInit) => {
        if (options?.method === 'POST') {
          submitted = JSON.parse(String(options.body));
          return json(saved('new'));
        }
        return json(path === '/api/tournaments?summary=true' ? [] : saved('new'));
      }),
    );
    show();
    const create = screen.getByRole('button', { name: 'New tournament' });
    await waitFor(() => expect(create).toBeEnabled());
    fireEvent.click(create);
    const setup = within(screen.getByRole('dialog', { name: 'Set up a tournament' }));
    // A fixed named preset exposes no optimization controls.
    expect(setup.queryByLabelText('Opponent 1 search preset')).not.toBeInTheDocument();
    fireEvent.change(setup.getByLabelText('Opponent 1 profile'), { target: { value: '' } });
    fireEvent.change(setup.getByLabelText('Opponent 1 algorithm'), {
      target: { value: 'alpha_beta' },
    });
    const heuristic = setup.getByLabelText('Opponent 1 heuristic');
    expect(heuristic).toHaveValue('shield_maiden');
    fireEvent.change(heuristic, { target: { value: 'shield_bearer' } });
    expect(heuristic).toHaveValue('shield_bearer');
    const preset = setup.getByLabelText('Opponent 1 search preset');
    expect(preset).toHaveValue('optimized');
    expect(setup.getByLabelText('Opponent 1 transposition table capacity')).toHaveValue(100000);
    fireEvent.change(preset, { target: { value: 'symmetry' } });
    expect(preset).toHaveValue('symmetry');
    expect(setup.getByLabelText('Opponent 1 symmetry move pruning')).toBeChecked();
    fireEvent.change(setup.getByLabelText('Opponent 1 transposition table capacity'), {
      target: { value: '2048' },
    });
    expect(preset).toHaveValue('custom');
    fireEvent.click(setup.getByRole('button', { name: 'Start tournament' }));
    await waitFor(() => expect(submitted).toBeDefined());
    const players = (submitted as { players: { config: Record<string, unknown> }[] }).players;
    expect(players[0].config.search).toEqual({
      legacy: false,
      transposition: true,
      move_ordering: true,
      symmetry_transposition: true,
      symmetry_pruning: true,
      table_capacity: 2048,
    });
    expect(players[0].config.heuristic).toBe('shield_bearer');
    // A non-alpha-beta opponent never carries search settings or an evaluator.
    expect(players[1].config.search).toBeUndefined();
    expect(players[1].config.heuristic).toBeUndefined();
  });

  it('disables irrelevant optimizations and explains the legacy alpha-beta search', async () => {
    let submitted: Record<string, unknown> | undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string, options?: RequestInit) => {
        if (options?.method === 'POST') {
          submitted = JSON.parse(String(options.body));
          return json(saved('new'));
        }
        return json(path === '/api/tournaments?summary=true' ? [] : saved('new'));
      }),
    );
    show();
    const create = screen.getByRole('button', { name: 'New tournament' });
    await waitFor(() => expect(create).toBeEnabled());
    fireEvent.click(create);
    const setup = within(screen.getByRole('dialog', { name: 'Set up a tournament' }));
    fireEvent.change(setup.getByLabelText('Opponent 1 profile'), { target: { value: '' } });
    fireEvent.change(setup.getByLabelText('Opponent 1 algorithm'), {
      target: { value: 'alpha_beta' },
    });
    const preset = setup.getByLabelText('Opponent 1 search preset');
    fireEvent.change(preset, { target: { value: 'legacy' } });
    expect(preset).toHaveValue('legacy');
    // Every optimized control and the capacity are disabled, so they cannot
    // produce a misleading Custom preset while legacy overrides them.
    expect(setup.getByLabelText('Opponent 1 transposition table')).toBeDisabled();
    expect(setup.getByLabelText('Opponent 1 improved move ordering')).toBeDisabled();
    expect(setup.getByLabelText('Opponent 1 d4-canonical transpositions')).toBeDisabled();
    expect(setup.getByLabelText('Opponent 1 symmetry move pruning')).toBeDisabled();
    expect(setup.getByLabelText('Opponent 1 transposition table capacity')).toBeDisabled();
    expect(setup.getByText(/original exact-value cache/)).toBeInTheDocument();
    fireEvent.click(setup.getByRole('button', { name: 'Start tournament' }));
    await waitFor(() => expect(submitted).toBeDefined());
    const players = (submitted as { players: { config: Record<string, unknown> }[] }).players;
    expect(players[0].config.search).toEqual({
      legacy: true,
      transposition: true,
      move_ordering: true,
      symmetry_transposition: false,
      symmetry_pruning: false,
      table_capacity: 100000,
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
        return json(path === '/api/tournaments?summary=true' ? [] : saved('new'));
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

  it('exposes model-backed alpha-beta optimizations for a custom neural tournament opponent', async () => {
    let submitted: { players: { config: Record<string, unknown> }[] } | undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string, options?: RequestInit) => {
        if (options?.method === 'POST') {
          submitted = JSON.parse(String(options.body));
          return json(saved('neural-search'));
        }
        return json(path === '/api/tournaments?summary=true' ? [] : saved('neural-search'));
      }),
    );
    show([{ id: 'raven-model', name: 'Raven model', ruleset: 'brandubh' }]);
    const create = screen.getByRole('button', { name: 'New tournament' });
    await waitFor(() => expect(create).toBeEnabled());
    fireEvent.click(create);
    const setup = within(screen.getByRole('dialog', { name: 'Set up a tournament' }));
    // The recommended opponent for a known battlefield is a trained model.
    fireEvent.change(setup.getByLabelText('Opponent 1 search wrapper'), {
      target: { value: 'alpha_beta' },
    });
    expect(setup.getByText('Alpha-beta optimizations')).toBeInTheDocument();
    expect(setup.getByLabelText('Opponent 1 symmetry move pruning')).toBeDisabled();
    expect(setup.queryByRole('option', { name: 'Full symmetry' })).not.toBeInTheDocument();
    fireEvent.change(setup.getByLabelText('Opponent 1 search preset'), {
      target: { value: 'custom' },
    });
    fireEvent.click(setup.getByLabelText('Opponent 1 improved move ordering'));
    fireEvent.change(setup.getByLabelText('Opponent 1 search depth'), { target: { value: '3' } });
    fireEvent.click(setup.getByRole('button', { name: 'Start tournament' }));
    await waitFor(() => expect(submitted).toBeDefined());
    expect(submitted?.players[0].config).toMatchObject({
      algorithm: 'neural',
      model_id: 'raven-model',
      search_wrapper: 'alpha_beta',
      depth: 3,
      search: {
        legacy: false,
        transposition: true,
        move_ordering: false,
        symmetry_transposition: false,
        symmetry_pruning: false,
        table_capacity: 100000,
      },
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
        if (path === '/api/tournaments?summary=true') return json([saved('a'), saved('b')]);
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
        return json(path === '/api/tournaments?summary=true' ? [] : saved('four'));
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
      vi.fn((path: string) =>
        json(path === '/api/tournaments?summary=true' ? [tournament] : tournament),
      ),
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
        return json(path === '/api/tournaments?summary=true' ? [tournament] : tournament);
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

  it('shows per-opponent search diagnostics in a collapsible panel', async () => {
    const tournament = saved('diagnostics');
    tournament.result = {
      scores: [1, 0],
      standings: [
        {
          player: 0,
          wins: 1,
          losses: 0,
          draws: 0,
          truncated: 0,
          games: 1,
          score: 1,
          diagnostics: {
            decisions: 12,
            mean_completed_depth: 2.5,
            mean_decision_time: 0.02,
            mean_nodes: 1234,
            mean_evaluations: 567,
            transposition_cutoffs: 9,
            symmetry_moves_skipped: 4,
            depth_cap_reached_fraction: 0.5,
          },
        },
        {
          player: 1,
          wins: 0,
          losses: 1,
          draws: 0,
          truncated: 0,
          games: 1,
          score: 0,
          diagnostics: {
            decisions: 11,
            mean_completed_depth: 2,
            mean_decision_time: 0.01,
            mean_nodes: 800,
            mean_evaluations: 400,
            transposition_cutoffs: 0,
            symmetry_moves_skipped: 0,
            depth_cap_reached_fraction: 1,
          },
        },
      ],
      by_ruleset: { brandubh: { wins: [1, 0], draws: 0, truncated: 0, games: 1 } },
      games: [],
      truncated: 0,
      average_game_length: 5,
      average_decision_time: 0.015,
    };
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) =>
        json(path === '/api/tournaments?summary=true' ? [tournament] : tournament),
      ),
    );
    const { container } = show();
    await screen.findByRole('heading', { name: 'Challenge diagnostics' });
    // The detailed metrics stay inside a collapsed disclosure so they do not
    // clutter the normal standings display.
    const panels = container.querySelectorAll('details.tournament-diagnostics');
    expect(panels).toHaveLength(2);
    const first = within(panels[0] as HTMLElement);
    expect(first.getByText('Mean completed depth')).toBeInTheDocument();
    expect(first.getByText('2.50')).toBeInTheDocument();
    expect(first.getByText('Transposition cutoffs')).toBeInTheDocument();
    expect(first.getByText('9')).toBeInTheDocument();
    expect(first.getByText('50%')).toBeInTheDocument();
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
      vi.fn((path: string) =>
        json(path === '/api/tournaments?summary=true' ? [tournament] : tournament),
      ),
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
        if (path === '/api/tournaments?summary=true') return json([first, second]);
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
        return json(path === '/api/tournaments?summary=true' ? [tournament] : tournament);
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
    vi.fn((path: string) =>
      json(path === '/api/tournaments?summary=true' ? [tournament] : tournament),
    ),
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
      return json(path === '/api/tournaments?summary=true' ? [] : saved('presets'));
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
  // A named preset is authoritative on the server, so no editable control may
  // imply its recorded evaluator can be overridden.
  expect(setup.queryByLabelText('Opponent 1 heuristic')).not.toBeInTheDocument();
  expect(setup.queryByLabelText('Opponent 2 heuristic')).not.toBeInTheDocument();
  expect(setup.queryByText('Alpha-beta optimizations')).not.toBeInTheDocument();
  expect(setup.getByText('Alpha-beta · depth 5 · 2s')).toBeInTheDocument();
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
      return json(path === '/api/tournaments?summary=true' ? [] : saved('custom'));
    }),
  );
  show();
  const create = screen.getByRole('button', { name: 'New tournament' });
  await waitFor(() => expect(create).toBeEnabled());
  fireEvent.click(create);
  const setup = within(screen.getByRole('dialog', { name: 'Set up a tournament' }));
  fireEvent.change(setup.getByLabelText('Opponent 2 profile'), { target: { value: '' } });
  fireEvent.change(setup.getByLabelText('Opponent 2 algorithm'), { target: { value: 'mcts' } });
  // A custom participant keeps every model-free search control.
  expect(setup.getByLabelText('Opponent 2 heuristic')).toHaveValue('shield_maiden');
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
    json(
      init?.method === 'POST' || path !== '/api/tournaments?summary=true' ? saved('maiden') : [],
    ),
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
    json(path === '/api/tournaments?summary=true' ? [] : saved('imported')),
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
    json(path === '/api/tournaments?summary=true' ? [] : saved('unchanged')),
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
    json(path === '/api/tournaments?summary=true' ? [] : saved('unchanged')),
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

it('submits a model-backed MCTS opponent imported from a configuration', async () => {
  let submitted: Record<string, unknown> | undefined;
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, options?: RequestInit) => {
      if (options?.method === 'POST') {
        submitted = JSON.parse(String(options.body));
        return json(saved('backed'));
      }
      return json(path === '/api/tournaments?summary=true' ? [] : saved('backed'));
    }),
  );
  render(
    <Tournaments
      rulesets={rulesets}
      profiles={profiles}
      models={[{ id: 'model-brandubh', name: 'Raven', ruleset: 'brandubh' }]}
      onError={vi.fn()}
    />,
  );
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
            {
              config: {
                algorithm: 'mcts',
                model_id: 'model-brandubh',
                iterations: 200,
                time_limit: 2,
              },
            },
            importedDocument.players[1],
          ],
        }),
      ],
    },
  });
  await waitFor(() => expect(setup.getByDisplayValue('Imported saga')).toBeInTheDocument());
  fireEvent.click(setup.getByRole('button', { name: 'Start tournament' }));
  await waitFor(() => expect(submitted).toBeDefined());
  // The registered model survives the import and reaches the API.
  expect(
    (submitted as { players: { config: Record<string, unknown> }[] }).players[0].config,
  ).toMatchObject({
    algorithm: 'mcts',
    model_id: 'model-brandubh',
    evaluation: 'neural',
    iterations: 200,
    time_limit: 2,
  });
});

it('rejects an unknown AI field on import without changing the form', async () => {
  const error = vi.spyOn(notify, 'error');
  const fetcher = vi.fn((path: string, _init?: RequestInit) =>
    json(path === '/api/tournaments?summary=true' ? [] : saved('unchanged')),
  );
  vi.stubGlobal('fetch', fetcher);
  render(<Tournaments rulesets={rulesets} profiles={profiles} models={[]} onError={vi.fn()} />);
  const create = screen.getByRole('button', { name: 'New tournament' });
  await waitFor(() => expect(create).toBeEnabled());
  fireEvent.click(create);
  const setup = within(screen.getByRole('dialog', { name: 'Set up a tournament' }));
  fireEvent.change(setup.getByLabelText('Tournament name'), { target: { value: 'Keep me' } });
  fireEvent.change(setup.getByLabelText('Games per battlefield'), { target: { value: '6' } });
  fireEvent.change(setup.getByLabelText('Move limit per game'), { target: { value: '123' } });
  fireEvent.change(setup.getByLabelText('Tournament seed'), { target: { value: '9' } });
  fireEvent.click(setup.getByRole('checkbox', { name: 'Tablut 9 × 9' }));
  const profilesBefore = setup
    .getAllByLabelText(/Opponent \d profile/)
    .map((field) => (field as HTMLSelectElement).value);
  fireEvent.change(setup.getByLabelText('Import configuration'), {
    target: {
      files: [
        importedFile({
          ...importedDocument,
          players: [{ config: { algorithm: 'mcts', iteratons: 500 } }, importedDocument.players[1]],
        }),
      ],
    },
  });
  await waitFor(() => expect(error).toHaveBeenCalled());
  expect(String(error.mock.calls.at(-1)?.[0])).toContain('players[0].config.iteratons');
  // A failed import never partially overwrites the setup form.
  expect(setup.getByDisplayValue('Keep me')).toBeInTheDocument();
  expect(setup.getByLabelText('Games per battlefield')).toHaveValue(6);
  expect(setup.getByLabelText('Move limit per game')).toHaveValue(123);
  expect(setup.getByLabelText('Tournament seed')).toHaveValue(9);
  expect(setup.getByRole('checkbox', { name: 'Tablut 9 × 9' })).toBeChecked();
  expect(
    setup
      .getAllByLabelText(/Opponent \d profile/)
      .map((field) => (field as HTMLSelectElement).value),
  ).toEqual(profilesBefore);
  expect(fetcher.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
});

it('rejects a malformed AI configuration on import without changing the form', async () => {
  const error = vi.spyOn(notify, 'error');
  const fetcher = vi.fn((path: string, _init?: RequestInit) =>
    json(path === '/api/tournaments?summary=true' ? [] : saved('unchanged')),
  );
  vi.stubGlobal('fetch', fetcher);
  render(<Tournaments rulesets={rulesets} profiles={profiles} models={[]} onError={vi.fn()} />);
  const create = screen.getByRole('button', { name: 'New tournament' });
  await waitFor(() => expect(create).toBeEnabled());
  fireEvent.click(create);
  const setup = within(screen.getByRole('dialog', { name: 'Set up a tournament' }));
  fireEvent.change(setup.getByLabelText('Tournament name'), { target: { value: 'Keep me' } });
  fireEvent.change(setup.getByLabelText('Games per battlefield'), { target: { value: '6' } });
  fireEvent.change(setup.getByLabelText('Move limit per game'), { target: { value: '123' } });
  fireEvent.change(setup.getByLabelText('Tournament seed'), { target: { value: '9' } });
  const profilesBefore = setup
    .getAllByLabelText(/Opponent \d profile/)
    .map((field) => (field as HTMLSelectElement).value);
  fireEvent.change(setup.getByLabelText('Import configuration'), {
    target: {
      files: [
        importedFile({
          ...importedDocument,
          players: [{ config: 'mcts' }, importedDocument.players[1]],
        }),
      ],
    },
  });
  await waitFor(() => expect(error).toHaveBeenCalled());
  expect(String(error.mock.calls.at(-1)?.[0])).toContain('players[0].config');
  expect(setup.getByDisplayValue('Keep me')).toBeInTheDocument();
  expect(setup.getByLabelText('Games per battlefield')).toHaveValue(6);
  expect(setup.getByLabelText('Move limit per game')).toHaveValue(123);
  expect(setup.getByLabelText('Tournament seed')).toHaveValue(9);
  expect(setup.getByRole('checkbox', { name: 'Brandubh 7 × 7' })).toBeChecked();
  expect(
    setup
      .getAllByLabelText(/Opponent \d profile/)
      .map((field) => (field as HTMLSelectElement).value),
  ).toEqual(profilesBefore);
  expect(fetcher.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
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
    return json(path === '/api/tournaments?summary=true' ? [saved('export')] : saved('export'));
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
        : json(path === '/api/tournaments?summary=true' ? [saved('export')] : saved('export')),
    ),
  );
  render(<Tournaments rulesets={rulesets} profiles={profiles} models={[]} onError={error} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Export results' }));
  await waitFor(() =>
    expect(error).toHaveBeenCalledWith('The saved tournament results could not be exported.'),
  );
});

describe('tournament lazy loading', () => {
  it('requests the lightweight collection and only the selected match history', async () => {
    const fetcher = vi.fn((path: string) =>
      json(
        path === '/api/tournaments?summary=true' ? [saved('a'), saved('b')] : saved(path.slice(-1)),
      ),
    );
    vi.stubGlobal('fetch', fetcher);
    show();
    await screen.findByRole('heading', { name: 'Challenge a' });
    const paths = fetcher.mock.calls.map(([path]) => path);
    expect(paths).toContain('/api/tournaments?summary=true');
    expect(paths).toContain('/api/tournaments/a');
    // Unselected tournaments never transfer their match history.
    expect(paths).not.toContain('/api/tournaments/b');
    expect(paths.filter((path) => path === '/api/tournaments?summary=true')).toHaveLength(1);
  });

  it('loads details for a newly selected tournament without refetching the others', async () => {
    const fetcher = vi.fn((path: string) =>
      json(
        path === '/api/tournaments?summary=true' ? [saved('a'), saved('b')] : saved(path.slice(-1)),
      ),
    );
    vi.stubGlobal('fetch', fetcher);
    show();
    await screen.findByRole('heading', { name: 'Challenge a' });
    fireEvent.click(screen.getByRole('button', { name: 'View tournament Challenge b' }));
    await screen.findByRole('heading', { name: 'Challenge b' });
    const detailPaths = fetcher.mock.calls
      .map(([path]) => path)
      .filter((path) => path === '/api/tournaments/a' || path === '/api/tournaments/b');
    expect(detailPaths).toEqual(['/api/tournaments/a', '/api/tournaments/b']);
  });

  it('polls summaries without re-downloading unchanged history and refreshes when a game finishes', async () => {
    const running: Tournament = { ...withMatches('running'), status: 'running' };
    const fetcher = vi.fn((path: string) =>
      json(path === '/api/tournaments?summary=true' ? [running] : running),
    );
    vi.stubGlobal('fetch', fetcher);
    show();
    await screen.findByRole('heading', { name: 'Challenge running' });
    const detailCalls = () =>
      fetcher.mock.calls.filter(([path]) => path === '/api/tournaments/running').length;
    const listCalls = () =>
      fetcher.mock.calls.filter(([path]) => path === '/api/tournaments?summary=true').length;
    expect(detailCalls()).toBe(1);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 2200));
    });
    expect(listCalls()).toBeGreaterThan(1);
    // The completed game count did not change, so the history is not re-fetched.
    expect(detailCalls()).toBe(1);
    running.progress = { completed_games: 3, total_games: 3 };
    running.status = 'completed';
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 2200));
    });
    // A finished game is a meaningful change, so the selected history refreshes.
    expect(detailCalls()).toBe(2);
  }, 20000);

  it('never duplicates an in-flight detail request while polling', async () => {
    const running: Tournament = { ...withMatches('running'), status: 'running' };
    let detailCalls = 0;
    let release!: (response: Response) => void;
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => {
        if (path === '/api/tournaments?summary=true') return json([running]);
        detailCalls += 1;
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      }),
    );
    show();
    await waitFor(() => expect(detailCalls).toBe(1));
    // A loading state is shown while the selected record is still pending.
    expect(screen.getByRole('heading', { name: 'Opening the tournament…' })).toBeInTheDocument();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 2200));
    });
    // The summary polled while the detail was in flight, so it must not have
    // started a second identical request.
    expect(detailCalls).toBe(1);
    await act(async () => {
      release(await json(running));
    });
    await screen.findByRole('heading', { name: 'Challenge running' });
  }, 20000);

  it('clears the selection once the deleted tournament leaves the collection', async () => {
    let listing = [saved('gone')];
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string, options?: RequestInit) => {
        if (options?.method === 'DELETE') {
          listing = [];
          return json({ deleted: true, id: 'gone' });
        }
        return json(path === '/api/tournaments?summary=true' ? listing : saved('gone'));
      }),
    );
    show();
    await screen.findByRole('heading', { name: 'Challenge gone' });
    fireEvent.click(screen.getByRole('button', { name: 'Delete tournament data' }));
    const confirm = within(
      screen.getByRole('dialog', { name: 'Delete tournament Challenge gone?' }),
    );
    fireEvent.click(confirm.getByRole('button', { name: 'Delete' }));
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Challenge gone' })).not.toBeInTheDocument(),
    );
    expect(
      screen.getByRole('heading', { name: 'Let the strongest ideas meet.' }),
    ).toBeInTheDocument();
  });
});

describe('fixed-role comparison setup', () => {
  const modelA: Model = { id: 'model-a', name: 'Model A', ruleset: 'brandubh' };
  const modelB: Model = { id: 'model-b', name: 'Model B', ruleset: 'brandubh' };
  const openFixedSetup = async (models: Model[]) => {
    show(models);
    // The empty-state panel repeats the creation action, so target the toolbar one.
    const [button] = screen.getAllByRole('button', { name: 'Fixed-role comparison' });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    return within(screen.getByRole('dialog', { name: 'Fixed-role comparison' }));
  };

  it('keeps neural selectors and edited budgets available when the model catalogue arrives late', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => json([])),
    );
    const view = show();
    const button = screen.getAllByRole('button', { name: 'Fixed-role comparison' })[0];
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    const setup = within(screen.getByRole('dialog', { name: 'Fixed-role comparison' }));
    expect(setup.getByLabelText('Attackers trained model')).toHaveValue('');
    expect(setup.getByLabelText('Defenders trained model')).toHaveValue('');
    fireEvent.change(setup.getByLabelText('Attackers search iterations'), {
      target: { value: '256' },
    });
    view.rerender(
      <Tournaments rulesets={rulesets} profiles={profiles} models={[modelA]} onError={vi.fn()} />,
    );
    fireEvent.change(setup.getByLabelText('Attackers trained model'), {
      target: { value: modelA.id },
    });
    fireEvent.change(setup.getByLabelText('Defenders trained model'), {
      target: { value: modelA.id },
    });
    expect(setup.getByLabelText('Attackers search iterations')).toHaveValue(256);
    expect(setup.getByLabelText('Defenders search iterations')).toHaveValue(80);
    expect(setup.getByRole('button', { name: 'Start tournament' })).toBeEnabled();
  });

  it('submits independent search configurations for two distinct models', async () => {
    let submitted: Record<string, unknown> | undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string, options?: RequestInit) => {
        if (options?.method === 'POST') {
          submitted = JSON.parse(String(options.body));
          return json(saved('fixed'));
        }
        return json(path === '/api/tournaments?summary=true' ? [] : saved('fixed'));
      }),
    );
    const setup = await openFixedSetup([modelA, modelB]);
    expect(setup.getByLabelText('Attackers trained model')).toHaveValue('model-a');
    expect(setup.getByLabelText('Defenders trained model')).toHaveValue('model-a');
    fireEvent.change(setup.getByLabelText('Defenders trained model'), {
      target: { value: 'model-b' },
    });
    fireEvent.change(setup.getByLabelText('Number of games'), { target: { value: '5' } });
    fireEvent.change(setup.getByLabelText('Attackers search iterations'), {
      target: { value: '300' },
    });
    expect(setup.getByText('5', { selector: '.tournament-run-count strong' })).toBeInTheDocument();
    fireEvent.click(setup.getByRole('button', { name: 'Start tournament' }));
    await waitFor(() => expect(submitted).toBeDefined());
    expect(submitted).toMatchObject({
      mode: 'fixed_roles',
      rulesets: ['brandubh'],
      games: 5,
      max_plies: 300,
      seed: 42,
    });
    const players = (submitted as { players: { config: Record<string, unknown> }[] }).players;
    expect(players[0].config.model_id).toBe('model-a');
    expect(players[1].config.model_id).toBe('model-b');
    expect(players[0].config.iterations).toBe(300);
    expect(players[1].config.iterations).toBe(40);
  });

  it('allows the same model in both seats with an odd game count', async () => {
    let submitted: Record<string, unknown> | undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string, options?: RequestInit) => {
        if (options?.method === 'POST') {
          submitted = JSON.parse(String(options.body));
          return json(saved('self'));
        }
        return json(path === '/api/tournaments?summary=true' ? [] : saved('self'));
      }),
    );
    const setup = await openFixedSetup([modelA]);
    expect(setup.getByLabelText('Attackers trained model')).toHaveValue('model-a');
    expect(setup.getByLabelText('Defenders trained model')).toHaveValue('model-a');
    fireEvent.change(setup.getByLabelText('Number of games'), { target: { value: '7' } });
    fireEvent.click(setup.getByRole('button', { name: 'Start tournament' }));
    await waitFor(() => expect(submitted).toBeDefined());
    expect(submitted).toMatchObject({ games: 7 });
    const players = (submitted as { players: { config: Record<string, unknown> }[] }).players;
    expect(players[0].config.model_id).toBe('model-a');
    expect(players[1].config.model_id).toBe('model-a');
  });

  it('flags an incompatible model after a battlefield change instead of replacing it', async () => {
    const tablut: Model = { id: 'tablut-model', name: 'Tablut model', ruleset: 'tablut' };
    const setup = await openFixedSetup([modelA, tablut]);
    fireEvent.click(setup.getByRole('radio', { name: 'Tablut 9 × 9' }));
    // The previous selection is kept visible and reported, never silently swapped.
    expect(setup.getByLabelText('Attackers trained model')).toHaveValue('model-a');
    expect(setup.getByLabelText('Defenders trained model')).toHaveValue('model-a');
    expect(
      setup.getByText('Choose a compatible trained model and its single matching battlefield.'),
    ).toBeInTheDocument();
    const start = setup.getByRole('button', { name: 'Start tournament' });
    expect(start).toBeDisabled();
    fireEvent.change(setup.getByLabelText('Attackers trained model'), {
      target: { value: 'tablut-model' },
    });
    expect(start).toBeDisabled();
    fireEvent.change(setup.getByLabelText('Defenders trained model'), {
      target: { value: 'tablut-model' },
    });
    expect(start).toBeEnabled();
  });

  it('rejects an unknown AI field in a fixed-role import without changing the form', async () => {
    const error = vi.spyOn(notify, 'error');
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => json(path === '/api/tournaments?summary=true' ? [] : saved('fixed'))),
    );
    const setup = await openFixedSetup([modelA, modelB]);
    fireEvent.change(setup.getByLabelText('Tournament name'), { target: { value: 'Keep me' } });
    fireEvent.change(setup.getByLabelText('Number of games'), { target: { value: '5' } });
    fireEvent.change(setup.getByLabelText('Attackers search iterations'), {
      target: { value: '300' },
    });
    fireEvent.change(setup.getByLabelText('Attackers trained model'), {
      target: { value: 'model-b' },
    });
    fireEvent.change(setup.getByLabelText('Import configuration'), {
      target: {
        files: [
          importedFile({
            name: 'Broken fixed roles',
            mode: 'fixed_roles',
            rulesets: ['brandubh'],
            players: [
              { config: { algorithm: 'neural', model_id: 'model-a', iterations: 100 } },
              {
                config: {
                  algorithm: 'neural',
                  model_id: 'model-b',
                  iterations: 100,
                  exploraton: 1.4,
                },
              },
            ],
            games: 7,
            max_plies: 200,
            seed: 5,
          }),
        ],
      },
    });
    await waitFor(() => expect(error).toHaveBeenCalled());
    expect(String(error.mock.calls.at(-1)?.[0])).toContain('players[1].config.exploraton');
    expect(setup.getByDisplayValue('Keep me')).toBeInTheDocument();
    expect(setup.getByLabelText('Number of games')).toHaveValue(5);
    expect(setup.getByLabelText('Attackers search iterations')).toHaveValue(300);
    expect(setup.getByLabelText('Attackers trained model')).toHaveValue('model-b');
  });

  it('rejects a malformed AI configuration in a fixed-role import without changing the form', async () => {
    const error = vi.spyOn(notify, 'error');
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => json(path === '/api/tournaments?summary=true' ? [] : saved('fixed'))),
    );
    const setup = await openFixedSetup([modelA, modelB]);
    fireEvent.change(setup.getByLabelText('Tournament name'), { target: { value: 'Keep me' } });
    fireEvent.change(setup.getByLabelText('Number of games'), { target: { value: '5' } });
    fireEvent.change(setup.getByLabelText('Attackers search iterations'), {
      target: { value: '300' },
    });
    fireEvent.change(setup.getByLabelText('Attackers trained model'), {
      target: { value: 'model-b' },
    });
    fireEvent.change(setup.getByLabelText('Import configuration'), {
      target: {
        files: [
          importedFile({
            name: 'Malformed fixed roles',
            mode: 'fixed_roles',
            rulesets: ['brandubh'],
            players: [
              { config: { algorithm: null } },
              { config: { algorithm: 'neural', model_id: 'model-b' } },
            ],
            games: 7,
            max_plies: 200,
            seed: 5,
          }),
        ],
      },
    });
    await waitFor(() => expect(error).toHaveBeenCalled());
    expect(String(error.mock.calls.at(-1)?.[0])).toContain('players[0].config.algorithm');
    expect(setup.getByDisplayValue('Keep me')).toBeInTheDocument();
    expect(setup.getByLabelText('Number of games')).toHaveValue(5);
    expect(setup.getByLabelText('Attackers search iterations')).toHaveValue(300);
    expect(setup.getByLabelText('Attackers trained model')).toHaveValue('model-b');
  });

  it('keeps fixed-role and round-robin imports in their own setup', async () => {
    const error = vi.spyOn(notify, 'error');
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) =>
        json(path === '/api/tournaments?summary=true' ? [] : saved('imported')),
      ),
    );
    const fixedDocument = {
      name: 'Fixed saga',
      mode: 'fixed_roles',
      rulesets: ['brandubh'],
      players: [
        {
          config: {
            algorithm: 'neural',
            model_id: 'model-a',
            search_wrapper: 'mcts',
            iterations: 120,
            time_limit: 2,
          },
        },
        {
          config: {
            algorithm: 'neural',
            model_id: 'model-a',
            search_wrapper: 'mcts',
            iterations: 120,
            time_limit: 2,
          },
        },
      ],
      games: 7,
      max_plies: 200,
      seed: 5,
    };
    show([modelA]);
    const create = screen.getByRole('button', { name: 'New tournament' });
    await waitFor(() => expect(create).toBeEnabled());
    fireEvent.click(create);
    const roundRobin = within(screen.getByRole('dialog', { name: 'Set up a tournament' }));
    fireEvent.change(roundRobin.getByLabelText('Import configuration'), {
      target: { files: [importedFile(fixedDocument)] },
    });
    await waitFor(() =>
      expect(error).toHaveBeenCalledWith(
        'This is a fixed-role comparison. Import it from the fixed-role setup instead.',
      ),
    );
    expect(roundRobin.getByLabelText('Games per battlefield')).toHaveValue(2);
    fireEvent.click(roundRobin.getByRole('button', { name: 'Cancel' }));

    const fixed = await openFixedSetup([modelA]);
    fireEvent.change(fixed.getByLabelText('Import configuration'), {
      target: { files: [importedFile(importedDocument)] },
    });
    await waitFor(() =>
      expect(error).toHaveBeenCalledWith(
        'This is a round-robin tournament. Import it from the tournament setup instead.',
      ),
    );
    fireEvent.change(fixed.getByLabelText('Import configuration'), {
      target: { files: [importedFile(fixedDocument)] },
    });
    await waitFor(() => expect(fixed.getByDisplayValue('Fixed saga')).toBeInTheDocument());
    expect(fixed.getByLabelText('Number of games')).toHaveValue(7);
    expect(fixed.getByLabelText('Attackers search iterations')).toHaveValue(120);
    expect(fixed.getByLabelText('Move limit per game')).toHaveValue(200);
    expect(fixed.getByLabelText('Tournament seed')).toHaveValue(5);
  });
});

const liveTournament = (
  id: string,
  completed: number,
  status: Tournament['status'] = 'running',
  error?: string,
): Tournament => {
  const tournament = withMatches(id);
  tournament.status = status;
  tournament.progress = { completed_games: completed, total_games: 4 };
  if (error) tournament.error = error;
  return tournament;
};
const pollingHandlers: Array<() => void> = [];
/**
 * Capture only the screen's 1.8s polling interval so a poll is fired
 * explicitly, never by sleeping. Every other timer - including the one the
 * testing library uses to poll assertions - stays real.
 */
const capturePolling = () => {
  const realSetInterval = window.setInterval.bind(window) as (
    handler: TimerHandler,
    timeout?: number,
  ) => number;
  vi.spyOn(window, 'setInterval').mockImplementation(((
    handler: TimerHandler,
    timeout?: number,
  ): number => {
    if (timeout !== 1800) return realSetInterval(handler, timeout);
    pollingHandlers.push(handler as () => void);
    return 0;
  }) as typeof window.setInterval);
};
/** Fire one summary poll once the screen registered its interval. */
const pollOnce = async () => {
  await waitFor(() => expect(pollingHandlers.length).toBeGreaterThan(0), { timeout: 5000 });
  const handlers = pollingHandlers.splice(0, pollingHandlers.length);
  await act(async () => {
    for (const handler of handlers) handler();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};
const retryTimers = new Map<number, () => void>();
let retryTimerId = 0;
/**
 * Capture the bounded retry timers so a retry is fired explicitly. Every
 * other timer, including the one the testing library uses to bound an
 * assertion, keeps its real implementation.
 */
const captureRetries = () => {
  const realSetTimeout = window.setTimeout.bind(window) as (
    handler: TimerHandler,
    timeout?: number,
  ) => number;
  const realClearTimeout = window.clearTimeout.bind(window) as (id?: number) => void;
  vi.spyOn(window, 'setTimeout').mockImplementation(((
    handler: TimerHandler,
    timeout?: number,
  ): number => {
    if (timeout === undefined || !DETAIL_RETRY_DELAYS.includes(timeout))
      return realSetTimeout(handler, timeout);
    retryTimerId += 1;
    retryTimers.set(retryTimerId, handler as () => void);
    return retryTimerId;
  }) as typeof window.setTimeout);
  vi.spyOn(window, 'clearTimeout').mockImplementation(((id?: number) => {
    if (!retryTimers.delete(id ?? -1)) realClearTimeout(id);
  }) as typeof window.clearTimeout);
};
/** Fire the pending automatic retry and let the resulting promises settle. */
const flushRetry = async () => {
  await waitFor(() => expect(retryTimers.size).toBeGreaterThan(0), { timeout: 4000 });
  const handlers = [...retryTimers.values()];
  retryTimers.clear();
  await act(async () => {
    for (const handler of handlers) handler();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
};
const detailPaths = (paths: string[]) => paths.filter((path) => path.endsWith('/a'));

describe('tournament detail synchronisation', () => {
  it('fetches the completed details when a running response arrives late', async () => {
    let release!: (response: Response) => void;
    const paths: string[] = [];
    let summary = liveTournament('a', 2);
    capturePolling();
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => {
        if (path === '/api/tournaments?summary=true') return json([summary]);
        paths.push(path);
        if (paths.length === 1)
          return new Promise<Response>((resolve) => {
            release = resolve;
          });
        return json(liveTournament('a', 4, 'completed'));
      }),
    );
    show();
    await screen.findByRole('heading', { name: 'Opening the tournament…' }, { timeout: 5000 });
    expect(detailPaths(paths)).toHaveLength(1);
    // The tournament finishes while the first detail response is still pending.
    summary = liveTournament('a', 4, 'completed');
    await pollOnce();
    // The in-flight request is not duplicated by the poll.
    expect(detailPaths(paths)).toHaveLength(1);
    await act(async () => {
      release(await json(liveTournament('a', 2)));
    });
    // The stale running snapshot schedules one immediate follow-up fetch.
    await waitFor(() => expect(detailPaths(paths)).toHaveLength(2), { timeout: 5000 });
    await screen.findByText('4 of 4 games saved', undefined, { timeout: 5000 });
    expect(screen.getByText('Series finished')).toBeInTheDocument();
    expect(
      screen.getByText('Completed', { selector: '.tournament-detail .tournament-status' }),
    ).toBeInTheDocument();
  }, 20000);

  it('fetches the failure details when a failed summary arrives during a request', async () => {
    let release!: (response: Response) => void;
    const paths: string[] = [];
    const failure = 'The tournament worker stopped. Completed games have been kept.';
    let summary = liveTournament('a', 2);
    capturePolling();
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => {
        if (path === '/api/tournaments?summary=true') return json([summary]);
        paths.push(path);
        if (paths.length === 1)
          return new Promise<Response>((resolve) => {
            release = resolve;
          });
        return json(liveTournament('a', 2, 'failed', failure));
      }),
    );
    show();
    await screen.findByRole('heading', { name: 'Opening the tournament…' }, { timeout: 5000 });
    summary = liveTournament('a', 2, 'failed', failure);
    await pollOnce();
    await act(async () => {
      release(await json(liveTournament('a', 2)));
    });
    await waitFor(() => expect(detailPaths(paths)).toHaveLength(2), { timeout: 5000 });
    await screen.findByRole('alert', undefined, { timeout: 5000 });
    expect(screen.getByRole('alert')).toHaveTextContent(failure);
    expect(
      screen.getByText('Interrupted', { selector: '.tournament-detail .tournament-status' }),
    ).toBeInTheDocument();
    // A failure is terminal, so polling stops and no further refresh is queued.
    expect(pollingHandlers).toHaveLength(0);
    expect(detailPaths(paths)).toHaveLength(2);
  }, 20000);

  it('fetches the cancelled details and keeps the retained results', async () => {
    let release!: (response: Response) => void;
    const paths: string[] = [];
    let summary = liveTournament('a', 2);
    capturePolling();
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => {
        if (path === '/api/tournaments?summary=true') return json([summary]);
        paths.push(path);
        if (paths.length === 1)
          return new Promise<Response>((resolve) => {
            release = resolve;
          });
        return json(liveTournament('a', 3, 'cancelled'));
      }),
    );
    show();
    await screen.findByRole('heading', { name: 'Opening the tournament…' }, { timeout: 5000 });
    summary = liveTournament('a', 3, 'cancelled');
    await pollOnce();
    await act(async () => {
      release(await json(liveTournament('a', 2)));
    });
    await waitFor(() => expect(detailPaths(paths)).toHaveLength(2), { timeout: 5000 });
    await screen.findByText('3 of 4 games saved', undefined, { timeout: 5000 });
    expect(screen.getByText('Partial series saved')).toBeInTheDocument();
    expect(
      screen.getByText('Stopped', { selector: '.tournament-detail .tournament-status' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Match log' }));
    expect(screen.getByText('Raven wins')).toBeVisible();
  }, 20000);

  it('lands on the latest result after progress moved during a request', async () => {
    let release!: (response: Response) => void;
    const paths: string[] = [];
    let summary = liveTournament('a', 2);
    capturePolling();
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => {
        if (path === '/api/tournaments?summary=true') return json([summary]);
        paths.push(path);
        if (paths.length === 1)
          return new Promise<Response>((resolve) => {
            release = resolve;
          });
        return json(liveTournament('a', 4, 'completed'));
      }),
    );
    show();
    await screen.findByRole('heading', { name: 'Opening the tournament…' }, { timeout: 5000 });
    summary = liveTournament('a', 3);
    await pollOnce();
    summary = liveTournament('a', 4, 'completed');
    await pollOnce();
    await act(async () => {
      release(await json(liveTournament('a', 2)));
    });
    // Several progress updates produce exactly one follow-up request, not one
    // request per observed summary.
    await waitFor(() => expect(detailPaths(paths)).toHaveLength(2), { timeout: 5000 });
    await screen.findByText('4 of 4 games saved', undefined, { timeout: 5000 });
    expect(pollingHandlers).toHaveLength(0);
    expect(detailPaths(paths)).toHaveLength(2);
  }, 20000);

  it('aborts obsolete detail requests when switching tournaments', async () => {
    const signals = new Map<string, AbortSignal>();
    const held: Record<string, (response: Response) => void> = {};
    const error = vi.fn();
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string, options?: RequestInit) => {
        if (path === '/api/tournaments?summary=true')
          return json([saved('a'), saved('b'), saved('c')]);
        signals.set(path, options?.signal as AbortSignal);
        return new Promise<Response>((resolve) => {
          held[path] = resolve;
        });
      }),
    );
    render(<Tournaments rulesets={rulesets} profiles={profiles} models={[]} onError={error} />);
    await waitFor(() => expect(signals.has('/api/tournaments/a')).toBe(true), { timeout: 5000 });
    fireEvent.click(screen.getByRole('button', { name: 'View tournament Challenge b' }));
    await waitFor(() => expect(signals.has('/api/tournaments/b')).toBe(true), { timeout: 5000 });
    fireEvent.click(screen.getByRole('button', { name: 'View tournament Challenge c' }));
    await waitFor(() => expect(signals.has('/api/tournaments/c')).toBe(true), { timeout: 5000 });
    // Abandoned tournaments stop transferring their history.
    expect(signals.get('/api/tournaments/a')?.aborted).toBe(true);
    expect(signals.get('/api/tournaments/b')?.aborted).toBe(true);
    expect(signals.get('/api/tournaments/c')?.aborted).toBe(false);
    // Late responses for the abandoned tournaments cannot replace the selection.
    await act(async () => {
      held['/api/tournaments/a'](await json(saved('a')));
    });
    await act(async () => {
      held['/api/tournaments/b'](await json(saved('b')));
    });
    await act(async () => {
      held['/api/tournaments/c'](await json(saved('c')));
    });
    await screen.findByRole('heading', { name: 'Challenge c' }, { timeout: 5000 });
    expect(screen.queryByRole('heading', { name: 'Challenge a' })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Challenge b' })).not.toBeInTheDocument();
    expect(error).not.toHaveBeenCalled();
  }, 20000);

  it('aborts pending detail requests on unmount', async () => {
    const signals = new Map<string, AbortSignal>();
    const error = vi.fn();
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string, options?: RequestInit) => {
        if (path === '/api/tournaments?summary=true') return json([saved('a')]);
        signals.set(path, options?.signal as AbortSignal);
        return new Promise<Response>(() => {});
      }),
    );
    const { unmount } = render(
      <Tournaments rulesets={rulesets} profiles={profiles} models={[]} onError={error} />,
    );
    await waitFor(() => expect(signals.has('/api/tournaments/a')).toBe(true), { timeout: 5000 });
    unmount();
    expect(signals.get('/api/tournaments/a')?.aborted).toBe(true);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(error).not.toHaveBeenCalled();
  }, 20000);

  it('does not request details again while the summary is unchanged', async () => {
    const paths: string[] = [];
    let summary = liveTournament('a', 2);
    capturePolling();
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => {
        if (path === '/api/tournaments?summary=true') return json([summary]);
        paths.push(path);
        return json(summary);
      }),
    );
    show();
    await screen.findByRole('heading', { name: 'Challenge a' }, { timeout: 5000 });
    expect(detailPaths(paths)).toHaveLength(1);
    await pollOnce();
    await pollOnce();
    await pollOnce();
    expect(detailPaths(paths)).toHaveLength(1);
    // Live progress still refreshes the selected record when it changes.
    summary = liveTournament('a', 3);
    await pollOnce();
    await screen.findByText('3 of 4 games saved', undefined, { timeout: 5000 });
    expect(detailPaths(paths)).toHaveLength(2);
  }, 20000);
});

describe('tournament detail recovery', () => {
  /**
   * A fetcher whose first detail response succeeds, whose next `failures`
   * responses fail, and whose later responses return the settled record.
   */
  const transientFetcher = (
    summary: () => Tournament,
    first: Tournament,
    settled: Tournament,
    failures: number,
  ) => {
    let detailCalls = 0;
    const fetcher = vi.fn((path: string) => {
      if (path === '/api/tournaments?summary=true') return json([summary()]);
      detailCalls += 1;
      if (detailCalls === 1) return json(first);
      if (detailCalls <= 1 + failures) return Promise.reject(new Error('Network error'));
      return json(settled);
    });
    return { fetcher, detailCalls: () => detailCalls };
  };

  it('recovers the completed details after a transient failure', async () => {
    let summary = liveTournament('a', 2);
    const error = vi.fn();
    capturePolling();
    captureRetries();
    const { fetcher, detailCalls } = transientFetcher(
      () => summary,
      liveTournament('a', 2),
      liveTournament('a', 4, 'completed'),
      1,
    );
    vi.stubGlobal('fetch', fetcher);
    render(<Tournaments rulesets={rulesets} profiles={profiles} models={[]} onError={error} />);
    await screen.findByRole('heading', { name: 'Challenge a' }, { timeout: 5000 });
    summary = liveTournament('a', 4, 'completed');
    await pollOnce();
    // The failed refresh is reported once and stays recoverable.
    await screen.findByRole('alert', undefined, { timeout: 5000 });
    expect(error).toHaveBeenCalledTimes(1);
    expect(error).toHaveBeenCalledWith('Network error');
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
    await flushRetry();
    await screen.findByText('4 of 4 games saved', undefined, { timeout: 5000 });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(retryTimers.size).toBe(0);
    expect(detailCalls()).toBe(3);
  }, 20000);

  it('recovers the failed details after a transient failure', async () => {
    const failure = 'The tournament worker stopped. Completed games have been kept.';
    let summary = liveTournament('a', 2);
    capturePolling();
    captureRetries();
    const { fetcher } = transientFetcher(
      () => summary,
      liveTournament('a', 2),
      liveTournament('a', 2, 'failed', failure),
      1,
    );
    vi.stubGlobal('fetch', fetcher);
    show();
    await screen.findByRole('heading', { name: 'Challenge a' }, { timeout: 5000 });
    summary = liveTournament('a', 2, 'failed', failure);
    await pollOnce();
    await screen.findByRole('alert', undefined, { timeout: 5000 });
    await flushRetry();
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(failure), {
      timeout: 5000,
    });
    expect(
      screen.getByText('Interrupted', { selector: '.tournament-detail .tournament-status' }),
    ).toBeInTheDocument();
    expect(retryTimers.size).toBe(0);
  }, 20000);

  it('recovers the cancelled details and keeps the retained results', async () => {
    let summary = liveTournament('a', 2);
    capturePolling();
    captureRetries();
    const { fetcher } = transientFetcher(
      () => summary,
      liveTournament('a', 2),
      liveTournament('a', 3, 'cancelled'),
      1,
    );
    vi.stubGlobal('fetch', fetcher);
    show();
    await screen.findByRole('heading', { name: 'Challenge a' }, { timeout: 5000 });
    summary = liveTournament('a', 3, 'cancelled');
    await pollOnce();
    await screen.findByRole('alert', undefined, { timeout: 5000 });
    await flushRetry();
    await screen.findByText('3 of 4 games saved', undefined, { timeout: 5000 });
    expect(
      screen.getByText('Stopped', { selector: '.tournament-detail .tournament-status' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Match log' }));
    expect(screen.getByText('Raven wins')).toBeVisible();
  }, 20000);

  it('stops after a bounded number of retries and offers a manual retry', async () => {
    let summary = liveTournament('a', 2);
    let failing = false;
    let detailCalls = 0;
    const error = vi.fn();
    capturePolling();
    captureRetries();
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => {
        if (path === '/api/tournaments?summary=true') return json([summary]);
        detailCalls += 1;
        if (detailCalls === 1) return json(liveTournament('a', 2));
        if (failing) return Promise.reject(new Error('Network error'));
        return json(liveTournament('a', 4, 'completed'));
      }),
    );
    render(<Tournaments rulesets={rulesets} profiles={profiles} models={[]} onError={error} />);
    await screen.findByRole('heading', { name: 'Challenge a' }, { timeout: 5000 });
    failing = true;
    summary = liveTournament('a', 4, 'completed');
    await pollOnce();
    await screen.findByRole('alert', undefined, { timeout: 5000 });
    for (let attempt = 0; attempt < DETAIL_RETRY_DELAYS.length; attempt += 1) await flushRetry();
    // One notification and one retry per scheduled delay, and no more.
    expect(error).toHaveBeenCalledTimes(1);
    expect(detailCalls).toBe(2 + DETAIL_RETRY_DELAYS.length);
    expect(retryTimers.size).toBe(0);
    expect(screen.getByRole('alert')).toBeInTheDocument();
    // The manual retry recovers as soon as the server answers again.
    failing = false;
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await screen.findByText('4 of 4 games saved', undefined, { timeout: 5000 });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  }, 20000);

  it('bounds detail requests while a running tournament keeps polling', async () => {
    let summary = liveTournament('a', 2);
    let detailCalls = 0;
    let failing = true;
    capturePolling();
    captureRetries();
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => {
        if (path === '/api/tournaments?summary=true') return json([summary]);
        detailCalls += 1;
        if (detailCalls === 1) return json(liveTournament('a', 2));
        if (failing) return Promise.reject(new Error('Network error'));
        return json(liveTournament('a', 4, 'completed'));
      }),
    );
    show();
    await screen.findByRole('heading', { name: 'Challenge a' }, { timeout: 5000 });
    summary = liveTournament('a', 3);
    await pollOnce();
    await screen.findByRole('alert', undefined, { timeout: 5000 });
    expect(detailCalls).toBe(2);
    expect(retryTimers.size).toBe(1);
    // Polling continues, but a pending retry is never bypassed by a poll.
    for (let poll = 0; poll < 6; poll += 1) await pollOnce();
    expect(detailCalls).toBe(2);
    expect(retryTimers.size).toBe(1);
    // The scheduled retries fire on their own backoff and then stop.
    for (let attempt = 0; attempt < DETAIL_RETRY_DELAYS.length; attempt += 1) await flushRetry();
    expect(detailCalls).toBe(2 + DETAIL_RETRY_DELAYS.length);
    // An unchanged marker cannot start more automatic requests, and the manual
    // recovery stays available.
    for (let poll = 0; poll < 5; poll += 1) await pollOnce();
    expect(detailCalls).toBe(2 + DETAIL_RETRY_DELAYS.length);
    expect(retryTimers.size).toBe(0);
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
    // The manual retry still recovers the newest details.
    failing = false;
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
      await new Promise((resolve) => setTimeout(resolve, 0));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await screen.findByText('4 of 4 games saved', undefined, { timeout: 5000 });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(retryTimers.size).toBe(0);
  }, 20000);

  it('resumes synchronization with a fresh budget for a newer marker', async () => {
    let summary = liveTournament('a', 2);
    let detailCalls = 0;
    let failing = true;
    capturePolling();
    captureRetries();
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => {
        if (path === '/api/tournaments?summary=true') return json([summary]);
        detailCalls += 1;
        if (detailCalls === 1) return json(liveTournament('a', 2));
        if (failing) return Promise.reject(new Error('Network error'));
        return json(liveTournament('a', 4));
      }),
    );
    show();
    await screen.findByRole('heading', { name: 'Challenge a' }, { timeout: 5000 });
    summary = liveTournament('a', 3);
    await pollOnce();
    await screen.findByRole('alert', undefined, { timeout: 5000 });
    for (let attempt = 0; attempt < DETAIL_RETRY_DELAYS.length; attempt += 1) await flushRetry();
    expect(detailCalls).toBe(2 + DETAIL_RETRY_DELAYS.length);
    for (let poll = 0; poll < 3; poll += 1) await pollOnce();
    expect(detailCalls).toBe(2 + DETAIL_RETRY_DELAYS.length);
    // A genuinely newer marker gets its own immediate attempt and a fresh budget.
    failing = false;
    summary = liveTournament('a', 4);
    await pollOnce();
    expect(detailCalls).toBe(3 + DETAIL_RETRY_DELAYS.length);
    await screen.findByText('4 of 4 games saved', undefined, { timeout: 5000 });
    expect(retryTimers.size).toBe(0);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  }, 20000);

  it('does not carry a pending retry into a newly selected tournament', async () => {
    let summary = liveTournament('a', 2);
    const error = vi.fn();
    capturePolling();
    captureRetries();
    let failDetail = false;
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => {
        if (path === '/api/tournaments?summary=true') return json([summary, saved('b')]);
        if (path.endsWith('/b')) return json(saved('b'));
        if (failDetail) return Promise.reject(new Error('Network error'));
        return json(liveTournament('a', 2));
      }),
    );
    render(<Tournaments rulesets={rulesets} profiles={profiles} models={[]} onError={error} />);
    await screen.findByRole('heading', { name: 'Challenge a' }, { timeout: 5000 });
    failDetail = true;
    summary = liveTournament('a', 3);
    await pollOnce();
    await screen.findByRole('alert', undefined, { timeout: 5000 });
    expect(retryTimers.size).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: 'View tournament Challenge b' }));
    await screen.findByRole('heading', { name: 'Challenge b' }, { timeout: 5000 });
    // The obsolete retry is cancelled and cannot overwrite the new selection.
    expect(retryTimers.size).toBe(0);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.getByRole('heading', { name: 'Challenge b' })).toBeInTheDocument();
  }, 20000);

  it('cancels a pending retry when the screen unmounts', async () => {
    let summary = liveTournament('a', 2);
    const error = vi.fn();
    capturePolling();
    captureRetries();
    let detailCalls = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) => {
        if (path === '/api/tournaments?summary=true') return json([summary]);
        detailCalls += 1;
        if (detailCalls === 1) return json(liveTournament('a', 2));
        return Promise.reject(new Error('Network error'));
      }),
    );
    const { unmount } = render(
      <Tournaments rulesets={rulesets} profiles={profiles} models={[]} onError={error} />,
    );
    await screen.findByRole('heading', { name: 'Challenge a' }, { timeout: 5000 });
    summary = liveTournament('a', 3);
    await pollOnce();
    await screen.findByRole('alert', undefined, { timeout: 5000 });
    expect(retryTimers.size).toBe(1);
    unmount();
    expect(retryTimers.size).toBe(0);
    const calls = detailCalls;
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(detailCalls).toBe(calls);
  }, 20000);

  it('does not request anything further once the terminal record is shown', async () => {
    let summary = liveTournament('a', 2);
    capturePolling();
    captureRetries();
    const { fetcher, detailCalls } = transientFetcher(
      () => summary,
      liveTournament('a', 2),
      liveTournament('a', 4, 'completed'),
      1,
    );
    vi.stubGlobal('fetch', fetcher);
    show();
    await screen.findByRole('heading', { name: 'Challenge a' }, { timeout: 5000 });
    summary = liveTournament('a', 4, 'completed');
    await pollOnce();
    await screen.findByRole('alert', undefined, { timeout: 5000 });
    await flushRetry();
    await screen.findByText('4 of 4 games saved', undefined, { timeout: 5000 });
    const settled = detailCalls();
    // Nothing is running any more, so polling has stopped and no retry remains.
    expect(pollingHandlers).toHaveLength(0);
    expect(retryTimers.size).toBe(0);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(detailCalls()).toBe(settled);
  }, 20000);
});

describe('fixed-role outcome chart', () => {
  const fixedRoleTournament = (
    outcomes: ('attackers' | 'defenders' | 'draw' | null)[],
  ): Tournament => {
    const tournament = saved('fixed');
    tournament.mode = 'fixed_roles';
    tournament.config.mode = 'fixed_roles';
    tournament.result = {
      scores: [0, 0],
      standings: tournament.config.players.map((_, player) => ({
        player,
        wins: 0,
        losses: 0,
        draws: 0,
        truncated: 0,
        games: outcomes.length,
        score: 0,
      })),
      by_ruleset: { brandubh: { wins: [0, 0], draws: 0, truncated: 0, games: outcomes.length } },
      games: outcomes.map((winner) => ({
        ruleset: 'brandubh',
        winner,
        outcome: winner ? ('completed' as const) : ('truncated' as const),
        reason: winner ? 'king_captured' : 'experiment_ply_limit',
        plies: 12,
        moves: [],
        attacker_player: 0,
        defender_player: 1,
        average_decision_time: 0.01,
        total_decision_time: 0.12,
      })),
      truncated: outcomes.filter((winner) => winner === null).length,
      average_game_length: 12,
      average_decision_time: 0.01,
    };
    return tournament;
  };
  const showChart = async (outcomes: ('attackers' | 'defenders' | 'draw' | null)[]) => {
    const tournament = fixedRoleTournament(outcomes);
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) =>
        json(path === '/api/tournaments?summary=true' ? [tournament] : tournament),
      ),
    );
    show();
    await screen.findByRole('heading', { name: 'Challenge fixed' }, { timeout: 5000 });
  };

  it('draws a segment only for outcome categories that happened', async () => {
    const cases: [('attackers' | 'defenders' | 'draw' | null)[], number][] = [
      [['attackers', 'attackers', 'attackers'], 1],
      [['defenders', 'defenders'], 1],
      [['draw'], 1],
      [['attackers', 'draw', 'attackers'], 2],
      [[null, 'defenders'], 2],
      [['attackers', 'defenders', 'draw', null], 4],
    ];
    for (const [outcomes, expected] of cases) {
      await showChart(outcomes);
      expect(document.querySelectorAll('.tournament-outcome-bar > span')).toHaveLength(expected);
      // The legend always documents every outcome, including the empty ones.
      expect(document.querySelectorAll('.tournament-outcome-legend li')).toHaveLength(4);
      cleanup();
    }
  }, 20000);

  it('describes the same outcome counts as the chart', async () => {
    await showChart(['attackers', 'attackers', 'defenders', 'draw', null]);
    expect(
      screen.getByRole('img', {
        name: '5 games: 2 attacker wins, 1 defender wins, 1 draws, 1 unfinished.',
      }),
    ).toBeInTheDocument();
    expect(document.querySelectorAll('.tournament-outcome-bar > span')).toHaveLength(4);
    const legend = [...document.querySelectorAll('.tournament-outcome-legend li')].map((item) =>
      item.textContent?.trim(),
    );
    expect(legend).toEqual(['Attacker wins2', 'Defender wins1', 'Rule draws1', 'Unfinished1']);
  });

  it('shows a placeholder instead of an empty chart', async () => {
    await showChart([]);
    expect(document.querySelector('.tournament-outcome-bar')).toBeNull();
    expect(screen.getByText('No completed games yet.')).toBeInTheDocument();
  });
});

describe('tournament benchmarking', () => {
  it.each(['running', 'completed'] as const)(
    'shows role denominators and unfinished counts for a %s tournament',
    async (status) => {
      const tournament = saved('benchmark');
      tournament.status = status;
      tournament.config.randomize_start_positions = true;
      const attack = { games: 5, wins: 1, losses: 1, draws: 1, truncated: 2 };
      const defend = { games: 4, wins: 3, losses: 0, draws: 1, truncated: 0 };
      tournament.result = {
        standings: [
          {
            player: 0,
            score: 5,
            games: 9,
            wins: 4,
            losses: 1,
            draws: 2,
            truncated: 2,
            roles: { attackers: attack, defenders: defend },
            diagnostics: { decisions: 9, mean_simulations: 63.5 },
          },
        ],
        scores: [5, 0],
        games: [],
        by_ruleset: {},
        truncated: 2,
        average_game_length: 2,
        average_decision_time: 0.01,
      };
      vi.stubGlobal(
        'fetch',
        vi.fn((path: string) =>
          json(path === '/api/tournaments?summary=true' ? [tournament] : tournament),
        ),
      );
      show();
      await screen.findByRole('heading', { name: 'Challenge benchmark' });
      const card = within(screen.getByRole('heading', { name: 'Raven' }).closest('article')!);
      expect(card.getByRole('region', { name: 'Overall' })).toHaveTextContent('57.1% · n=7');
      expect(card.getByRole('region', { name: 'Overall' })).toHaveTextContent('71.4% · n=7');
      expect(card.getByRole('region', { name: 'As attackers' })).toHaveTextContent('33.3% · n=3');
      expect(card.getByRole('region', { name: 'As attackers' })).toHaveTextContent('50.0% · n=3');
      expect(card.getByRole('region', { name: 'As attackers' })).toHaveTextContent('Unfinished2');
      expect(card.getByRole('region', { name: 'As defenders' })).toHaveTextContent('75.0% · n=4');
      expect(card.getByRole('region', { name: 'As defenders' })).toHaveTextContent('87.5% · n=4');
      expect(card.getByText('Mean MCTS simulations')).toBeInTheDocument();
      expect(screen.getByText('Randomized legal openings')).toBeInTheDocument();
    },
  );

  it('keeps zero-completed and unavailable roles distinct from a 0% result', async () => {
    const tournament = saved('empty-roles');
    tournament.config.mode = 'fixed_roles';
    tournament.result = {
      scores: [0, 0],
      games: [],
      by_ruleset: {},
      truncated: 1,
      average_game_length: 1,
      average_decision_time: 0.01,
      standings: [
        {
          player: 0,
          score: 0,
          games: 1,
          wins: 0,
          losses: 0,
          draws: 0,
          truncated: 1,
          roles: {
            attackers: { games: 1, wins: 0, losses: 0, draws: 0, truncated: 1 },
            defenders: { games: 0, wins: 0, losses: 0, draws: 0, truncated: 0 },
          },
        },
      ],
    };
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) =>
        json(path === '/api/tournaments?summary=true' ? [tournament] : tournament),
      ),
    );
    show();
    await screen.findByRole('heading', { name: 'Challenge empty-roles' });
    const card = within(screen.getByRole('heading', { name: 'Raven' }).closest('article')!);
    expect(card.getByRole('region', { name: 'As attackers' })).toHaveTextContent('— · n=0');
    expect(card.getByRole('region', { name: 'As defenders' })).toHaveTextContent('— · n=0');
    expect(card.queryByText(/0\.0%/)).not.toBeInTheDocument();
  });

  it('submits the same model with independent wrappers, budgets and randomized openings', async () => {
    let submitted: Record<string, unknown> | undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string, options?: RequestInit) => {
        if (options?.method === 'POST') {
          submitted = JSON.parse(String(options.body));
          return json(saved('created'));
        }
        return json(path === '/api/tournaments?summary=true' ? [] : saved('created'));
      }),
    );
    show([{ id: 'same', name: 'Same weights', ruleset: 'brandubh' }]);
    const button = screen.getAllByRole('button', { name: 'Fixed-role comparison' })[0];
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    const form = within(screen.getByRole('dialog', { name: 'Fixed-role comparison' }));
    fireEvent.change(form.getByLabelText('Attackers search iterations'), {
      target: { value: '256' },
    });
    fireEvent.change(form.getByLabelText('Attackers time limit (seconds)'), {
      target: { value: '2' },
    });
    fireEvent.change(form.getByLabelText('Defenders search wrapper'), {
      target: { value: 'alpha_beta' },
    });
    fireEvent.change(form.getByLabelText('Defenders search depth'), { target: { value: '3' } });
    fireEvent.click(form.getByLabelText('Randomize starting positions'));
    fireEvent.click(form.getByRole('button', { name: 'Start tournament' }));
    await waitFor(() => expect(submitted).toBeDefined());
    expect(submitted).toMatchObject({
      randomize_start_positions: true,
      players: [
        { config: { model_id: 'same', iterations: 256, time_limit: 2 } },
        { config: { model_id: 'same', search_wrapper: 'alpha_beta', depth: 3, time_limit: 10 } },
      ],
    });
  });
});
