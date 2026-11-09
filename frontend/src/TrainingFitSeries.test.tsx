import { useState } from 'react';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import TrainingHistoryChart from './TrainingHistoryChart';
import TrainingJobDashboard from './TrainingJobDashboard';
import { setLanguage } from './i18n';
import type { JobTab } from './jobTabs';
import type { Job } from './types';
import {
  evaluationGroups,
  evaluationIdentity,
  iterationRanges,
  latestEvaluationGroup,
  type HistoryPoint,
} from './trainingDashboardData';

beforeEach(() => setLanguage('en', false));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  setLanguage('en', false);
});

function observation(iteration: number, incumbent = 1200): HistoryPoint {
  return {
    iteration,
    evaluation: {
      opponent: 'champion',
      opponent_identity: 'champion:shared',
      context: { games: 12 },
      champion_iteration: incumbent,
      score: 0.5,
      candidate_as_attacker: { score: 0.25 },
      candidate_as_defender: { score: 0.75 },
      promoted: iteration === 1350,
    },
  };
}
function longHistory(): HistoryPoint[] {
  return [
    { iteration: 1, policy_loss: 2 },
    observation(10, 0),
    observation(500, 0),
    observation(501, 500),
    observation(1200, 500),
    observation(1201),
    observation(1250),
    { iteration: 1251, policy_loss: 1 },
    observation(1350),
    { iteration: 2500, policy_loss: 0.1 },
  ];
}
const jobWithHistory = (history = longHistory(), id = 'fit'): Job => ({
  id,
  kind: 'train',
  status: 'completed',
  config: { model_name: id, iterations: 2500 },
  progress: { iteration: 2500, history },
});
const noop = () => {};
function Dashboard({ job = jobWithHistory(), revision = 0 }: { job?: Job; revision?: number }) {
  const [tab, setTab] = useState<JobTab>('Evaluation');
  return (
    <TrainingJobDashboard
      key={job.id}
      job={job}
      tab={tab}
      onTabChange={setTab}
      revision={revision}
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
const chart = () => screen.getByRole('img', { name: 'Evaluation by iteration' });
const xTicks = () =>
  [...chart().querySelectorAll('text[y="206"]')].map((tick) => Number(tick.textContent));
const scoreMarkers = () => [...chart().querySelectorAll('circle[fill="var(--chart-evaluation)"]')];
const chooseRange = (value: string) =>
  fireEvent.change(screen.getByLabelText('Iteration range'), { target: { value } });
const chooseSeries = (value: string) =>
  fireEvent.change(screen.getByLabelText('Evaluation series'), { target: { value } });

it.each([
  { iterations: [100, 200, 300], identities: ['A', 'B', 'A'], latest: 'A' },
  { iterations: [100, 200, 300, 400], identities: ['A', 'B', 'C', 'B'], latest: 'B' },
  { iterations: [100, 200, 300], identities: ['A', 'A', 'A'], latest: 'A' },
  { iterations: [100, 150, 175, 200], identities: ['A', 'A', 'A', 'B'], latest: 'B' },
  { iterations: [1000, 1250, 1900], identities: ['A', 'B', 'A'], latest: 'A' },
  { iterations: [1900, 1500, 1000], identities: ['A', 'B', 'A'], latest: 'A' },
])(
  'resolves $identities by recorded iterations $iterations without reordering groups',
  ({ iterations, identities, latest }) => {
    // Legacy/imported groups can have unsorted points; their maximum iteration
    // determines recency, not their last array entry or number of observations.
    const groups = evaluationGroups(
      iterations.map((iteration, index) => ({
        iteration,
        opponent_identity: identities[index],
        evaluation_score: 0.5,
      })),
    );
    const original = structuredClone(groups);
    expect(latestEvaluationGroup(groups)?.points[0].opponent_identity).toBe(latest);
    expect(groups).toEqual(original);
  },
);

it('finds no latest group when observations have invalid iterations or scores', () => {
  expect(latestEvaluationGroup([])).toBeUndefined();
  expect(
    latestEvaluationGroup(
      evaluationGroups([
        { iteration: NaN, evaluation_score: 0.5 },
        { iteration: Infinity, evaluation_score: 0.5 },
        { iteration: 1500, evaluation_score: NaN },
        { iteration: 2000, evaluation_score: Infinity },
        { iteration: 2500 },
      ]),
    ),
  ).toBeUndefined();
});

it.each([false, true])(
  'defaults the Evaluation tab to a returning latest context with fit=%s',
  (fitSeries) => {
    render(
      <Dashboard
        job={jobWithHistory([
          { iteration: 900 },
          observation(1000, 0),
          observation(1500, 500),
          observation(1900, 0),
          { iteration: 2500 },
        ])}
      />,
    );
    if (fitSeries) chooseRange('fit-series');
    expect(screen.getByLabelText('Evaluation series')).toHaveValue('latest');
    expect(document.querySelector('.series-context')).toHaveTextContent('Series 1');
    expect(scoreMarkers().map((marker) => marker.querySelector('title')?.textContent)).toEqual([
      'Iteration 1000: 50%',
      'Iteration 1900: 50%',
    ]);
    expect(xTicks()).toEqual(fitSeries ? [1000, 1450, 1900] : [900, 1700, 2500]);
  },
);

it('fits the latest recorded series at 1201–1350 rather than the full 1–2500 history', async () => {
  const history = longHistory();
  const original = structuredClone(history);
  render(<Dashboard job={jobWithHistory(history)} />);
  expect(xTicks()).toEqual([1, 1251, 2500]);
  // A previous numeric window must not hide the series from Fit series.
  chooseRange('10');
  expect(screen.queryByLabelText('Evaluation series')).toBeNull();
  chooseRange('fit-series');
  expect(screen.getByLabelText('Evaluation series')).toHaveValue('latest');
  expect(xTicks()).toEqual([1201, 1276, 1350]);
  expect(scoreMarkers()).toHaveLength(3);
  expect(chart()).toHaveTextContent('0%');
  expect(chart()).toHaveTextContent('100%');
  expect(chart().querySelectorAll('.chart-promotion')).toHaveLength(1);
  expect(chart().querySelectorAll('circle')).toHaveLength(9);
  fireEvent.click(screen.getByText('View graph data'));
  const table = await screen.findByRole('table', { name: 'Training history data' });
  expect(within(table).getAllByRole('row')).toHaveLength(history.length + 1);
  expect(within(table).getByRole('rowheader', { name: '1' })).toBeInTheDocument();
  expect(within(table).getByRole('rowheader', { name: '2500' })).toBeInTheDocument();
  expect(history).toEqual(original);
});

it('refits individual incumbents, effective contexts and opponents without merging identities', () => {
  const changedContext = observation(1400);
  changedContext.evaluation!.context = { games: 24 };
  const changedOpponent = observation(1500);
  changedOpponent.evaluation!.opponent = 'jarl';
  render(<Dashboard job={jobWithHistory([...longHistory(), changedContext, changedOpponent])} />);
  chooseRange('fit-series');
  for (const [point, expected] of [
    [observation(501, 500), [501, 851, 1200]],
    [observation(1201), [1201, 1276, 1350]],
    [changedContext, [1400]],
    [changedOpponent, [1500]],
  ] as const) {
    chooseSeries(evaluationIdentity(point));
    expect(xTicks()).toEqual(expected);
    expect(screen.getByLabelText('Iteration range')).toHaveValue('fit-series');
    expect(document.querySelectorAll('.evaluation-series')).toHaveLength(1);
  }
});

it('fits All evaluation series to the full merged observation span', () => {
  render(<Dashboard />);
  chooseRange('fit-series');
  chooseSeries('all');
  expect(xTicks()).toEqual([10, 680, 1350]);
  expect(scoreMarkers()).toHaveLength(7);
  expect(document.querySelector('.series-context')).toHaveTextContent(
    'Merged evaluation series · 3 series',
  );
  expect(document.querySelectorAll('.evaluation-series')).toHaveLength(1);
  chooseRange('0');
  expect(xTicks()).toEqual([1, 1251, 2500]);
});

it('keeps resumed sparse legacy observations at their actual positions and preserves gaps', () => {
  render(
    <TrainingHistoryChart
      history={[
        { iteration: 1000 },
        { iteration: 1201, evaluation_score: 0.5 },
        { iteration: 1250, evaluation_score: 0.6 },
        { iteration: 1251 },
        { iteration: 1350, evaluation_score: 0.7 },
        { iteration: 2500 },
      ]}
      view="evaluation"
      fitSeries
      selectable
    />,
  );
  expect(screen.queryByLabelText('Evaluation series')).toBeNull();
  expect(xTicks()).toEqual([1201, 1276, 1350]);
  const positions = scoreMarkers().map((marker) => Number(marker.getAttribute('cx')));
  expect(positions[0]).toBe(58);
  expect(positions[1]).toBeCloseTo(58 + (49 / 149) * (520 - 58 - 16));
  expect(positions[2]).toBe(504);
  expect(chart().querySelector('path')!.getAttribute('d')).not.toContain('L');
});

it('keeps a single valid observation visible with finite coordinates and a nonzero span', () => {
  render(
    <TrainingHistoryChart
      history={[
        { iteration: 1000 },
        { iteration: 1201, evaluation_score: 0 },
        { iteration: 1250, evaluation_score: NaN },
        { iteration: 1300, evaluation_score: Infinity },
        { iteration: 1350, evaluation: { score: -Infinity } },
        { evaluation_score: 0.9 },
        { iteration: '1100', evaluation_score: 0.9 },
        { iteration: NaN, evaluation_score: 0.9 },
        { iteration: Infinity, evaluation_score: 0.9 },
        { iteration: 2500 },
      ]}
      view="evaluation"
      selectable
      fitSeries
    />,
  );
  expect(xTicks()).toEqual([1201]);
  expect(scoreMarkers()).toHaveLength(1);
  expect(Number(scoreMarkers()[0].getAttribute('cx'))).toBe(58);
  expect(Number(scoreMarkers()[0].getAttribute('cy'))).toBe(187);
  expect(chart().innerHTML).not.toMatch(/NaN|Infinity/);
  fireEvent.keyDown(chart(), { key: 'End' });
  expect(document.querySelector('.chart-tooltip')).toHaveTextContent('Iteration 1201');
});

it.each(
  [
    [],
    [{ iteration: 1000 }, { iteration: 2500 }],
    [
      { iteration: 1200, evaluation_score: NaN },
      { iteration: 1300, evaluation: { score: Infinity } },
    ],
    [
      { iteration: null, evaluation_score: 0.5 },
      { iteration: '1000', evaluation_score: 0.5 },
    ],
  ].map((history) => ({ history })),
)('preserves the existing empty state for history $history', ({ history }) => {
  const { container } = render(
    <TrainingHistoryChart history={history} view="evaluation" selectable fitSeries />,
  );
  expect(
    screen.getByText(
      history.some((row) => typeof row.iteration === 'number')
        ? 'No evaluation recorded yet.'
        : 'Graphs appear after the first completed training iteration.',
    ),
  ).toBeInTheDocument();
  expect(container.querySelectorAll('circle')).toHaveLength(0);
  expect(container.innerHTML).not.toMatch(/NaN|Infinity/);
});

it('retains pointer tooltips and keyboard inspection of observations and gaps in fit mode', () => {
  render(<Dashboard />);
  chooseRange('fit-series');
  const svg = chart();
  const tooltip = within(svg.closest('figure')!).getByRole('status');
  fireEvent.keyDown(svg, { key: 'Home' });
  expect(tooltip).toHaveTextContent(
    'Iteration 1201 · Completion-aware score: 50% · Attacker-side score: 25% · Defender-side score: 75%',
  );
  fireEvent.keyDown(svg, { key: 'ArrowRight' });
  expect(tooltip).toHaveTextContent('Iteration 1250');
  fireEvent.keyDown(svg, { key: 'ArrowRight' });
  expect(tooltip).toHaveTextContent('Iteration 1251 · Completion-aware score: —');
  fireEvent.keyDown(svg, { key: 'End' });
  expect(tooltip).toHaveTextContent('Iteration 1350');
  vi.stubGlobal('PointerEvent', MouseEvent);
  vi.spyOn(svg, 'getBoundingClientRect').mockReturnValue({ left: 0, width: 520 } as DOMRect);
  fireEvent.pointerMove(svg, { clientX: 58 });
  expect(tooltip).toHaveTextContent('Iteration 1201');
  fireEvent.pointerMove(svg, { clientX: 504 });
  expect(tooltip).toHaveTextContent('Iteration 1350');
  fireEvent.pointerLeave(svg);
  expect(tooltip).toHaveTextContent('Point to the chart or use arrow keys for exact values.');
});

it('keeps fit mode and series selection as monitoring updates extend the selected context', () => {
  const view = render(<Dashboard />);
  chooseRange('fit-series');
  chooseSeries(evaluationIdentity(observation(501, 500)));
  const reactivated = [...longHistory(), observation(1400, 500)];
  view.rerender(<Dashboard job={jobWithHistory(reactivated)} />);
  expect(screen.getByLabelText('Iteration range')).toHaveValue('fit-series');
  expect(screen.getByLabelText('Evaluation series')).toHaveValue(
    evaluationIdentity(observation(501, 500)),
  );
  expect(xTicks()).toEqual([501, 951, 1400]);
  chooseSeries('latest');
  expect(xTicks()).toEqual([501, 951, 1400]);
  view.rerender(<Dashboard job={jobWithHistory([...reactivated, observation(1450)])} />);
  expect(xTicks()).toEqual([1201, 1326, 1450]);
  expect(screen.getByLabelText('Iteration range')).toHaveValue('fit-series');
});

it.each([false, true])(
  'updates Latest on reactivation while preserving explicit and All selections with fit=%s',
  (fitSeries) => {
    const view = render(<Dashboard />);
    if (fitSeries) chooseRange('fit-series');
    let history = [...longHistory(), observation(1400, 500)];
    view.rerender(<Dashboard job={jobWithHistory(history)} />);
    expect(screen.getByLabelText('Evaluation series')).toHaveValue('latest');
    expect(document.querySelector('.series-context')).toHaveTextContent('Series 2');
    expect(xTicks()).toEqual(fitSeries ? [501, 951, 1400] : [1, 1251, 2500]);

    const explicit = evaluationIdentity(observation(10, 0));
    chooseSeries(explicit);
    history = [...history, observation(1450)];
    view.rerender(<Dashboard job={jobWithHistory(history)} />);
    expect(screen.getByLabelText('Evaluation series')).toHaveValue(explicit);
    expect(document.querySelector('.series-context')).toHaveTextContent('Series 1');
    expect(xTicks()).toEqual(fitSeries ? [10, 255, 500] : [1, 1251, 2500]);

    chooseSeries('all');
    history = [...history, observation(1600, 500)];
    view.rerender(<Dashboard job={jobWithHistory(history)} />);
    expect(screen.getByLabelText('Evaluation series')).toHaveValue('all');
    expect(document.querySelector('.series-context')).toHaveTextContent(
      'Merged evaluation series · 3 series',
    );
    expect(xTicks()).toEqual(fitSeries ? [10, 805, 1600] : [1, 1251, 2500]);
    expect(scoreMarkers()).toHaveLength(10);
    chooseSeries('latest');
    expect(document.querySelector('.series-context')).toHaveTextContent('Series 2');
    expect(xTicks()).toEqual(fitSeries ? [501, 1051, 1600] : [1, 1251, 2500]);
  },
);

it('keeps numeric windows in Learning and Performance and restores fit on returning to Evaluation', () => {
  render(<Dashboard />);
  chooseRange('250');
  chooseRange('fit-series');
  for (const tab of ['Learning', 'Performance']) {
    fireEvent.click(screen.getByRole('tab', { name: tab }));
    expect(screen.getByLabelText('Iteration range')).toHaveValue('250');
    expect(screen.queryByRole('option', { name: 'Fit series' })).toBeNull();
  }
  fireEvent.click(screen.getByRole('tab', { name: 'Evaluation' }));
  expect(screen.getByLabelText('Iteration range')).toHaveValue('fit-series');
  expect(xTicks()).toEqual([1201, 1276, 1350]);
  chooseRange('25');
  expect(screen.getByLabelText('Iteration range')).toHaveValue('25');
  fireEvent.click(screen.getByRole('tab', { name: 'Learning' }));
  expect(screen.getByLabelText('Iteration range')).toHaveValue('25');
});

it('resets fit mode on a new job or dashboard revision and translates the option in German', () => {
  const view = render(<Dashboard />);
  chooseRange('fit-series');
  chooseSeries('all');
  view.rerender(<Dashboard job={jobWithHistory(longHistory(), 'other')} />);
  expect(screen.getByLabelText('Iteration range')).toHaveValue('0');
  expect(screen.getByLabelText('Evaluation series')).toHaveValue('latest');
  chooseRange('fit-series');
  view.rerender(<Dashboard job={jobWithHistory(longHistory(), 'other')} revision={1} />);
  expect(screen.getByLabelText('Iteration range')).toHaveValue('0');
  act(() => setLanguage('de', false));
  expect(screen.getByRole('option', { name: 'Datenreihe einpassen' })).toHaveValue('fit-series');
});

it.each([0, ...iterationRanges])(
  'preserves the numeric range %i for resumed histories',
  (range) => {
    const history = Array.from({ length: 300 }, (_, index) => ({
      iteration: 1001 + index,
      evaluation_score: 0.5,
    }));
    render(<TrainingHistoryChart history={history} view="evaluation" range={range} selectable />);
    const ticks = xTicks();
    expect(ticks[0]).toBe(range ? 1301 - range : 1001);
    expect(ticks.at(-1)).toBe(1300);
  },
);
