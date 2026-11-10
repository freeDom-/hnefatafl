import {
  act,
  cleanup,
  render,
  renderHook,
  screen,
  waitFor,
  fireEvent,
} from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import AgentAnalysis, { useGameAnalysis } from './AgentAnalysis';
import { advisorOptions, AnalyticsSettingsPanel } from './AdvisorSelect';
import { defaultAnalyticsSettings } from './analysisTypes';
import type { AIProfile, Game, Model } from './types';
import type { AnalyticsSettings, PositionAnalysis } from './analysisTypes';
import { MobilePlayContext } from './mobilePlay';
import { setLanguage } from './i18n';

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
  // Winning chances and best-move hints are requested only with an advisor.
  analytics: {
    ...defaultAnalyticsSettings,
    advisor: { name: 'Advisor', profile_id: 'baseline', config: { algorithm: 'baseline' } },
  },
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
  setLanguage('en', false);
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it('lets mobile readers switch agents and recovers when the selected report disappears', () => {
  const compact = (value: PositionAnalysis) => (
    <MobilePlayContext.Provider value>
      <AgentAnalysis game={game} report={value} pending={false} failed={false} retry={vi.fn()} />
    </MobilePlayContext.Provider>
  );
  const view = render(compact(report));
  expect(screen.getAllByRole('meter')).toHaveLength(1);
  fireEvent.change(screen.getByRole('combobox', { name: 'Agent evaluations' }), {
    target: { value: 'defenders' },
  });
  expect(screen.getByRole('meter', { name: 'Munin estimate for Attackers' })).toHaveAttribute(
    'aria-valuenow',
    '30',
  );
  view.rerender(compact({ ...report, evaluations: [report.evaluations[0]] }));
  expect(screen.getByRole('meter', { name: 'Hugin estimate for Attackers' })).toHaveAttribute(
    'aria-valuenow',
    '70',
  );
  expect(screen.getByText(/not calibrated winning probabilities/)).toBeInTheDocument();
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
  const saved: PositionAnalysis = { ...report, position_key: 'saved-position', status: 'complete' };
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

it('marks live analysis as non-historical so the exact-cursor contract holds', async () => {
  const fetcher = vi.fn(async (_url: string, _opts?: RequestInit) => response(report));
  vi.stubGlobal('fetch', fetcher);
  const view = renderHook(() => useGameAnalysis(game, true));
  await waitFor(() => expect(view.result.current.report).toEqual(report));
  const call = fetcher.mock.calls.find(
    ([url, opts]) => String(url).includes('/analysis') && opts?.method === 'POST',
  );
  expect(call).toBeTruthy();
  expect(JSON.parse(String(call![1]?.body))).toMatchObject({ historical: false });
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

it('distinguishes configured alpha-beta depth from the depth completed within the time limit', () => {
  const limited: PositionAnalysis = {
    ...report,
    evaluations: [
      {
        ...report.evaluations[0],
        config: { algorithm: 'alpha_beta', depth: 4 },
        completed_depth: 2,
      },
      {
        ...report.evaluations[1],
        config: { algorithm: 'alpha_beta', depth: 3 },
        completed_depth: 3,
      },
    ],
  };
  render(
    <AgentAnalysis game={game} report={limited} pending={false} failed={false} retry={vi.fn()} />,
  );
  expect(screen.getByText('Heuristic estimate · depth 2 / 4')).toBeInTheDocument();
  expect(screen.getByText('Heuristic estimate · depth 3 / 3')).toBeInTheDocument();
  expect(
    screen.getAllByText('The time limit was reached before the configured depth finished.'),
  ).toHaveLength(1);
});

it('labels newly searched neural values and retains the original label for recorded root values', () => {
  const searched: PositionAnalysis = {
    ...report,
    evaluations: [
      { ...report.evaluations[0], source: 'neural_search', simulations: 123 },
      { ...report.evaluations[1], source: 'neural_value', simulations: 40 },
    ],
  };
  render(
    <AgentAnalysis game={game} report={searched} pending={false} failed={false} retry={vi.fn()} />,
  );
  expect(screen.getByText('Neural search estimate · 123 simulations')).toBeInTheDocument();
  expect(screen.getByText('Neural value head · 40 simulations')).toBeInTheDocument();
});

it('keeps partial estimates visible while background search improves them', async () => {
  let calls = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      response({
        ...report,
        status: ++calls === 1 ? 'pending' : 'complete',
        evaluations: [{ ...report.evaluations[0], completed_depth: calls }],
      }),
    ),
  );
  const view = renderHook(() => useGameAnalysis(game, true));
  await waitFor(() => expect(view.result.current.report?.evaluations[0].completed_depth).toBe(1));
  expect(view.result.current.pending).toBe(true);
  await waitFor(() => expect(view.result.current.report?.evaluations[0].completed_depth).toBe(2));
  expect(view.result.current.pending).toBe(false);
  expect(calls).toBe(2);
});

it('shows chances and the advisor move without a visibility checkbox', () => {
  const hint = { agent_id: 'advisor' as const, move: { source: 0, target: 1 } };
  render(
    <AgentAnalysis
      game={game}
      report={{ ...report, hint }}
      pending={false}
      failed={false}
      retry={vi.fn()}
    />,
  );
  expect(screen.queryByRole('checkbox')).toBeNull();
  expect(screen.queryByText('Show winning chances')).toBeNull();
  expect(screen.getByRole('meter', { name: 'Hugin estimate for Attackers' })).toBeInTheDocument();
  expect(screen.getByText('Suggested move: a7 → b7')).toBeInTheDocument();
});

it('renders progressively available estimates without waiting for the final search', () => {
  render(<AgentAnalysis game={game} report={report} pending failed={false} retry={vi.fn()} />);
  expect(screen.getByText('Evaluating this position…')).toBeInTheDocument();
  expect(screen.getByRole('meter', { name: 'Hugin estimate for Attackers' })).toHaveAttribute(
    'aria-valuenow',
    '70',
  );
});

it('configures a custom advisor independently of game creation and hides custom fields for named agents', () => {
  const onChange = vi.fn(async (_next: typeof defaultAnalyticsSettings) => undefined);
  const profiles = [
    {
      id: 'alpha_beta',
      name: 'Jarl',
      algorithm: 'alpha_beta',
      difficulty: 'strong',
      parameters: { depth: 5 },
      supported_rulesets: ['brandubh'],
    },
  ];
  const view = render(
    <AnalyticsSettingsPanel
      game={game}
      profiles={profiles}
      models={[]}
      settings={defaultAnalyticsSettings}
      onChange={onChange}
    />,
  );
  expect(screen.getByRole('combobox', { name: 'Move advisor' })).toHaveValue('');
  expect(screen.getByRole('spinbutton', { name: 'Maximum search time (seconds)' })).toHaveValue(5);
  fireEvent.change(screen.getByRole('combobox', { name: 'Move advisor' }), {
    target: { value: 'custom' },
  });
  const customSettings = onChange.mock.calls[0][0];
  expect(customSettings.advisor?.config).toMatchObject({ algorithm: 'alpha_beta', depth: 5 });
  view.rerender(
    <AnalyticsSettingsPanel
      game={game}
      profiles={profiles}
      models={[]}
      settings={customSettings}
      onChange={onChange}
    />,
  );
  expect(screen.getByRole('spinbutton', { name: 'Search depth' })).toHaveValue(5);
  fireEvent.change(screen.getByRole('combobox', { name: 'Move advisor' }), {
    target: { value: 'profile:alpha_beta' },
  });
  const namedSettings = onChange.mock.calls[1][0];
  view.rerender(
    <AnalyticsSettingsPanel
      game={game}
      profiles={profiles}
      models={[]}
      settings={namedSettings}
      onChange={onChange}
    />,
  );
  expect(screen.queryByRole('spinbutton', { name: 'Search depth' })).not.toBeInTheDocument();
});

it('serializes analytics writes and ignores earlier settings responses', async () => {
  let finishFirst!: (response: Response) => void;
  let writes = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn((_url, options) => {
      if (options?.method !== 'PUT')
        return Promise.resolve(response({ ...report, advisor_revision: writes }));
      writes += 1;
      if (writes === 1)
        return new Promise<Response>((resolve) => {
          finishFirst = resolve;
        });
      return Promise.resolve(response({ ...defaultAnalyticsSettings, time_limit: 9, revision: 2 }));
    }),
  );
  const view = renderHook(() => useGameAnalysis(game, true));
  let first!: Promise<void>;
  let second!: Promise<void>;
  act(() => {
    first = view.result.current.updateSettings({ ...defaultAnalyticsSettings, time_limit: 7 });
    second = view.result.current.updateSettings({ ...defaultAnalyticsSettings, time_limit: 9 });
  });
  await waitFor(() => expect(writes).toBe(1));
  expect(view.result.current.settings.time_limit).toBe(9);
  await act(async () => {
    finishFirst(response({ ...defaultAnalyticsSettings, time_limit: 7, revision: 1 }));
    await Promise.all([first, second]);
  });
  expect(writes).toBe(2);
  expect(view.result.current.settings).toMatchObject({ time_limit: 9, revision: 2 });
});

it('replaces advisor predictions when settings change and rejects an earlier search result', async () => {
  let finishOld!: (response: Response) => void;
  const advisor = {
    name: 'Jarl',
    profile_id: 'alpha_beta',
    config: { algorithm: 'alpha_beta', depth: 5 },
  };
  let searches = 0;
  const nextHint = { agent_id: 'advisor' as const, move: { source: 5, target: 6 } };
  vi.stubGlobal(
    'fetch',
    vi.fn((_url, options) => {
      if (options?.method === 'PUT')
        return Promise.resolve(
          response({
            ...defaultAnalyticsSettings,
            advisor,
            revision: 1,
          }),
        );
      searches += 1;
      if (searches === 1)
        return new Promise<Response>((resolve) => {
          finishOld = resolve;
        });
      return Promise.resolve(
        response({ ...report, hint: nextHint, advisor_revision: 1, status: 'complete' }),
      );
    }),
  );
  const view = renderHook(() => useGameAnalysis(game, true));
  await waitFor(() => expect(searches).toBe(1));
  await act(() => view.result.current.updateSettings({ ...defaultAnalyticsSettings, advisor }));
  await waitFor(() => expect(view.result.current.report?.hint).toEqual(nextHint));
  await act(async () =>
    finishOld(
      response({ ...report, hint: { agent_id: 'advisor', move: { source: 1, target: 2 } } }),
    ),
  );
  expect(view.result.current.report?.hint).toEqual(nextHint);
});

it('does not let a delayed settings read overwrite an edit made while loading', async () => {
  let finishRead!: (response: Response) => void;
  const fetcher = vi.fn((_url, options) => {
    if (options?.method === 'GET')
      return new Promise<Response>((resolve) => {
        finishRead = resolve;
      });
    if (options?.method === 'PUT')
      return Promise.resolve(response({ ...defaultAnalyticsSettings, time_limit: 9, revision: 1 }));
    return Promise.resolve(response({ ...report, advisor_revision: 1, status: 'complete' }));
  });
  vi.stubGlobal('fetch', fetcher);
  const view = renderHook(() => useGameAnalysis({ ...game, analytics: undefined }, true));
  expect(view.result.current.settingsPending).toBe(true);
  await act(() =>
    view.result.current.updateSettings({ ...defaultAnalyticsSettings, time_limit: 9 }),
  );
  expect(view.result.current.settings).toMatchObject({ time_limit: 9, revision: 1 });
  await act(async () => finishRead(response(defaultAnalyticsSettings)));
  expect(view.result.current.settings).toMatchObject({ time_limit: 9, revision: 1 });
});

it('recovers server settings after a rejected edit instead of analyzing the unsaved advisor', async () => {
  const invalidAdvisor = {
    name: 'Unavailable model',
    config: { algorithm: 'neural', model_id: 'missing' },
  };
  const fetcher = vi.fn((_url, options) => {
    if (options?.method === 'PUT') return Promise.reject(new Error('This model is unavailable.'));
    if (options?.method === 'GET') return Promise.resolve(response(defaultAnalyticsSettings));
    return Promise.resolve(response({ ...report, status: 'complete' }));
  });
  vi.stubGlobal('fetch', fetcher);
  const view = renderHook(() => useGameAnalysis(game, true));
  await act(() =>
    view.result.current.updateSettings({
      ...defaultAnalyticsSettings,
      advisor: invalidAdvisor,
      revision: 8,
    }),
  );
  expect(view.result.current.settings).toEqual(defaultAnalyticsSettings);
  expect(view.result.current.settingsError).toBe('This model is unavailable.');
  expect(view.result.current.settingsPending).toBe(false);
  await waitFor(() => expect(view.result.current.pending).toBe(false));
});

it('stops background polling if saving and reloading settings both fail', async () => {
  let searches = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn((_url, options) => {
      if (options?.method === 'PUT' || options?.method === 'GET')
        return Promise.reject(new Error('offline'));
      searches += 1;
      return Promise.resolve(response({ ...report, status: 'complete' }));
    }),
  );
  const view = renderHook(() => useGameAnalysis(game, true));
  await waitFor(() => expect(view.result.current.pending).toBe(false));
  const before = searches;
  await act(() =>
    view.result.current.updateSettings({ ...defaultAnalyticsSettings, time_limit: 9, revision: 8 }),
  );
  expect(view.result.current.settings).toEqual(game.analytics);
  expect(view.result.current.settingsError).toBe('offline');
  expect(view.result.current.pending).toBe(false);
  expect(searches).toBe(before);
});

