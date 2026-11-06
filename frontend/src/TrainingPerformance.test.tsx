import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it } from 'vitest';
import TrainingPerformance, {
  completedGames,
  performanceChartRows,
  performanceMetrics,
} from './TrainingPerformance';
import { chartMaximum } from './TrainingMetricChart';
import { latestPerformance } from './trainingDashboardData';
import { setLanguage } from './i18n';
import type { HistoryPoint } from './trainingDashboardData';

beforeEach(() => setLanguage('en', false));
afterEach(cleanup);

const history: HistoryPoint[] = [
  {
    iteration: 1,
    performance: {
      simulations_per_second: 10,
      games_per_hour: 100,
      average_game_length: 20,
      attacker_wins: 3,
      defender_wins: 1,
      drawn_games: 1,
      completed_games: 5,
    },
  },
  {
    iteration: 2,
    performance: {
      simulations_per_second: 20,
      games_per_hour: 200,
      average_game_length: 22,
      attacker_wins: 2,
      defender_wins: 2,
      drawn_games: 0,
      completed_games: 4,
    },
  },
];

const metric = (key: string) => performanceMetrics.find((item) => item.key === key)!;
const options = () =>
  [...screen.getByLabelText('Performance metric').querySelectorAll('option')].map((option) =>
    option.getAttribute('value'),
  );
const pointTitles = (label: string) =>
  [...screen.getByRole('img', { name: label }).querySelectorAll('circle title')].map(
    (title) => title.textContent,
  );
const percentAxis = (label: string) =>
  [...screen.getByRole('img', { name: label }).querySelectorAll('text')]
    .map((node) => node.textContent)
    .filter((text): text is string => !!text && text.endsWith('%'));
const percentSeries = [
  { key: 'attacker_win_rate', name: 'Attacker win rate', color: 'var(--chart-value)' },
];
const rateRows = (values: number[]): HistoryPoint[] =>
  values.map((value, index) => ({ iteration: index + 1, attacker_win_rate: value }));

it('offers every requested performance metric alongside the existing options', () => {
  render(<TrainingPerformance progress={{ history }} />);
  expect(options()).toEqual(
    expect.arrayContaining([
      'simulations_per_second',
      'positions_per_second',
      'neural_evaluations_per_second',
      'games_per_hour',
      'average_game_length',
      'attacker_wins',
      'attacker_win_rate',
      'defender_wins',
      'defender_win_rate',
    ]),
  );
});

it('maps each requested metric to its stored field, unit and value format', () => {
  expect(metric('games_per_hour')).toMatchObject({ label: 'Games / hour', unit: 'Games / hour' });
  expect(metric('average_game_length')).toMatchObject({
    label: 'Average game length',
    unit: 'Moves',
  });
  expect(metric('attacker_wins')).toMatchObject({ label: 'Attacker wins', unit: 'Games' });
  expect(metric('defender_wins')).toMatchObject({ label: 'Defender wins', unit: 'Games' });
  expect(metric('attacker_win_rate')).toMatchObject({
    label: 'Attacker win rate',
    unit: 'Win rate',
    percent: true,
  });
  expect(metric('defender_win_rate')).toMatchObject({
    label: 'Defender win rate',
    unit: 'Win rate',
    percent: true,
  });
});

it('derives side win rates per iteration without changing the stored win counts', () => {
  const rows = performanceChartRows(history);
  expect(rows[0]).toMatchObject({
    iteration: 1,
    attacker_wins: 3,
    defender_wins: 1,
    attacker_win_rate: 0.6,
    defender_win_rate: 0.2,
  });
  expect(rows[1]).toMatchObject({
    iteration: 2,
    attacker_wins: 2,
    defender_wins: 2,
    attacker_win_rate: 0.5,
    defender_win_rate: 0.5,
  });
});

it('falls back to wins plus draws for records without completed_games', () => {
  const [row] = performanceChartRows([
    { iteration: 1, performance: { attacker_wins: 3, defender_wins: 1, drawn_games: 1 } },
  ]);
  expect(row.attacker_win_rate).toBeCloseTo(3 / 5);
  expect(row.defender_win_rate).toBeCloseTo(1 / 5);
});

