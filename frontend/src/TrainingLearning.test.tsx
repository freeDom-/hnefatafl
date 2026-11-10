import { useState } from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { TrainingLearningView } from './TrainingAnalysisViews';
import TrainingJobDashboard from './TrainingJobDashboard';
import { chartMaximum, policySeries } from './TrainingMetricChart';
import { learningChartRows, learningMetrics, formatLearningValue } from './trainingLearningData';
import { iterationRanges, recentHistory, type HistoryPoint } from './trainingDashboardData';
import { learningHistory } from '../tests/learning-fixtures';
import { setLanguage, translate } from './i18n';
import type { Job } from './types';
import type { JobTab } from './jobTabs';

afterEach(() => {
  cleanup();
  setLanguage('en', false);
});
const history = learningHistory();
const view = (rows = history, range = 0) => (
  <TrainingLearningView
    progress={{}}
    history={rows}
    config={{ batch_size: 2048, mcts_iterations: 999 }}
    range={range}
  />
);
const choose = (value: string) =>
  fireEvent.change(screen.getByLabelText('Learning metric'), { target: { value } });
const graph = () => screen.getByRole('img');
const tooltip = () => document.querySelector('.chart-tooltip');
const paths = () =>
  [...graph().querySelectorAll('path')].map((path) => path.getAttribute('d') ?? '');
async function table() {
  fireEvent.click(screen.getByText('View graph data'));
  return screen.findByRole('table', { name: 'Training history data' });
}

it('defaults to one loss chart with both existing series and no separate replay chart', () => {
  render(view());
  expect(screen.getByLabelText('Learning metric')).toHaveValue('loss');
  expect(screen.getAllByRole('img')).toHaveLength(1);
  expect(graph()).toHaveAccessibleName('Loss by iteration');
  expect(graph().querySelectorAll('path')).toHaveLength(2);
  expect(graph().querySelector('path[stroke="var(--chart-policy)"]')).toBeInTheDocument();
  expect(graph().querySelector('path[stroke="var(--chart-value)"]')).toBeInTheDocument();
  expect(screen.queryByRole('img', { name: 'Replay buffer positions' })).not.toBeInTheDocument();
  expect(screen.getByText(/Lower loss means better fit/)).toBeInTheDocument();
});

it('labels the selected job replay capacity separately from recorded replay occupancy', () => {
  render(
    <TrainingLearningView
      progress={{}}
      history={history}
      config={{ replay_buffer_size: 8000 }}
      range={0}
    />,
  );
  const kpis = document.querySelector('.learning-kpis') as HTMLElement;
  expect(
    within(kpis).getByText('Replay buffer positions').closest('div')?.querySelector('dd'),
  ).toHaveTextContent('12,000');
  const capacity = within(kpis).getByText('Replay buffer capacity').closest('div')!;
  expect(capacity.querySelector('dd')).toHaveTextContent('8,000');
  expect(capacity).toHaveTextContent('Selected job configuration; earlier iterations may differ.');
});

it('switches every recorded metric, title, series, unit, description and exact tooltip', () => {
  render(view());
  for (const metric of learningMetrics) {
    expect(screen.getByRole('option', { name: metric.label })).toBeEnabled();
    choose(metric.key);
    expect(graph()).toHaveAccessibleName(metric.label);
    expect(graph()).toHaveAttribute(
      'aria-description',
      expect.stringContaining(metric.description),
    );
    expect(graph().querySelector('.chart-unit')).toHaveTextContent(metric.unit);
    expect(graph().querySelectorAll('path')).toHaveLength(metric.series.length);
    fireEvent.keyDown(graph(), { key: 'End' });
    expect(tooltip()).toHaveTextContent('Iteration 1500');
    for (const series of metric.series) expect(tooltip()).toHaveTextContent(series.name);
  }
  choose('policy_loss');
  expect(graph().querySelectorAll('path')).toHaveLength(1);
  expect(tooltip()).not.toHaveTextContent('Value loss');
  choose('replay_positions');
  expect(tooltip()).toHaveTextContent('Replay buffer positions: 12,000');
  choose('replay_buffer_size');
  expect(tooltip()).toHaveTextContent('Replay buffer capacity: 12,000');
});