it('disables custom neural advisors until a compatible model exists', () => {
  const onChange = vi.fn(async (_next: typeof defaultAnalyticsSettings) => undefined);
  render(
    <AnalyticsSettingsPanel
      game={game}
      profiles={[]}
      models={[]}
      settings={{
        ...defaultAnalyticsSettings,
        advisor: { name: 'Custom move advisor', config: { algorithm: 'alpha_beta', depth: 5 } },
      }}
      onChange={onChange}
    />,
  );
  expect(screen.getByRole('option', { name: 'Neural network' })).toBeDisabled();
  fireEvent.change(screen.getByRole('combobox', { name: 'Algorithm' }), {
    target: { value: 'neural' },
  });
  expect(onChange).not.toHaveBeenCalled();
});

it('selects the evaluator for a custom alpha-beta move advisor', () => {
  const onChange = vi.fn(async (_next: typeof defaultAnalyticsSettings) => undefined);
  const profiles = [
    {
      id: 'alpha_beta',
      name: 'Jarl',
      algorithm: 'alpha_beta',
      difficulty: 'strong',
      parameters: { depth: 5 },
      supported_rulesets: ['brandubh'],
    },
  ];
  const view = render(
    <AnalyticsSettingsPanel
      game={game}
      profiles={profiles}
      models={[]}
      settings={defaultAnalyticsSettings}
      onChange={onChange}
    />,
  );
  fireEvent.change(screen.getByRole('combobox', { name: 'Move advisor' }), {
    target: { value: 'custom' },
  });
  const custom = onChange.mock.calls[0][0];
  view.rerender(
    <AnalyticsSettingsPanel
      game={game}
      profiles={profiles}
      models={[]}
      settings={custom}
      onChange={onChange}
    />,
  );
  const heuristic = screen.getByRole('combobox', { name: 'Move advisor heuristic' });
  expect(heuristic).toHaveValue('shield_maiden');
  fireEvent.change(heuristic, { target: { value: 'shield_bearer' } });
  expect(onChange.mock.calls.at(-1)?.[0].advisor?.config.heuristic).toBe('shield_bearer');
});

