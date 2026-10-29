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
import type { Game } from './types';
import type { PositionAnalysis } from './analysisTypes';
import { MobilePlayContext } from './mobilePlay';

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
  vi.unstubAllGlobals();
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