it('reconstructs all three phases at their exact iteration boundaries', () => {
  const rows = learningChartRows(history);
  for (const [iteration, batch, mcts] of [
    [1, 64, 64],
    [1000, 64, 64],
    [1001, 128, 128],
    [1250, 128, 128],
    [1251, 128, 256],
    [1500, 128, 256],
  ]) {
    expect(rows[iteration - 1]).toMatchObject({
      iteration,
      parameter_batch_size: batch,
      parameter_mcts_iterations: mcts,
    });
  }
  expect(history.filter((row) => row.training_parameters)).toHaveLength(3);
  expect(rows[1249].effective_learning_rate).toBe(1e-7);
  expect(rows[1249].learning_rate).toBe(0.0001);
  expect(rows[1250].learning_rate).toBe(1e-7);
});

it('draws horizontal-then-vertical steps at the first iteration using the changed settings', () => {
  render(view(history.slice(0, 1003), 5));
  choose('batch_size');
  const path = paths()[0];
  const x = 58 + ((1001 - 999) / (1003 - 999)) * (520 - 58 - 16);
  expect(path).toContain(`H${x.toFixed(2)}V`);
  expect(path).not.toContain('L');
  fireEvent.keyDown(graph(), { key: 'Home' });
  expect(tooltip()).toHaveTextContent('Iteration 999 · Training batch size: 64');
  fireEvent.keyDown(graph(), { key: 'ArrowRight' });
  expect(tooltip()).toHaveTextContent('Iteration 1000 · Training batch size: 64');
  fireEvent.keyDown(graph(), { key: 'ArrowRight' });
  expect(tooltip()).toHaveTextContent('Iteration 1001 · Training batch size: 128');
});

it.each(iterationRanges)(
  'applies the last %i iterations after reconstructing every selectable metric',
  (range) => {
    render(view(history, range));
    for (const metric of learningMetrics) {
      choose(metric.key);
      fireEvent.keyDown(graph(), { key: 'Home' });
      expect(tooltip()).toHaveTextContent(`Iteration ${1501 - range} ·`);
      fireEvent.keyDown(graph(), { key: 'End' });
      expect(tooltip()).toHaveTextContent('Iteration 1500 ·');
    }
    choose('mcts_iterations');
    expect(tooltip()).toHaveTextContent('256');
  },
);

it('retains only reliable legacy observations, including when a range excludes its initial snapshot', async () => {
  const legacy = learningHistory(true);
  const rows = learningChartRows(legacy);
  expect(rows.slice(0, 1250).every((row) => row.parameter_batch_size === undefined)).toBe(true);
  expect(rows.slice(1250).every((row) => row.parameter_batch_size === 128)).toBe(true);
  const component = render(view(legacy));
  choose('batch_size');
  fireEvent.keyDown(graph(), { key: 'Home' });
  expect(tooltip()).toHaveTextContent('Training batch size: —');
  expect(paths()[0].split('M')).toHaveLength(2);
  const data = await table();
  expect(within(data).getAllByRole('row')[1250].lastElementChild).toHaveTextContent('—');
  expect(within(data).getAllByRole('row')[1251].lastElementChild).toHaveTextContent('128');
  component.rerender(view(legacy, 100));
  fireEvent.keyDown(graph(), { key: 'Home' });
  expect(tooltip()).toHaveTextContent('Iteration 1401 · Training batch size: 128');
});

it('breaks lines through missing values and iteration gaps, preserving zero and isolated observations', () => {
  const rows: HistoryPoint[] = [
    { iteration: 1, training_parameters: { version: 1, weight_decay: 0 } },
    { iteration: 2, training_parameters_iteration: 1 },
    { iteration: 3 },
    { iteration: 4, training_parameters: { version: 1, weight_decay: 0.01 } },
    { iteration: 8, training_parameters_iteration: 4 },
  ];
  render(view(rows));
  choose('weight_decay');
  expect(paths()[0].match(/M/g)).toHaveLength(3);
  fireEvent.keyDown(graph(), { key: 'Home' });
  expect(tooltip()).toHaveTextContent('Weight decay: 0');
  fireEvent.keyDown(graph(), { key: 'ArrowRight' });
  fireEvent.keyDown(graph(), { key: 'ArrowRight' });
  expect(tooltip()).toHaveTextContent('Iteration 3 · Weight decay: —');
});