it('leaves a win rate absent instead of plotting a misleading zero', () => {
  const rows = performanceChartRows([
    {
      iteration: 1,
      performance: { attacker_wins: 3, defender_wins: 1, drawn_games: 0, completed_games: 0 },
    },
    { iteration: 2, performance: { attacker_wins: 3 } },
    { iteration: 3, performance: null },
  ]);
  expect(rows[0].attacker_win_rate).toBeUndefined();
  expect(rows[1].attacker_win_rate).toBeUndefined();
  expect(rows[2].attacker_win_rate).toBeUndefined();
});

it('plots a selected win rate as a percentage at the recorded iteration', () => {
  render(<TrainingPerformance progress={{ history }} />);
  fireEvent.change(screen.getByLabelText('Performance metric'), {
    target: { value: 'attacker_win_rate' },
  });
  expect(pointTitles('Attacker win rate')).toEqual(['Iteration 1: 60%', 'Iteration 2: 50%']);
  expect(screen.getByText('Win rate')).toBeInTheDocument();
});

it('plots win counts as numbers independently of their win rates', () => {
  render(<TrainingPerformance progress={{ history }} />);
  fireEvent.change(screen.getByLabelText('Performance metric'), {
    target: { value: 'attacker_wins' },
  });
  expect(pointTitles('Attacker wins')).toEqual(['Iteration 1: 3', 'Iteration 2: 2']);
  fireEvent.change(screen.getByLabelText('Performance metric'), {
    target: { value: 'defender_wins' },
  });
  expect(pointTitles('Defender wins')).toEqual(['Iteration 1: 1', 'Iteration 2: 2']);
});

it('plots throughput and game length with their stored units', () => {
  render(<TrainingPerformance progress={{ history }} />);
  fireEvent.change(screen.getByLabelText('Performance metric'), {
    target: { value: 'games_per_hour' },
  });
  expect(pointTitles('Games / hour')).toEqual(['Iteration 1: 100', 'Iteration 2: 200']);
  expect(screen.getAllByText('Games / hour').length).toBeGreaterThan(0);
  fireEvent.change(screen.getByLabelText('Performance metric'), {
    target: { value: 'average_game_length' },
  });
  expect(pointTitles('Average game length')).toEqual(['Iteration 1: 20', 'Iteration 2: 22']);
  expect(screen.getByText('Moves')).toBeInTheDocument();
});

it('skips missing historical values without drawing a zero point', () => {
  render(
    <TrainingPerformance
      progress={{
        history: [
          { iteration: 1, performance: { games_per_hour: 100 } },
          { iteration: 2, performance: {} },
        ],
      }}
    />,
  );
  fireEvent.change(screen.getByLabelText('Performance metric'), {
    target: { value: 'games_per_hour' },
  });
  expect(pointTitles('Games / hour')).toEqual(['Iteration 1: 100']);
});

it('switches between every metric without a frontend error', () => {
  render(<TrainingPerformance progress={{ history }} />);
  for (const item of performanceMetrics) {
    fireEvent.change(screen.getByLabelText('Performance metric'), { target: { value: item.key } });
    expect(screen.getByRole('img', { name: item.label })).toBeInTheDocument();
  }
});

it('keeps the throughput history headings sticky inside their scroll container', async () => {
  render(<TrainingPerformance progress={{ history }} />);
  fireEvent.click(screen.getByText('Throughput history'));
  const table = await screen.findByRole('table', { name: 'Throughput history' });
  // Semantic column headers live in thead so only they are pinned, not the first data row.
  expect(table.querySelector('thead th')).toBeInTheDocument();
  expect(table.querySelector('tbody th')).toHaveTextContent('1');
  const css = readFileSync(resolve(process.cwd(), 'src/trainingDashboard.css'), 'utf8');
  const rule = css.match(
    /\.training-workshop \.training-performance \.table-scroll thead th\s*\{([^}]*)\}/,
  );
  expect(rule?.[1]).toMatch(/position:\s*sticky/);
  expect(rule?.[1]).toMatch(/top:\s*0/);
  expect(rule?.[1]).toMatch(/background:\s*var\(--surface\)/);
});

// Finding 1: historical fallback.

