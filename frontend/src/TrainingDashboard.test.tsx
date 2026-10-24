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
import { recordedJobParameters } from './trainingReuse';
import { trainingConfig } from './trainingPresets';
import { jobGameCounts } from './trainingGameCounts';
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
  render(
    <Training
      rulesets={[
        { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
        { id: 'tablut', name: 'Tablut', size: 9, description: '', rules_markdown: '' },
      ]}
      models={[]}
      onError={vi.fn()}
      onModelsChanged={vi.fn()}
    />,
  );
}

it.each(['completed', 'running', 'queued', 'cancelled', 'failed'])(
  'copies %s job settings, opens the form and starts only after explicit edits',
  async (status) => {
    jobs = [
      {
        id: 'original-job',
        kind: 'train',
        status,
        execution: 'remote',
        target_worker_id: 'unavailable',
        config: {
          ...trainingConfig('brandubh', 'smoke'),
          model_name: 'Recorded Freydis',
          seed: 0,
          blocks: 0,
          mixed_precision: false,
          weight_decay: 0,
          evaluation_games: 12,
          evaluation_opponent: 'jarl',
          evaluation_jarl_depth: 4,
          checkpoint_frequency: 3,
          inference_cache_mode: 'exact',
          inference_cache_capacity: 512,
          device: 'cuda',
          inference_backend: 'openvino',
          inference_device: 'GPU.9',
          resume: '/private/last.pt',
          output_dir: '/private',
          token: 'secret',
          internal_id: 'original',
        },
      },
    ];
    const original = structuredClone(jobs[0]);
    const scroll = vi.fn();
    const oldScroll = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = scroll;
    try {
      show();
      await loaded();
      fireEvent.click(screen.getByRole('button', { name: 'Use parameters' }));
      expect(screen.getByRole('button', { name: 'Forge a new strategist' })).toHaveAttribute(
        'aria-expanded',
        'true',
      );
      expect(screen.getByRole('button', { name: /Advanced configuration/ })).toHaveAttribute(
        'aria-expanded',
        'true',
      );
      expect(screen.getByLabelText('Model name')).toHaveValue('Recorded Freydis');
      expect(screen.getByLabelText('Random seed')).toHaveValue(0);
      expect(screen.getByLabelText('Residual blocks')).toHaveValue(0);
      expect(screen.getByLabelText('Weight regularization')).toHaveValue(0);
      expect(screen.getByLabelText('Mixed precision when supported')).not.toBeChecked();
      expect(screen.getByLabelText('Evaluation games (even)')).toHaveValue(12);
      expect(screen.getByLabelText('Jarl evaluation depth')).toHaveValue(4);
      expect(screen.getByLabelText('Checkpoint every (iterations)')).toHaveValue(3);
      expect(screen.getByLabelText('Training device')).toHaveValue('cpu');
      expect(screen.getByLabelText('Neural inference backend')).toHaveValue('pytorch');
      expect(screen.getByLabelText('Inference cache capacity')).toHaveValue(512);
      expect(screen.getByLabelText('Run training on')).toHaveValue('local');
      expect(screen.getByText(/Parameters copied from job Recorded Freydis/)).toHaveTextContent(
        'Invalid or unavailable',
      );
      expect(fetcher.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true);
      expect(jobs[0]).toEqual(original);
      await waitFor(() => expect(scroll).toHaveBeenCalled());
      fireEvent.change(screen.getByLabelText('Model name'), { target: { value: 'Editable copy' } });
      fireEvent.change(screen.getByLabelText('Random seed'), { target: { value: '23' } });
      fireEvent.submit(screen.getByRole('button', { name: /Start training/ }).closest('form')!);
      await waitFor(() =>
        expect(fetcher.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true),
      );
      const posted = JSON.parse(
        String(fetcher.mock.calls.find(([, init]) => init?.method === 'POST')![1]!.body),
      );
      expect(posted).toMatchObject({ model_name: 'Editable copy', seed: 23, kind: 'train' });
      for (const key of ['resume', 'output_dir', 'token', 'internal_id', 'worker_id'])
        expect(posted).not.toHaveProperty(key);
      expect(jobs[0]).toEqual(original);
    } finally {
      HTMLElement.prototype.scrollIntoView = oldScroll;
    }
  },
);