it('does not carry provenance into older-worker rows or through corrupt/future events', () => {
  const rows = learningChartRows([
    {
      iteration: 1,
      training_parameters: { version: 1, batch_size: 64, weight_decay: 0, token: 'private' },
    },
    { iteration: 2, parameter_batch_size: 2048 },
    { iteration: 3, training_parameters: { version: 2, batch_size: 256 } },
    { iteration: 4, training_parameters_iteration: 3 },
    {
      iteration: 5,
      training_parameters: { version: 1, batch_size: true, weight_decay: -1, epochs: '4' },
    },
    { iteration: 6, training_parameters: null },
  ]);
  expect(rows[0].parameter_batch_size).toBe(64);
  expect(rows[0].parameter_weight_decay).toBe(0);
  expect(rows.slice(1).every((row) => row.parameter_batch_size === undefined)).toBe(true);
  expect(rows[4].parameter_weight_decay).toBeUndefined();
  expect(rows[4].parameter_epochs).toBeUndefined();
  expect(rows[0].parameter_token).toBeUndefined();
});

it('marks unavailable types explicitly and enables observations arriving during polling without resetting selection', () => {
  const component = render(view([{ iteration: 1, policy_loss: 0.5 }]));
  expect(screen.getByRole('option', { name: 'Training batch size · Not recorded' })).toBeDisabled();
  choose('policy_loss');
  component.rerender(view([{ iteration: 1, policy_loss: 0.5 }, history[0]]));
  expect(screen.getByLabelText('Learning metric')).toHaveValue('policy_loss');
  expect(screen.getByRole('option', { name: 'Training batch size' })).toBeEnabled();
});

it('shows a clear empty window without discarding earlier available data or fabricating zeros', () => {
  const rows = history.map((row) =>
    row.iteration > 1490 ? { ...row, learning_rate: undefined } : row,
  );
  render(view(rows, 10));
  choose('learning_rate');
  expect(screen.getByText('No recorded observations')).toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'Learning rate (used)' })).toBeEnabled();
  expect(paths()[0]).not.toMatch(/[MLHV]/);
});

it('scales tiny rates to their data and preserves the existing loss, score and performance scaling', () => {
  const series = [{ key: 'learning_rate', name: 'Rate', color: 'red' }];
  const rows = [
    { iteration: 1, learning_rate: 1e-7 },
    { iteration: 2, learning_rate: 2e-7 },
  ];
  expect(chartMaximum(rows, series, false, false, true)).toBeCloseTo(2.16e-7, 14);
  expect(chartMaximum(rows, series, false, false)).toBeCloseTo(0.00108);
  expect(chartMaximum(rows, policySeries, true, false)).toBe(1);
  render(view(rows));
  choose('learning_rate');
  const ys = [...graph().querySelectorAll('circle')].map((circle) =>
    Number(circle.getAttribute('cy')),
  );
  expect(Math.max(...ys) - Math.min(...ys)).toBeGreaterThan(70);
  expect(graph().textContent).toContain('E-7');
  expect(formatLearningValue(0.000000123456789123456, 'en-GB')).toBe('1.23456789123456E-7');
  expect(formatLearningValue(0.000000123456789123456, 'de-DE')).toBe('1,23456789123456E-7');
});