it('prefers the current progress performance snapshot over history', () => {
  const current = { games_per_hour: 999 };
  expect(latestPerformance({ performance: current, history }, history)).toBe(current);
});

it('reads the current snapshot from metrics.performance when needed', () => {
  const current = { games_per_hour: 555 };
  expect(latestPerformance({ metrics: { performance: current }, history }, history)).toBe(current);
});

it('falls back to the most recent recorded history performance', () => {
  const rows: HistoryPoint[] = [
    { iteration: 1, performance: { games_per_hour: 100 } },
    { iteration: 2, performance: { games_per_hour: 200 } },
    { iteration: 3 },
  ];
  expect(latestPerformance({ history: rows }, rows)).toEqual({ games_per_hour: 200 });
});

it('finds the latest usable snapshot in a partially populated history', () => {
  const rows: HistoryPoint[] = [
    { iteration: 1 },
    { iteration: 2, performance: { games_per_hour: 100 } },
    { iteration: 3 },
    { iteration: 4, performance: null },
    { iteration: 5 },
    { iteration: 6, performance: {} },
  ];
  expect(latestPerformance({ history: rows }, rows)).toEqual({ games_per_hour: 100 });
});

it('returns undefined when no performance snapshot exists anywhere', () => {
  const rows: HistoryPoint[] = [{ iteration: 1 }, { iteration: 2, performance: null }];
  expect(latestPerformance({ history: rows }, rows)).toBeUndefined();
});

it('ignores empty snapshots and returns undefined when none recorded anything', () => {
  const rows: HistoryPoint[] = [
    { iteration: 1, performance: {} },
    { iteration: 2, performance: {} },
  ];
  expect(latestPerformance({ history: rows }, rows)).toBeUndefined();
});

it('falls through an empty progress.performance to metrics.performance', () => {
  const current = { games_per_hour: 555 };
  expect(
    latestPerformance({ performance: {}, metrics: { performance: current }, history }, history),
  ).toBe(current);
});

it('falls through empty current sources to the latest usable history snapshot', () => {
  const rows: HistoryPoint[] = [
    { iteration: 1, performance: { games_per_hour: 100 } },
    { iteration: 2, performance: {} },
  ];
  expect(
    latestPerformance({ performance: {}, metrics: { performance: {} }, history: rows }, rows),
  ).toEqual({ games_per_hour: 100 });
});

it('renders history metrics when both current performance sources are empty', () => {
  render(
    <TrainingPerformance
      progress={{
        performance: {},
        metrics: { performance: {} },
        history: [{ iteration: 1, performance: { games_per_hour: 100 } }],
      }}
    />,
  );
  expect(screen.getByLabelText('Training performance')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Performance metric'), {
    target: { value: 'games_per_hour' },
  });
  expect(pointTitles('Games / hour')).toEqual(['Iteration 1: 100']);
});

it('plots earlier observations when the final history entry has no performance', () => {
  render(
    <TrainingPerformance
      progress={{
        history: [
          { iteration: 1, performance: { games_per_hour: 100 } },
          { iteration: 2, performance: { games_per_hour: 200 } },
          { iteration: 3 },
        ],
      }}
    />,
  );
  expect(screen.getByLabelText('Training performance')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Performance metric'), {
    target: { value: 'games_per_hour' },
  });
  expect(pointTitles('Games / hour')).toEqual(['Iteration 1: 100', 'Iteration 2: 200']);
});

it('shows the missing-performance notice only when nothing was recorded', () => {
  render(<TrainingPerformance progress={{ history: [{ iteration: 1 }, { iteration: 2 }] }} />);
  expect(
    screen.getByText('Performance metrics were not recorded by this worker.'),
  ).toBeInTheDocument();
});

// Finding 2: win-rate validation.

