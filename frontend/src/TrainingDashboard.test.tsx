import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Training from './Training';
import TrainingHistoryChart from './TrainingHistoryChart';
import TrainingPerformance from './TrainingPerformance';
import {
  evaluationGroups,
  trainingHistory,
  filterJobs,
  safeDiagnostics,
} from './trainingDashboardData';
import { workshopFixtures } from '../tests/workshop-fixtures';
import { setLanguage } from './i18n';
import type { Job } from './types';
const json = (value: unknown) => Promise.resolve({ ok: true, json: async () => value } as Response);
let jobs: Job[];
let fetcher: ReturnType<typeof vi.fn>;
beforeEach(() => {
  setLanguage('en', false);
  jobs = workshopFixtures();
  fetcher = vi.fn((path: string, init?: RequestInit) =>
    json(
      init?.method === 'POST'
        ? jobs[1]
        : path === '/api/training/jobs'
          ? jobs
          : path.endsWith('/device')
            ? { device: 'cpu' }
            : [],
    ),
  );
  vi.stubGlobal('fetch', fetcher);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  setLanguage('en', false);
});
function show() {
  render(<Training rulesets={[]} models={[]} onError={vi.fn()} onModelsChanged={vi.fn()} />);
}
async function loaded() {
  return screen.findByRole('tab', { name: 'Overview' });
}
it('selects an active job initially and preserves form state across disclosure and refresh', async () => {
  show();
  await loaded();
  expect(screen.getByRole('heading', { name: 'Eir · Tablut strategist' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Forge a new strategist' })).toHaveAttribute(
    'aria-expanded',
    'false',
  );
  fireEvent.click(screen.getByRole('button', { name: /New training/ }));
  fireEvent.change(screen.getByLabelText('Model name'), { target: { value: 'Unfinished draft' } });
  fireEvent.click(screen.getByRole('button', { name: 'Forge a new strategist' }));
  expect(screen.getByLabelText('Model name')).not.toBeVisible();
  jobs = [...jobs].reverse();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  await waitFor(() =>
    expect(fetcher.mock.calls.filter(([path]) => path === '/api/training/jobs')).toHaveLength(2),
  );
  fireEvent.click(screen.getByRole('button', { name: /New training/ }));
  expect(screen.getByLabelText('Model name')).toHaveValue('Unfinished draft');
  expect(screen.getByRole('heading', { name: 'Eir · Tablut strategist' })).toBeInTheDocument();
});
it('keeps the explicit job, tab and range during polling refresh and filtering', async () => {
  show();
  await loaded();
  fireEvent.click(screen.getByRole('button', { name: /Bjorn · Northern campaign/ }));
  fireEvent.click(screen.getByRole('tab', { name: 'Learning' }));
  fireEvent.change(screen.getByLabelText('Iteration range'), { target: { value: '10' } });
  fireEvent.change(screen.getByLabelText('Search jobs'), { target: { value: 'Legacy' } });
  expect(screen.getByRole('heading', { name: 'Bjorn · Northern campaign' })).toBeInTheDocument();
  expect(document.querySelectorAll('.jobs-list .job-row')).toHaveLength(1);
  fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'failed' } });
  expect(screen.getByText('No jobs match these filters.')).toBeInTheDocument();
  jobs = [jobs[1], jobs[0], ...jobs.slice(2)];
  fireEvent.click(screen.getByRole('button', { name: 'Refresh job' }));
  await waitFor(() =>
    expect(screen.getByRole('tab', { name: 'Learning' })).toHaveAttribute('aria-selected', 'true'),
  );
  expect(screen.getByLabelText('Iteration range')).toHaveValue('10');
  fireEvent.click(screen.getByText('View graph data'));
  expect(
    within(await screen.findByRole('table', { name: 'Training history data' })).getAllByRole('row'),
  ).toHaveLength(11);
});
it('supports arrow/Home/End tab navigation and lazy bounded diagnostics', async () => {
  show();
  await loaded();
  fireEvent.keyDown(screen.getByRole('tab', { name: 'Overview' }), { key: 'ArrowRight' });
  expect(screen.getByRole('tab', { name: 'Learning' })).toHaveFocus();
  fireEvent.keyDown(screen.getByRole('tab', { name: 'Learning' }), { key: 'End' });
  expect(screen.getByRole('tab', { name: 'Details' })).toHaveAttribute('aria-selected', 'true');
  const disclosure = screen.getByText('Training configuration').closest('details')!;
  expect(disclosure.querySelector('pre')).toBeNull();
  disclosure.open = true;
  fireEvent(disclosure, new Event('toggle'));
  await waitFor(() => expect(disclosure.querySelector('pre')).toHaveTextContent('model_name'));
  expect(screen.getByRole('button', { name: 'Stop training' })).toBeEnabled();
  fireEvent.keyDown(screen.getByRole('tab', { name: 'Details' }), { key: 'Home' });
  expect(screen.getByRole('tab', { name: 'Overview' })).toHaveFocus();
});
it('opens creation for an empty workshop and preserves tuning settings after starting', async () => {
  jobs = [];
  show();
  await screen.findByText('Your next opponent starts here.');
  expect(screen.getByRole('button', { name: 'Forge a new strategist' })).toHaveAttribute(
    'aria-expanded',
    'true',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Tune parameters' }));
  fireEvent.change(screen.getByLabelText('Study name'), { target: { value: 'northern-study' } });
  fireEvent.change(screen.getByLabelText('Trial budget'), { target: { value: '7' } });
  const created = { ...workshopFixtures()[1], id: 'study', kind: 'tune' };
  fetcher.mockImplementation((path: string, init?: RequestInit) =>
    json(init?.method === 'POST' ? created : path.endsWith('/jobs') ? jobs : []),
  );
  fireEvent.click(screen.getByRole('button', { name: /Start tuning/ }));
  await loaded();
  const post = fetcher.mock.calls.find(([, init]) => init?.method === 'POST');
  expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({
    kind: 'tune',
    trials: 7,
    study_name: 'northern-study',
  });
  expect(screen.getByRole('button', { name: 'Forge a new strategist' })).toHaveAttribute(
    'aria-expanded',
    'false',
  );
});
it('filters by associated model name and sorts active jobs without mutating records', () => {
  const original = jobs.map((job) => job.id);
  expect(filterJobs(jobs, '', 'all', 'active')[0].id).toBe('running');
  expect(jobs.map((job) => job.id)).toEqual(original);
  expect(
    filterJobs([{ ...jobs[0], model_id: 'renamed' }], 'Registered raven', 'all', 'active', [
      { id: 'renamed', name: 'Registered raven', ruleset: 'tablut' },
    ]),
  ).toHaveLength(1);
  expect(filterJobs(jobs, '', 'failed', 'active').map((job) => job.id)).toEqual(['failed']);
});
it('keeps sparse observations, effective settings and champion incumbent changes separate', () => {
  const rows = trainingHistory([
    {
      iteration: 1,
      evaluation_score: 0.5,
      evaluation: { opponent: 'jarl', context: { depth: 2 } },
    },
    { iteration: 2 },
    {
      iteration: 3,
      evaluation_score: 0.7,
      evaluation: { opponent: 'jarl', context: { depth: 3 } },
    },
    {
      iteration: 4,
      evaluation_score: 0.6,
      evaluation: { opponent: 'champion', champion_iteration: 1 },
    },
    {
      iteration: 5,
      evaluation_score: 0.6,
      evaluation: { opponent: 'champion', champion_iteration: 3 },
    },
  ]);
  expect(evaluationGroups(rows)).toHaveLength(4);
  expect(rows[1].evaluation_score).toBeUndefined();
  const view = render(<TrainingHistoryChart history={rows} view="evaluation" />);
  expect(screen.getAllByRole('img')).toHaveLength(4);
  expect(
    [...document.querySelectorAll('svg path')].every(
      (path) => !(path.getAttribute('d') ?? '').includes('L'),
    ),
  ).toBe(true);
  view.rerender(<TrainingHistoryChart history={rows} view="evaluation" selectable />);
  expect(document.querySelector('.series-context')).toHaveTextContent('Series 4');
});
it('omits dense point markers, provides exact keyboard values and does not invent missing loss', () => {
  const rows = Array.from({ length: 1000 }, (_, index) => ({
    iteration: index + 1,
    policy_loss: 1 / (index + 1),
  }));
  render(<TrainingHistoryChart history={rows} view="learning" />);
  expect(document.querySelectorAll('circle')).toHaveLength(0);
  const chart = screen.getByRole('img');
  fireEvent.keyDown(chart, { key: 'End' });
  expect(screen.getByRole('status')).toHaveTextContent(
    'Iteration 1000 · Policy loss: 0.001 · Value loss: —',
  );
});
it('keeps overlapping activity counters outside the phase comparison and reports units', () => {
  render(<TrainingPerformance progress={jobs[1].progress} />);
  const phases = screen.getByLabelText('Sequential phase timings');
  expect(phases).not.toHaveTextContent('600');
  expect(phases).toHaveTextContent('240 s');
  expect(screen.getByText('Peak GPU allocated memory (MiB)').nextElementSibling).toHaveTextContent(
    '380',
  );
  expect(screen.getByText('Simulation completion ratio').nextElementSibling).toHaveTextContent(
    '97.5%',
  );
  expect(screen.getByText('Overlapping activity counters')).toBeInTheDocument();
});
it('redacts nested credentials in diagnostic projections', () => {
  expect(
    safeDiagnostics({ config: { worker_token: 'private', seed: 17 }, authorization: 'bearer' }),
  ).toEqual({ config: { worker_token: '[redacted]', seed: 17 }, authorization: '[redacted]' });
});
it('keeps an explicit selection safe when an older response completes later', async () => {
  const pending: ((response: Response) => void)[] = [];
  show();
  await loaded();
  fetcher.mockImplementation((path: string) =>
    path.endsWith('/jobs') ? new Promise<Response>((resolve) => pending.push(resolve)) : json([]),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  fireEvent.click(screen.getByRole('button', { name: /Legacy raven/ }));
  await act(async () => pending[1](await json(jobs)));
  await act(async () => pending[0](await json([jobs[1]])));
  expect(screen.getByRole('heading', { name: 'Legacy raven' })).toBeInTheDocument();
});
it('reports current trial iterations separately from a resumed study budget and retains trial results', async () => {
  const study: Job = {
    ...workshopFixtures()[1],
    id: 'study',
    kind: 'tune',
    config: {
      study_name: 'Northern search',
      trials: 3,
      training: { model_name: 'Tuning Eir', ruleset: 'tablut', iterations: 5 },
    },
    progress: {
      completed_trials: 10,
      trials_requested: 3,
      trial: 10,
      trial_progress: {
        iteration: 3,
        total_iterations: 5,
        history: [{ iteration: 3, policy_loss: 1.2, value_loss: 0.2 }],
      },
    },
  };
  jobs = [study];
  show();
  await loaded();
  expect(screen.getByRole('progressbar', { name: 'Training progress' })).toHaveAttribute(
    'value',
    '0.6',
  );
  expect(screen.getByText('Completed trials').nextElementSibling).toHaveTextContent('10');
  expect(screen.getByText('Additional trial budget').nextElementSibling).toHaveTextContent('3');
  jobs = [
    {
      ...study,
      status: 'completed',
      progress: {
        completed_trials: 11,
        best_score: 0.5,
        trials: [
          { number: 10, state: 'COMPLETE', score: 0.5, parameters: { learning_rate: 0.001 } },
          { number: 11, state: 'PRUNED', score: null, parameters: { learning_rate: 0.002 } },
        ],
      },
    },
  ];
  fireEvent.click(screen.getByRole('button', { name: 'Refresh job' }));
  await screen.findByRole('button', { name: 'Resume study' });
  fireEvent.click(screen.getByText('Trial history'));
  const table = await screen.findByRole('table', { name: 'Trial history' });
  expect(within(table).getAllByRole('row')).toHaveLength(3);
  expect(within(table).getByText('PRUNED').nextElementSibling).toHaveTextContent('—');
});
it('reports an initial load failure and allows Refresh to recover an empty workshop', async () => {
  fetcher.mockImplementation(() => Promise.reject(new Error('Server unavailable')));
  show();
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not load training jobs');
  fetcher.mockImplementation(() => json([]));
  fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
  expect(await screen.findByText('Your next opponent starts here.')).toBeVisible();
  expect(screen.queryByRole('alert')).toBeNull();
});
it('keeps small nonzero measurements readable in exact chart values', () => {
  render(
    <TrainingHistoryChart
      view="learning"
      history={[{ iteration: 1, policy_loss: 0.00000037, value_loss: 0.0000001 }]}
    />,
  );
  fireEvent.keyDown(screen.getByRole('img'), { key: 'End' });
  expect(screen.getByRole('status')).toHaveTextContent(
    'Policy loss: 0.00000037 · Value loss: 0.0000001',
  );
});