const neuralProfiles: AIProfile[] = [
  {
    id: 'neural',
    name: 'Trained model',
    algorithm: 'neural',
    difficulty: 'Model dependent',
    parameters: { iterations: 40, time_limit: 10 },
    supported_rulesets: ['brandubh'],
  },
];
const neuralModels: Model[] = [{ id: 'hugin', name: 'Hugin', ruleset: 'brandubh' }];
const neuralSettings: AnalyticsSettings = {
  ...defaultAnalyticsSettings,
  advisor: {
    name: 'Hugin',
    profile_id: 'neural',
    config: { algorithm: 'neural', model_id: 'hugin' },
  },
};

it('saves neural analytics iterations and keeps them when changing the search window or reloading', async () => {
  let saved = defaultAnalyticsSettings;
  const writes: AnalyticsSettings[] = [];
  let searches = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url, options) => {
      if (options?.method === 'PUT') {
        saved = { ...JSON.parse(String(options.body)), revision: saved.revision + 1 };
        writes.push(saved);
        return response(saved);
      }
      searches += 1;
      return response({ ...report, status: 'complete', advisor_revision: saved.revision });
    }),
  );
  function Settings() {
    const analysis = useGameAnalysis({ ...game, analytics: saved }, true);
    return (
      <AnalyticsSettingsPanel
        game={game}
        profiles={neuralProfiles}
        models={neuralModels}
        settings={analysis.settings}
        onChange={analysis.updateSettings}
      />
    );
  }
  const view = render(<Settings />);
  expect(screen.queryByRole('spinbutton', { name: 'MCTS Iterations' })).toBeNull();
  fireEvent.change(screen.getByRole('combobox', { name: 'Move advisor' }), {
    target: { value: 'model:hugin' },
  });
  const input = screen.getByRole('spinbutton', { name: 'MCTS Iterations' });
  expect(input).toHaveValue(1024);
  await waitFor(() => expect(writes.at(-1)?.advisor?.config.iterations).toBe(1024));
  for (const budget of [2048, 4096]) {
    fireEvent.change(input, { target: { value: String(budget) } });
    await waitFor(() => expect(writes.at(-1)?.advisor?.config.iterations).toBe(budget));
  }
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Maximum search time (seconds)' }), {
    target: { value: '15' },
  });
  await waitFor(() => expect(writes.at(-1)?.time_limit).toBe(15));
  expect(writes.at(-1)?.advisor?.config.iterations).toBe(4096);
  expect(input).toHaveValue(4096);
  await waitFor(() => expect(searches).toBeGreaterThan(0));
  view.unmount();
  render(<Settings />);
  expect(screen.getByRole('spinbutton', { name: 'MCTS Iterations' })).toHaveValue(4096);
});