it('rejects zero, negative, fractional and non-finite counters', () => {
  const rows = performanceChartRows([
    {
      iteration: 1,
      performance: { attacker_wins: 0, defender_wins: 0, drawn_games: 0, completed_games: 0 },
    },
    {
      iteration: 2,
      performance: { attacker_wins: -1, defender_wins: -2, drawn_games: 0, completed_games: 3 },
    },
    {
      iteration: 3,
      performance: { attacker_wins: 2.5, defender_wins: 0.5, drawn_games: 0, completed_games: 3 },
    },
    {
      iteration: 4,
      performance: {
        attacker_wins: Number.NaN,
        defender_wins: Number.POSITIVE_INFINITY,
        drawn_games: 0,
        completed_games: 3,
      },
    },
  ]);
  for (const row of rows) {
    expect(row.attacker_win_rate).toBeUndefined();
    expect(row.defender_win_rate).toBeUndefined();
  }
});

it('keeps legitimate 0% and 100% outcomes', () => {
  const rows = performanceChartRows([
    {
      iteration: 1,
      performance: { attacker_wins: 0, defender_wins: 3, drawn_games: 1, completed_games: 4 },
    },
    {
      iteration: 2,
      performance: { attacker_wins: 4, defender_wins: 0, drawn_games: 0, completed_games: 4 },
    },
  ]);
  expect(rows[0].attacker_win_rate).toBe(0);
  expect(rows[0].defender_win_rate).toBe(0.75);
  expect(rows[1].attacker_win_rate).toBe(1);
  expect(rows[1].defender_win_rate).toBe(0);
});

it('counts draws in the denominator and excludes truncated games', () => {
  const [row] = performanceChartRows([
    {
      iteration: 1,
      performance: {
        attacker_wins: 2,
        defender_wins: 2,
        drawn_games: 1,
        truncated_games: 10,
        completed_games: 5,
      },
    },
  ]);
  expect(completedGames(row)).toBe(5);
  expect(row.attacker_win_rate).toBe(0.4);
  expect(row.defender_win_rate).toBe(0.4);
});

it('treats inconsistent win and draw totals as missing', () => {
  const [row] = performanceChartRows([
    {
      iteration: 1,
      performance: { attacker_wins: 2, defender_wins: 2, drawn_games: 2, completed_games: 5 },
    },
  ]);
  expect(row.attacker_win_rate).toBeUndefined();
  expect(row.defender_win_rate).toBeUndefined();
});

it('rejects a win count larger than the completed total', () => {
  const [row] = performanceChartRows([
    { iteration: 1, performance: { attacker_wins: 5, completed_games: 3 } },
  ]);
  expect(row.attacker_win_rate).toBeUndefined();
});

it('validates a stored win rate and keeps plausible fractions', () => {
  const rows = performanceChartRows([
    {
      iteration: 1,
      performance: {
        attacker_win_rate: 60,
        attacker_wins: 3,
        defender_wins: 1,
        drawn_games: 0,
        completed_games: 4,
      },
    },
    {
      iteration: 2,
      performance: {
        attacker_win_rate: -0.5,
        attacker_wins: 1,
        defender_wins: 3,
        drawn_games: 0,
        completed_games: 4,
      },
    },
    { iteration: 3, performance: { attacker_win_rate: 0.75 } },
  ]);
  // An out-of-range stored value is re-derived from the counts, never rescaled.
  expect(rows[0].attacker_win_rate).toBe(0.75);
  expect(rows[1].attacker_win_rate).toBe(0.25);
  // A plausible stored fraction is trusted even without counts.
  expect(rows[2].attacker_win_rate).toBe(0.75);
});

it('drops an ambiguous stored rate when no counts can replace it', () => {
  const [row] = performanceChartRows([{ iteration: 1, performance: { attacker_win_rate: 60 } }]);
  expect(row.attacker_win_rate).toBeUndefined();
});

it('does not plot an invalid win rate point', () => {
  render(
    <TrainingPerformance
      progress={{
        history: [
          { iteration: 1, performance: { attacker_win_rate: 60 } },
          { iteration: 2, performance: { attacker_win_rate: 0.5 } },
        ],
      }}
    />,
  );
  fireEvent.change(screen.getByLabelText('Performance metric'), {
    target: { value: 'attacker_win_rate' },
  });
  expect(pointTitles('Attacker win rate')).toEqual(['Iteration 2: 50%']);
});

