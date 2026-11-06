import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it } from 'vitest';
import TrainingPerformance, {
  performanceChartRows,
  performanceMetrics,
} from './TrainingPerformance';
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