it.each<AnalyticsSettings['advisor']>([
  null,
  { name: 'Heuristic', config: { algorithm: 'baseline' } },
  { name: 'Seer', profile_id: 'mcts', config: { algorithm: 'mcts', iterations: 5000 } },
  { name: 'Policy', config: { algorithm: 'neural', model_id: 'hugin', search_wrapper: 'none' } },
  {
    name: 'Neural alpha-beta',
    config: { algorithm: 'neural', model_id: 'hugin', search_wrapper: 'alpha_beta' },
  },
])('hides MCTS iterations for analytics without neural MCTS: %j', (advisor) => {
  render(
    <AnalyticsSettingsPanel
      game={game}
      profiles={neuralProfiles}
      models={neuralModels}
      settings={{ ...defaultAnalyticsSettings, advisor }}
      onChange={vi.fn()}
    />,
  );
  expect(screen.queryByRole('spinbutton', { name: 'MCTS Iterations' })).toBeNull();
});

it('reports invalid MCTS budgets locally without saving them and allows clearing before retyping', () => {
  const onChange = vi.fn();
  render(
    <AnalyticsSettingsPanel
      game={game}
      profiles={neuralProfiles}
      models={neuralModels}
      settings={neuralSettings}
      onChange={onChange}
    />,
  );
  const input = screen.getByRole('spinbutton', { name: 'MCTS Iterations' });
  expect(input).toHaveValue(1024);
  for (const invalid of ['', '0', '-1', '1.5', '5001']) {
    fireEvent.change(input, { target: { value: invalid } });
    expect(input).toBeInvalid();
    expect(screen.getByRole('alert')).toHaveTextContent('Enter a whole number from 1 to 5000.');
    expect(onChange).not.toHaveBeenCalled();
  }
  fireEvent.change(input, { target: { value: '4096' } });
  expect(input).toBeValid();
  expect(screen.queryByRole('alert')).toBeNull();
  fireEvent.blur(input);
  expect(onChange.mock.calls[0][0].advisor.config.iterations).toBe(4096);
});