it('drops invalid win counts from chart rows but keeps legitimate zeros', () => {
  const rows = performanceChartRows([
    { iteration: 1, performance: { attacker_wins: -1, defender_wins: 2.5, completed_games: 3 } },
    { iteration: 2, performance: { attacker_wins: 0, defender_wins: 3, completed_games: 3 } },
  ]);
  expect(rows[0].attacker_wins).toBeUndefined();
  expect(rows[0].defender_wins).toBeUndefined();
  expect(rows[1].attacker_wins).toBe(0);
  expect(rows[1].defender_wins).toBe(3);
});

it('does not plot an invalid win count', () => {
  render(
    <TrainingPerformance
      progress={{
        history: [
          { iteration: 1, performance: { attacker_wins: -1, completed_games: 2 } },
          { iteration: 2, performance: { attacker_wins: 0, completed_games: 2 } },
        ],
      }}
    />,
  );
  fireEvent.change(screen.getByLabelText('Performance metric'), {
    target: { value: 'attacker_wins' },
  });
  expect(pointTitles('Attacker wins')).toEqual(['Iteration 2: 0']);
});

it('accepts incomplete but consistent outcome counters', () => {
  const [row] = performanceChartRows([
    { iteration: 1, performance: { attacker_wins: 3, defender_wins: 2, completed_games: 5 } },
  ]);
  expect(row.attacker_win_rate).toBe(0.6);
  expect(row.defender_win_rate).toBe(0.4);
});

it('rejects partial counters whose known sum exceeds completed games', () => {
  const [row] = performanceChartRows([
    { iteration: 1, performance: { attacker_wins: 4, defender_wins: 4, completed_games: 5 } },
  ]);
  expect(row.attacker_win_rate).toBeUndefined();
  expect(row.defender_win_rate).toBeUndefined();
});

it('rejects a negative opponent-side win count', () => {
  const [row] = performanceChartRows([
    { iteration: 1, performance: { attacker_wins: 3, defender_wins: -1, completed_games: 4 } },
  ]);
  expect(row.attacker_win_rate).toBeUndefined();
  expect(row.defender_win_rate).toBeUndefined();
});

it('rejects a fractional opponent-side win count', () => {
  const [row] = performanceChartRows([
    { iteration: 1, performance: { attacker_wins: 3, defender_wins: 1.5, completed_games: 5 } },
  ]);
  expect(row.attacker_win_rate).toBeUndefined();
  expect(row.defender_win_rate).toBeUndefined();
});

it('rejects a counter above the completed total even when the sum fits', () => {
  const [row] = performanceChartRows([
    { iteration: 1, performance: { attacker_wins: 6, completed_games: 5 } },
  ]);
  expect(row.attacker_win_rate).toBeUndefined();
  expect(row.attacker_wins).toBeUndefined();
});

it('keeps a valid stored rate that agrees with the counts', () => {
  const [row] = performanceChartRows([
    {
      iteration: 1,
      performance: {
        attacker_win_rate: 0.75,
        attacker_wins: 3,
        defender_wins: 1,
        drawn_games: 0,
        completed_games: 4,
      },
    },
  ]);
  expect(row.attacker_win_rate).toBe(0.75);
});

it('prefers the count-based rate when a stored rate contradicts the counts', () => {
  const [row] = performanceChartRows([
    {
      iteration: 1,
      performance: {
        attacker_win_rate: 0.9,
        attacker_wins: 3,
        defender_wins: 1,
        drawn_games: 0,
        completed_games: 4,
      },
    },
  ]);
  expect(row.attacker_win_rate).toBe(0.75);
});

it('drops a stored rate when counters exist but cannot form a valid rate', () => {
  const [row] = performanceChartRows([
    { iteration: 1, performance: { attacker_win_rate: 0.5, completed_games: 4 } },
  ]);
  expect(row.attacker_win_rate).toBeUndefined();
});

it('handles a zero completed-game denominator', () => {
  const [row] = performanceChartRows([
    {
      iteration: 1,
      performance: { attacker_wins: 0, defender_wins: 0, drawn_games: 0, completed_games: 0 },
    },
  ]);
  expect(row.attacker_win_rate).toBeUndefined();
  expect(row.defender_win_rate).toBeUndefined();
});