it('keeps one lazy bounded table with core recorded values, per-iteration updates and the selected exact value', async () => {
  render(view(history, 10));
  expect(screen.queryByRole('table')).not.toBeInTheDocument();
  choose('learning_rate');
  const data = await table();
  expect(screen.getAllByRole('table')).toHaveLength(1);
  expect(data.closest('.bounded-table')).toBeInTheDocument();
  expect(within(data).getAllByRole('row')).toHaveLength(11);
  for (const name of [
    'Policy loss',
    'Value loss',
    'Replay buffer positions',
    'Optimizer updates',
    'Learning rate (used)',
  ])
    expect(within(data).getByRole('columnheader', { name })).toBeInTheDocument();
  const last = within(data).getAllByRole('row').at(-1)!;
  expect(last.lastElementChild).toHaveTextContent('1E-7');
  const updateIndex = within(data)
    .getAllByRole('columnheader')
    .findIndex((header) => header.textContent === 'Optimizer updates');
  expect(last.children[updateIndex]).toHaveTextContent(/^4$/);
  choose('batch_size');
  expect(
    within(data).getByRole('columnheader', { name: 'Training batch size' }),
  ).toBeInTheDocument();
  expect(last.lastElementChild).toHaveTextContent('128');
  expect(
    within(data).queryByRole('columnheader', { name: 'Learning rate (used)' }),
  ).not.toBeInTheDocument();
});

function Dashboard({ job }: { job: Job }) {
  const [tab, setTab] = useState<JobTab>('Learning');
  const noop = vi.fn();
  return (
    <TrainingJobDashboard
      key={job.id}
      job={job}
      tab={tab}
      onTabChange={setTab}
      revision={0}
      busy={false}
      exporting={false}
      onCancel={noop}
      onResume={noop}
      onExport={noop}
      onRefresh={noop}
      onDelete={noop}
      onRename={noop}
      onUseParameters={noop}
      onTuneCheckpoint={noop}
      canUseParameters={false}
      trainingJobs={[]}
      onUseBestParameters={noop}
      onContinueTrial={noop}
      onApplyToJob={noop}
      onDownloadBestConfig={noop}
    />
  );
}
it('preserves selection through progress polling and restores loss when switching jobs', () => {
  const job: Job = {
    id: 'first',
    kind: 'train',
    status: 'running',
    config: {},
    progress: { history },
  };
  const component = render(<Dashboard job={job} />);
  choose('batch_size');
  component.rerender(<Dashboard job={{ ...job, progress: { history, duration: 100 } }} />);
  expect(screen.getByLabelText('Learning metric')).toHaveValue('batch_size');
  component.rerender(
    <Dashboard
      job={{
        ...job,
        progress: {
          history: [...history, { iteration: 1501, training_parameters_iteration: 1251 }],
        },
      }}
    />,
  );
  expect(screen.getByLabelText('Learning metric')).toHaveValue('batch_size');
  fireEvent.keyDown(graph(), { key: 'End' });
  expect(tooltip()).toHaveTextContent('Iteration 1501 · Training batch size: 128');
  component.rerender(<Dashboard job={{ ...job, id: 'second' }} />);
  expect(screen.getByLabelText('Learning metric')).toHaveValue('loss');
  expect(graph()).toHaveAccessibleName('Loss by iteration');
});

it('localizes registry labels, units, explanations, empty states and accessible descriptions', () => {
  for (const item of learningMetrics) {
    for (const source of [item.label, item.unit, item.description]) {
      expect(translate(source, {}, 'en')).toBe(source);
      expect(translate(source, {}, 'de'), source).not.toBe(source);
    }
  }
  setLanguage('de', false);
  render(view());
  expect(screen.getByLabelText('Lernmetrik')).toHaveValue('loss');
  fireEvent.change(screen.getByLabelText('Lernmetrik'), { target: { value: 'batch_size' } });
  expect(graph()).toHaveAccessibleName('Trainings-Batchgröße');
  expect(graph()).toHaveAttribute('aria-description', expect.stringContaining('Replay-Beispielen'));
  expect(formatLearningValue(undefined, 'de-DE')).toBe('—');
});

it('keeps timeline reconstruction before filtering a sparse resumed range', () => {
  const rows = learningChartRows(history);
  expect(recentHistory(rows, 100).every((row) => row.parameter_mcts_iterations === 256)).toBe(true);
});