it('gives custom neural MCTS advisors one iterations input with the analytics default', () => {
  const onChange = vi.fn();
  const view = render(
    <AnalyticsSettingsPanel
      game={game}
      profiles={neuralProfiles}
      models={neuralModels}
      settings={{
        ...defaultAnalyticsSettings,
        advisor: {
          name: 'Custom move advisor',
          config: { algorithm: 'alpha_beta', iterations: 5000 },
        },
      }}
      onChange={onChange}
    />,
  );
  fireEvent.change(screen.getByRole('combobox', { name: 'Algorithm' }), {
    target: { value: 'neural' },
  });
  const chosen = onChange.mock.calls[0][0];
  expect(chosen.advisor.config.iterations).toBe(1024);
  view.rerender(
    <AnalyticsSettingsPanel
      game={game}
      profiles={neuralProfiles}
      models={neuralModels}
      settings={chosen}
      onChange={onChange}
    />,
  );
  expect(screen.getByRole('spinbutton', { name: 'MCTS Iterations' })).toHaveValue(1024);
  expect(screen.queryByRole('spinbutton', { name: 'Search iterations' })).toBeNull();
});

it('localizes the accessible MCTS label, tooltip and validation', () => {
  setLanguage('de', false);
  render(
    <AnalyticsSettingsPanel
      game={game}
      profiles={neuralProfiles}
      models={neuralModels}
      settings={neuralSettings}
      onChange={vi.fn()}
    />,
  );
  const input = screen.getByRole('spinbutton', { name: 'MCTS-Iterationen' });
  fireEvent.click(screen.getByRole('button', { name: 'MCTS-Iterationen erklären' }));
  expect(screen.getByRole('tooltip')).toHaveTextContent(
    'Anzahl der MCTS-Simulationen pro analysierter Stellung',
  );
  fireEvent.change(input, { target: { value: '0' } });
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Gib eine ganze Zahl zwischen 1 und 5000 ein.',
  );
});