it('handles a missing completed-game denominator', () => {
  const [row] = performanceChartRows([
    { iteration: 1, performance: { attacker_wins: 2, defender_wins: 1 } },
  ]);
  expect(row.attacker_win_rate).toBeUndefined();
  expect(row.defender_win_rate).toBeUndefined();
});

// Finding 3: percentage axis scaling.

it('keeps the percentage axis within 0-100% and zooms in on small rates', () => {
  expect(chartMaximum(rateRows([0.5]), percentSeries, false, true)).toBeCloseTo(0.54);
  expect(chartMaximum(rateRows([0.05]), percentSeries, false, true)).toBeCloseTo(0.054);
  expect(chartMaximum(rateRows([0.98]), percentSeries, false, true)).toBe(1);
  expect(chartMaximum(rateRows([1]), percentSeries, false, true)).toBe(1);
});

it('uses a readable full scale for an all-zero percentage series', () => {
  expect(chartMaximum(rateRows([0, 0]), percentSeries, false, true)).toBe(1);
  expect(chartMaximum([], percentSeries, false, true)).toBe(1);
});

it('leaves non-percentage and score axis scaling unchanged', () => {
  const rows: HistoryPoint[] = [{ iteration: 1, value: 0.5 }];
  const series = [{ key: 'value', name: 'Value', color: 'var(--chart-value)' }];
  expect(chartMaximum(rows, series, false, false)).toBeCloseTo(0.54);
  expect(chartMaximum(rows, series, true, false)).toBe(1);
  expect(chartMaximum(rows, series, true, true)).toBe(1);
});

it('renders a readable 0-100% axis for an all-zero win-rate series', () => {
  render(
    <TrainingPerformance
      progress={{
        history: [
          {
            iteration: 1,
            performance: { attacker_wins: 0, defender_wins: 2, drawn_games: 0, completed_games: 2 },
          },
        ],
      }}
    />,
  );
  fireEvent.change(screen.getByLabelText('Performance metric'), {
    target: { value: 'attacker_win_rate' },
  });
  expect(percentAxis('Attacker win rate')).toEqual(['0%', '25%', '50%', '75%', '100%']);
  expect(pointTitles('Attacker win rate')).toEqual(['Iteration 1: 0%']);
});

it('renders a 100% observation at the top of a full axis', () => {
  render(
    <TrainingPerformance
      progress={{
        history: [
          {
            iteration: 1,
            performance: { attacker_wins: 2, defender_wins: 0, drawn_games: 0, completed_games: 2 },
          },
        ],
      }}
    />,
  );
  fireEvent.change(screen.getByLabelText('Performance metric'), {
    target: { value: 'attacker_win_rate' },
  });
  expect(percentAxis('Attacker win rate').at(-1)).toBe('100%');
  expect(pointTitles('Attacker win rate')).toEqual(['Iteration 1: 100%']);
});

it('clamps a near-100% series so the axis never exceeds 100%', () => {
  render(
    <TrainingPerformance
      progress={{
        history: [
          {
            iteration: 1,
            performance: {
              attacker_wins: 19,
              defender_wins: 1,
              drawn_games: 0,
              completed_games: 20,
            },
          },
        ],
      }}
    />,
  );
  fireEvent.change(screen.getByLabelText('Performance metric'), {
    target: { value: 'attacker_win_rate' },
  });
  expect(percentAxis('Attacker win rate').at(-1)).toBe('100%');
  expect(pointTitles('Attacker win rate')).toEqual(['Iteration 1: 95%']);
});

it('handles mixed and missing percentage observations', () => {
  render(
    <TrainingPerformance
      progress={{
        history: [
          {
            iteration: 1,
            performance: { attacker_wins: 1, defender_wins: 1, drawn_games: 0, completed_games: 2 },
          },
          { iteration: 2 },
          {
            iteration: 3,
            performance: { attacker_wins: 3, defender_wins: 0, drawn_games: 1, completed_games: 4 },
          },
        ],
      }}
    />,
  );
  fireEvent.change(screen.getByLabelText('Performance metric'), {
    target: { value: 'attacker_win_rate' },
  });
  expect(pointTitles('Attacker win rate')).toEqual(['Iteration 1: 50%', 'Iteration 3: 75%']);
  expect(percentAxis('Attacker win rate').at(-1)).toBe('81%');
});