it('restores nested tuning inputs and study seed into a fresh independent study', async () => {
  const search = { learning_rate: { type: 'float', low: 0.0001, high: 0.003, log: true } };
  jobs = [
    {
      id: 'original-study',
      status: 'completed',
      kind: 'tune',
      config: {
        training: {
          ...trainingConfig('brandubh', 'smoke'),
          model_name: 'Tuned Freydis',
          seed: 123,
        },
        trials: 7,
        study_name: 'northern-study',
        seed: 0,
        search_space: search,
        storage: 'sqlite:////private/study.sqlite3',
        resume_study: 'previous',
      },
    },
  ];
  show();
  await loaded();
  fireEvent.click(screen.getByRole('button', { name: 'Use parameters' }));
  expect(screen.getByLabelText('Model name')).toHaveValue('Tuned Freydis');
  expect(screen.getByLabelText('Random seed')).toHaveValue(123);
  expect(screen.getByLabelText('Study random seed')).toHaveValue(0);
  expect(screen.getByLabelText('Trial budget')).toHaveValue(7);
  expect(screen.getByLabelText('Study name')).toHaveValue('northern-study');
  expect(
    JSON.parse(
      (screen.getByLabelText('Parameter search space (JSON)') as HTMLTextAreaElement).value,
    ),
  ).toEqual(search);
  fireEvent.submit(screen.getByRole('button', { name: /Start tuning/ }).closest('form')!);
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true),
  );
  const posted = JSON.parse(
    String(fetcher.mock.calls.find(([, init]) => init?.method === 'POST')![1]!.body),
  );
  expect(posted).toMatchObject({
    seed: 0,
    trials: 7,
    study_name: 'northern-study',
    search_space: search,
    training: { seed: 123 },
  });
  expect(posted).not.toHaveProperty('resume_study');
  expect(posted).not.toHaveProperty('storage');
});

it('validates partial legacy settings, unsupported values and malformed tuning search spaces', () => {
  const previous = trainingConfig('brandubh', 'smoke');
  const copy = recordedJobParameters(previous, {
    id: 'legacy',
    status: 'failed',
    config: {
      ruleset: 'brandubh',
      seed: 0,
      mixed_precision: false,
      blocks: -1,
      games: 0,
      evaluation_games: 3,
      learning_rate: Infinity,
      optimizer: 'unsafe',
      preset: 'missing',
    },
  })!;
  expect(copy.config).toMatchObject({
    seed: 0,
    mixed_precision: false,
    blocks: 0,
    games: 1,
    evaluation_games: 2,
  });
  expect(copy.warnings).toEqual(
    expect.arrayContaining([
      'blocks',
      'games',
      'evaluation_games',
      'learning_rate',
      'optimizer',
      'preset',
    ]),
  );
  const tune = recordedJobParameters(previous, {
    id: 'tune',
    status: 'failed',
    kind: 'tune',
    config: {
      training: { seed: 0 },
      trials: -1,
      seed: -1,
      search_space: { output_dir: { type: 'categorical', choices: ['/private'] } },
    },
  })!;
  expect(tune.search).toBeUndefined();
  expect(tune.warnings).toEqual(expect.arrayContaining(['search_space', 'trials', 'study_seed']));
  expect(
    recordedJobParameters(previous, { id: 'empty', status: 'completed', config: {} }),
  ).toBeNull();
});

it('keeps supported inference settings while excluding all execution references', () => {
  const previous = trainingConfig('brandubh', 'smoke');
  const loaded = recordedJobParameters(
    previous,
    {
      id: 'gpu',
      status: 'running',
      config: {
        ...previous,
        inference_backend: 'openvino',
        inference_device: 'GPU.0',
        device: 'cpu',
        lease_token: 'secret',
        checkpoint: 'token',
      },
    },
    {
      inference_backends: ['openvino'],
      openvino_devices: [{ id: 'GPU.0', name: 'Intel', available: true }],
    },
  )!;
  expect(loaded.config).toMatchObject({ inference_backend: 'openvino', inference_device: 'GPU.0' });
  expect(loaded.warnings).toEqual([]);
  expect(loaded.config).not.toHaveProperty('lease_token');
  expect(loaded.config).not.toHaveProperty('checkpoint');
});

it('renders locale-aware job-local and cumulative totals and keeps zero distinct from unknown', async () => {
  setLanguage('de', false);
  jobs = [
    {
      id: 'counts',
      status: 'running',
      config: { model_name: 'Counts', iterations: 200 },
      progress: {
        iteration: 125,
        games_generated: 6000,
        self_play_games: { version: 1, job: 2000, baseline: 4000, cumulative: 6000 },
      },
    },
  ];
  show();
  await screen.findByRole('tab', { name: 'Übersicht' });
  const counts = screen.getByLabelText('Erzeugte Selbstspielpartien');
  expect(counts).toHaveTextContent('Selbstspielpartien dieses Auftrags2.000');
  expect(counts).toHaveTextContent('Kumulative Trainingspartien6.000');
  expect(screen.getByRole('navigation', { name: 'Trainingsaufträge' })).toHaveTextContent(
    '2.000 Partien',
  );
  const base = { id: 'legacy', status: 'cancelled', config: { iterations: 200, games: 40 } };
  expect(jobGameCounts(base)).toEqual({ job: undefined, cumulative: undefined });
  expect(jobGameCounts({ ...base, progress: { games_generated: 0 } })).toEqual({
    job: 0,
    cumulative: 0,
  });
  expect(
    jobGameCounts({ ...base, config: { resume: 'opaque' }, progress: { games_generated: 6000 } }),
  ).toEqual({ job: undefined, cumulative: 6000 });
  expect(
    jobGameCounts({ ...base, kind: 'tune', progress: { trial_progress: jobs[0].progress } }),
  ).toEqual({});
  expect(
    jobGameCounts({
      ...base,
      result: { self_play_games: { version: 1, job: 2, baseline: null, cumulative: null } },
    }),
  ).toEqual({ job: 2, cumulative: undefined });
});
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