const busy = (retryAfter = '1') =>
  new Response(JSON.stringify({ detail: 'Analysis workers are busy.' }), {
    status: 503,
    headers: { 'Retry-After': retryAfter },
  });
const tick = async (ms = 0) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

it('recovers from a capacity error using Retry-After while preserving a valid partial report', async () => {
  vi.useFakeTimers();
  const partial = { ...report, status: 'pending' };
  const final = {
    ...report,
    status: 'complete',
    hint: { agent_id: 'advisor', move: { source: 2, target: 3 } },
  };
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(response(partial))
    .mockImplementationOnce(async () => busy('2'))
    .mockResolvedValue(response(final));
  vi.stubGlobal('fetch', fetcher);
  const view = renderHook(() => useGameAnalysis(game, true));
  await tick();
  expect(view.result.current.report).toEqual(partial);
  await tick(300);
  expect(view.result.current.waiting).toBe(true);
  expect(view.result.current.pending).toBe(true);
  expect(view.result.current.failed).toBe(false);
  expect(view.result.current.report).toEqual(partial);
  await tick(1999);
  expect(fetcher).toHaveBeenCalledTimes(2);
  await tick(1);
  expect(fetcher).toHaveBeenCalledTimes(3);
  expect(view.result.current.report).toEqual(final);
  expect(view.result.current.waiting).toBe(false);
  expect(view.result.current.pending).toBe(false);
});

it('backs off repeated 503s to a capped delay without overlapping polls or declaring failure', async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn(async () => busy());
  vi.stubGlobal('fetch', fetcher);
  const view = renderHook(() => useGameAnalysis(game, true));
  await tick();
  let calls = 1;
  for (const delay of [1000, 2000, 4000, 8000, 16000, 30000, 30000]) {
    await tick(delay - 1);
    expect(fetcher).toHaveBeenCalledTimes(calls);
    await tick(1);
    expect(fetcher).toHaveBeenCalledTimes(++calls);
    expect(view.result.current.waiting).toBe(true);
    expect(view.result.current.failed).toBe(false);
    expect(vi.getTimerCount()).toBe(1);
  }
  view.unmount();
  await tick(60000);
  expect(fetcher).toHaveBeenCalledTimes(calls);
});

it('caps excessive retry guidance and resets backoff after successful progress', async () => {
  vi.useFakeTimers();
  const fetcher = vi
    .fn()
    .mockImplementationOnce(async () => busy('999999'))
    .mockResolvedValueOnce(response({ ...report, status: 'pending' }))
    .mockImplementationOnce(async () => busy())
    .mockResolvedValue(response({ ...report, status: 'complete' }));
  vi.stubGlobal('fetch', fetcher);
  const view = renderHook(() => useGameAnalysis(game, true));
  await tick();
  await tick(29999);
  expect(fetcher).toHaveBeenCalledTimes(1);
  await tick(1);
  expect(fetcher).toHaveBeenCalledTimes(2);
  await tick(300);
  expect(view.result.current.waiting).toBe(true);
  await tick(1000);
  expect(fetcher).toHaveBeenCalledTimes(4);
  expect(view.result.current.pending).toBe(false);
});

it.each(['unmount', 'game', 'position', 'iterations'])(
  'cancels capacity backoff on %s changes',
  async (change) => {
    vi.useFakeTimers();
    let reads = 0;
    let revision = 0;
    const fetcher = vi.fn(async (_url: string, options?: RequestInit) => {
      if (options?.method === 'PUT') {
        revision++;
        return response({ ...JSON.parse(String(options.body)), revision });
      }
      if (++reads === 1) return busy();
      return response({
        ...report,
        cursor: change === 'position' ? 1 : 0,
        status: 'complete',
        advisor_revision: revision,
      });
    });
    vi.stubGlobal('fetch', fetcher);
    const current = { ...game, analytics: neuralSettings };
    const view = renderHook(({ current }) => useGameAnalysis(current, true), {
      initialProps: { current },
    });
    await tick();
    expect(view.result.current.waiting).toBe(true);
    const oldSignal = fetcher.mock.calls[0][1]?.signal;
    if (change === 'unmount') view.unmount();
    else if (change === 'iterations')
      await act(() =>
        view.result.current.updateSettings({
          ...neuralSettings,
          advisor: {
            ...neuralSettings.advisor!,
            config: { ...neuralSettings.advisor!.config, iterations: 4096 },
          },
        }),
      );
    else
      view.rerender({
        current: { ...current, ...(change === 'game' ? { id: 'two' } : { cursor: 1, version: 1 }) },
      });
    await tick();
    expect(oldSignal?.aborted).toBe(true);
    await tick(60000);
    expect(reads).toBe(change === 'unmount' ? 1 : 2);
    if (change !== 'unmount') expect(view.result.current.failed).toBe(false);
    if (change === 'iterations')
      expect(view.result.current.settings.advisor?.config.iterations).toBe(4096);
  },
);

it('aborts an old in-flight poll and never publishes its delayed response or starts duplicate polls', async () => {
  vi.useFakeTimers();
  let finish!: (value: Response) => void;
  const old = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  const fetcher = vi
    .fn()
    .mockReturnValueOnce(old)
    .mockResolvedValue(response({ ...report, cursor: 1, status: 'complete' }));
  vi.stubGlobal('fetch', fetcher);
  const view = renderHook(({ current }) => useGameAnalysis(current, true), {
    initialProps: { current: game },
  });
  await tick(60000);
  expect(fetcher).toHaveBeenCalledTimes(1);
  view.rerender({ current: { ...game, version: 1, cursor: 1 } });
  await tick();
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
  expect(view.result.current.report?.cursor).toBe(1);
  await act(async () => {
    finish(response({ ...report, cursor: 0 }));
    await old;
  });
  expect(view.result.current.report?.cursor).toBe(1);
  await tick(60000);
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it.each([400, 401, 403, 422, 429, 500])(
  'keeps HTTP %s failures terminal until manual retry',
  async (status) => {
    vi.useFakeTimers();
    const fetcher = vi.fn(
      async () => new Response(JSON.stringify({ detail: 'Unavailable' }), { status }),
    );
    vi.stubGlobal('fetch', fetcher);
    const view = renderHook(() => useGameAnalysis(game, true));
    await tick();
    expect(view.result.current.failed).toBe(true);
    expect(view.result.current.pending).toBe(false);
    await tick(60000);
    expect(fetcher).toHaveBeenCalledTimes(1);
    fetcher.mockImplementation(async () => response({ ...report, status: 'complete' }));
    act(() => view.result.current.retry());
    await tick();
    expect(view.result.current.failed).toBe(false);
    expect(view.result.current.report).toMatchObject({ status: 'complete' });
  },
);

it('localizes the pending capacity message without hiding available estimates', () => {
  setLanguage('de', false);
  render(
    <AgentAnalysis game={game} report={report} pending waiting failed={false} retry={vi.fn()} />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Warten auf freie Analysekapazität…');
  expect(screen.getAllByRole('meter')).toHaveLength(2);
});

it('debounces rapid MCTS typing and does not save again on blur or Enter', async () => {
  vi.useFakeTimers();
  const onChange = vi.fn();
  render(
    <AnalyticsSettingsPanel
      game={game}
      profiles={neuralProfiles}
      models={neuralModels}
      settings={neuralSettings}
      onChange={onChange}
    />,
  );
  const input = screen.getByRole('spinbutton', { name: 'MCTS Iterations' });
  for (const value of ['2', '20', '204', '2048']) {
    fireEvent.change(input, { target: { value } });
    await tick(100);
    expect(input).toHaveValue(Number(value));
    expect(onChange).not.toHaveBeenCalled();
  }
  await tick(499);
  expect(onChange).not.toHaveBeenCalled();
  await tick(1);
  expect(onChange).toHaveBeenCalledTimes(1);
  expect(onChange.mock.calls[0][0].advisor.config.iterations).toBe(2048);
  fireEvent.blur(input);
  fireEvent.keyDown(input, { key: 'Enter' });
  await tick(1000);
  expect(onChange).toHaveBeenCalledTimes(1);
});

it('commits MCTS edits on blur or Enter and never commits invalid drafts', async () => {
  vi.useFakeTimers();
  const onChange = vi.fn();
  render(
    <AnalyticsSettingsPanel
      game={game}
      profiles={neuralProfiles}
      models={neuralModels}
      settings={neuralSettings}
      onChange={onChange}
    />,
  );
  const input = screen.getByRole('spinbutton', { name: 'MCTS Iterations' });
  fireEvent.change(input, { target: { value: '2048' } });
  fireEvent.blur(input);
  expect(onChange.mock.calls[0][0].advisor.config.iterations).toBe(2048);
  fireEvent.change(input, { target: { value: '4096' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  fireEvent.blur(input);
  expect(onChange.mock.calls[1][0].advisor.config.iterations).toBe(4096);
  for (const invalid of ['', '-1', '0', '1.5', '5001']) {
    fireEvent.change(input, { target: { value: invalid } });
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.blur(input);
    await tick(1000);
    expect(input).toBeInvalid();
    expect(onChange).toHaveBeenCalledTimes(2);
  }
});

it.each(['Move advisor', 'Algorithm', 'Trained model'])(
  'discards the MCTS draft when real focus moves to %s, before selection changes',
  async (label) => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    render(
      <AnalyticsSettingsPanel
        game={game}
        profiles={neuralProfiles}
        models={neuralModels}
        settings={{
          ...neuralSettings,
          advisor: { ...neuralSettings.advisor!, profile_id: undefined },
        }}
        onChange={onChange}
      />,
    );
    const input = screen.getByRole('spinbutton', { name: 'MCTS Iterations' });
    act(() => input.focus());
    fireEvent.change(input, { target: { value: '4096' } });
    act(() => screen.getByRole('combobox', { name: label }).focus());
    expect(input).toHaveValue(1024);
    await tick(2000);
    expect(onChange).not.toHaveBeenCalled();
  },
);

it.each(['unmount', 'game', 'advisor'])(
  'discards pending MCTS drafts on %s changes',
  async (change) => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    const panel = (current = game, settings = neuralSettings) => (
      <AnalyticsSettingsPanel
        game={current}
        profiles={neuralProfiles}
        models={neuralModels}
        settings={settings}
        onChange={onChange}
      />
    );
    const view = render(panel());
    fireEvent.change(screen.getByRole('spinbutton', { name: 'MCTS Iterations' }), {
      target: { value: '4096' },
    });
    if (change === 'unmount') view.unmount();
    else if (change === 'game') view.rerender(panel({ ...game, id: 'two' }));
    else
      view.rerender(
        panel(game, {
          ...neuralSettings,
          advisor: {
            ...neuralSettings.advisor!,
            config: { algorithm: 'neural', model_id: 'another-model', iterations: 128 },
          },
        }),
      );
    await tick(10000);
    expect(onChange).not.toHaveBeenCalled();
    if (change === 'advisor')
      expect(screen.getByRole('spinbutton', { name: 'MCTS Iterations' })).toHaveValue(128);
  },
);

it('persists one MCTS revision after rapid edits without undoing a search-time change', async () => {
  vi.useFakeTimers();
  let saved = neuralSettings;
  const writes: AnalyticsSettings[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url, options) => {
      const body = JSON.parse(String(options.body));
      saved = {
        ...body,
        revision: saved.revision + 1,
        advisor: {
          ...body.advisor,
          config: { ...body.advisor.config, iterations: body.advisor.config.iterations ?? 1024 },
        },
      };
      writes.push(saved);
      return response(saved);
    }),
  );
  function Controls() {
    const analysis = useGameAnalysis({ ...game, analytics: saved }, false);
    return (
      <AnalyticsSettingsPanel
        game={game}
        profiles={neuralProfiles}
        models={neuralModels}
        settings={analysis.settings}
        onChange={analysis.updateSettings}
      />
    );
  }
  const view = render(<Controls />);
  const input = screen.getByRole('spinbutton', { name: 'MCTS Iterations' });
  for (const value of ['4', '40', '409', '4096']) fireEvent.change(input, { target: { value } });
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Maximum search time (seconds)' }), {
    target: { value: '15' },
  });
  await tick();
  expect(writes).toHaveLength(1);
  expect(input).toHaveValue(4096);
  await tick(600);
  expect(writes).toHaveLength(2);
  expect(saved).toMatchObject({
    time_limit: 15,
    revision: 2,
    advisor: { config: { iterations: 4096 } },
  });
  fireEvent.blur(input);
  await tick(1000);
  expect(writes).toHaveLength(2);
  view.unmount();
  render(<Controls />);
  expect(screen.getByRole('spinbutton', { name: 'MCTS Iterations' })).toHaveValue(4096);
});
