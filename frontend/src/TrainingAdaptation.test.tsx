import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Training from './Training';
import TrainingResumeDialog, { type ResumeCheckpoint } from './TrainingResumeDialog';
import { TrainingLearningView } from './TrainingAnalysisViews';
import { setLanguage, translate } from './i18n';
import type { HistoryPoint } from './trainingDashboardData';
import {
  adaptationConfigError,
  boundedCandidates,
  candidateRates,
  trainingConfig,
  type TrainingConfig,
} from './trainingPresets';
import type { Job, Ruleset } from './types';

/**
 * The optional learning-rate controller is off by default, incompatible with
 * tuning, and its bounded decision history must render unambiguously. These are
 * behaviour tests over the real form and the real learning view.
 */

const rules: Ruleset[] = [
  { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
  { id: 'tablut', name: 'Tablut', size: 9, description: '', rules_markdown: '' },
];
const json = (value: unknown, ok = true) =>
  Promise.resolve({ ok, json: async () => value } as Response);

function harness() {
  const posts: Record<string, unknown>[] = [];
  const fetcher = vi.fn((path: string, init?: RequestInit) => {
    const base = path.split('?')[0];
    if (base === '/api/training/jobs') {
      if (init?.method === 'POST') {
        posts.push(JSON.parse(String(init.body)));
        return json({ id: 'new-job', status: 'running', config: {} });
      }
      return json([]);
    }
    if (path === '/api/training/workers') return json([]);
    if (path === '/api/training/device') return json({});
    return json([]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<Training rulesets={rules} models={[]} onError={vi.fn()} onModelsChanged={vi.fn()} />);
  return { posts };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  setLanguage('en', false);
});

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute('open', '');
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.removeAttribute('open');
  });
});

it('shows a Champion promotion reset with the preserved rate', async () => {
  renderLearning(
    {
      adaptation: { effective_learning_rate: 0.003, promotion_resets: 2 },
      adaptation_events: [
        {
          iteration: 8,
          observation: 0.9,
          previous_learning_rate: 0.003,
          selected_learning_rate: 0.003,
          preserved_learning_rate: 0.003,
          changed: false,
          skipped: 'promotion_reset',
          promotion_reset: true,
        },
      ],
    },
    [{ iteration: 8, learning_rate: 0.003, effective_learning_rate: 0.003 }],
    0,
  );
  expect(screen.getByText('Promotion resets')).toBeInTheDocument();
  fireEvent.click(screen.getByText('Automatic hyperparameter updates'));
  const table = await screen.findByRole('table', { name: 'Automatic hyperparameter updates' });
  expect(within(table).getByText('Champion promoted · warm-up restarted')).toBeInTheDocument();
  // The preserved rate is shown as both the used and the selected rate.
  expect(within(table).getAllByText('0.003').length).toBeGreaterThanOrEqual(2);
});

it('shows the active Champion and warm-up progress for a Champion controller', () => {
  renderLearning(
    {
      evaluation_selection: { opponent: 'champion', champion_iteration: 350 },
      adaptation: {
        algorithm: 'hypercontroller',
        effective_learning_rate: 0.0003,
        observations: 2,
        regime_observations: 2,
        warmup: 3,
        promotion_resets: 4,
        last_promotion: { preserved_learning_rate: 0.0003 },
      },
    },
    [{ iteration: 350, learning_rate: 0.0003, effective_learning_rate: 0.0003 }],
    0,
  );
  expect(screen.getByText('Current Champion')).toBeInTheDocument();
  expect(screen.getAllByText('350').length).toBeGreaterThan(0);
  expect(screen.getByText('Controller warm-up')).toBeInTheDocument();
  expect(screen.getByText('Warm-up (2 / 3 evaluations)')).toBeInTheDocument();
  expect(screen.getByText(/Rate kept at last promotion/)).toBeInTheDocument();
  // Once warm-up is satisfied the same KPI reports the active state.
  cleanup();
  renderLearning(
    {
      evaluation_selection: { opponent: 'champion', champion_iteration: 350 },
      adaptation: {
        algorithm: 'hypercontroller',
        observations: 3,
        regime_observations: 3,
        warmup: 3,
      },
    },
    [{ iteration: 350, learning_rate: 0.0003, effective_learning_rate: 0.0003 }],
    0,
  );
  expect(screen.getByText('Active (3 / 3 evaluations)')).toBeInTheDocument();
});

it('does not show Champion controller KPIs for fixed-opponent or legacy jobs', () => {
  renderLearning({
    adaptation: {
      algorithm: 'hypercontroller',
      effective_learning_rate: 0.001,
      observations: 2,
      warmup: 3,
    },
  });
  expect(screen.queryByText('Current Champion')).toBeNull();
  expect(screen.queryByText('Controller warm-up')).toBeNull();
  // A Champion job without an enabled controller is not an adaptation monitor.
  cleanup();
  renderLearning({
    evaluation_selection: { opponent: 'champion', champion_iteration: 350 },
    adaptation: {},
  });
  expect(screen.queryByText('Current Champion')).toBeNull();
  expect(screen.queryByText('Controller warm-up')).toBeNull();
  cleanup();
  renderLearning({ adaptation: { effective_learning_rate: 0.001 } });
  expect(screen.queryByText('Current Champion')).toBeNull();
  expect(screen.queryByText('Controller warm-up')).toBeNull();
});

it('leaves automatic hyperparameter updates off by default', async () => {
  harness();
  await screen.findByText('Your next opponent starts here.');
  const select = screen.getByRole('combobox', { name: 'Automatic hyperparameter updates' });
  expect(select).toHaveValue('off');
  // The candidate and schedule controls only appear once it is enabled.
  expect(screen.queryByLabelText('Learning rate candidates')).toBeNull();
});

it('submits an enabled controller with its candidates', async () => {
  const { posts } = harness();
  await screen.findByText('Your next opponent starts here.');
  fireEvent.change(screen.getByRole('combobox', { name: 'Automatic hyperparameter updates' }), {
    target: { value: 'hypercontroller' },
  });
  fireEvent.click(screen.getByRole('button', { name: /Advanced configuration/ }));
  fireEvent.change(screen.getByLabelText('Learning rate candidates'), {
    target: { value: '0.0003,0.0007,0.002' },
  });
  fireEvent.change(screen.getByLabelText('Evaluation games (even)'), {
    target: { value: '6' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toMatchObject({
    adaptation: 'hypercontroller',
    adaptation_lr_candidates: '0.0003,0.0007,0.002',
    evaluation_games: 6,
  });
});

it('disables and omits the controller while tuning', async () => {
  const { posts } = harness();
  await screen.findByText('Your next opponent starts here.');
  fireEvent.change(screen.getByRole('combobox', { name: 'Automatic hyperparameter updates' }), {
    target: { value: 'hypercontroller' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Tune parameters' }));
  const select = screen.getByRole('combobox', { name: 'Automatic hyperparameter updates' });
  expect(select).toBeDisabled();
  expect(select).toHaveValue('off');
  expect(
    screen.getByText(/Unavailable while tuning: the study varies the learning rate/),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Start tuning' }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect((posts[0].training as Record<string, unknown>).adaptation).toBe('off');
});

it('submits the controller with a Champion evaluation opponent', async () => {
  const { posts } = harness();
  await screen.findByText('Your next opponent starts here.');
  fireEvent.change(screen.getByRole('combobox', { name: 'Automatic hyperparameter updates' }), {
    target: { value: 'hypercontroller' },
  });
  fireEvent.change(screen.getByRole('combobox', { name: 'Evaluation opponent' }), {
    target: { value: 'champion' },
  });
  fireEvent.change(screen.getByLabelText('Evaluation games (even)'), { target: { value: '6' } });
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toMatchObject({
    adaptation: 'hypercontroller',
    evaluation_opponent: 'champion',
    evaluation_games: 6,
  });
});

it('explains an uninformative controller evaluation budget before submitting', async () => {
  const { posts } = harness();
  await screen.findByText('Your next opponent starts here.');
  fireEvent.change(screen.getByRole('combobox', { name: 'Automatic hyperparameter updates' }), {
    target: { value: 'hypercontroller' },
  });
  fireEvent.change(screen.getByLabelText('Evaluation games (even)'), { target: { value: '2' } });
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('at least 4 evaluation games');
  expect(posts).toHaveLength(0);
});

it('blocks malformed learning-rate candidates before submitting', async () => {
  const { posts } = harness();
  await screen.findByText('Your next opponent starts here.');
  fireEvent.change(screen.getByRole('combobox', { name: 'Automatic hyperparameter updates' }), {
    target: { value: 'hypercontroller' },
  });
  fireEvent.change(screen.getByLabelText('Evaluation games (even)'), {
    target: { value: '6' },
  });
  fireEvent.click(screen.getByRole('button', { name: /Advanced configuration/ }));
  const candidates = screen.getByLabelText('Learning rate candidates');
  fireEvent.change(candidates, { target: { value: '0.001,garbage' } });
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('Learning rate candidates');
  expect(alert).toHaveTextContent(/decimal numbers/);
  expect(candidates).toHaveAttribute('aria-invalid', 'true');
  expect(posts).toHaveLength(0);
});

it('blocks an oversized learning-rate candidate list before submitting', async () => {
  const { posts } = harness();
  await screen.findByText('Your next opponent starts here.');
  fireEvent.change(screen.getByRole('combobox', { name: 'Automatic hyperparameter updates' }), {
    target: { value: 'hypercontroller' },
  });
  fireEvent.change(screen.getByLabelText('Evaluation games (even)'), {
    target: { value: '6' },
  });
  fireEvent.click(screen.getByRole('button', { name: /Advanced configuration/ }));
  const candidates = screen.getByLabelText('Learning rate candidates');
  fireEvent.change(candidates, { target: { value: '0.001,'.repeat(200) } });
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent(/at most 512 characters/);
  expect(posts).toHaveLength(0);
});

it('reports the cross-field controller constraints directly', () => {
  const base = trainingConfig('brandubh', 'recommended');
  expect(adaptationConfigError(base)).toBeNull();
  // Champion is a supported objective: each promotion restarts the controller's
  // opponent-specific state while the effective rate is preserved.
  expect(
    adaptationConfigError({
      ...base,
      adaptation: 'hypercontroller',
      evaluation_opponent: 'champion',
    }),
  ).toBeNull();
  expect(
    adaptationConfigError({ ...base, adaptation: 'hypercontroller', evaluation_games: 2 })?.message,
  ).toMatch(/at least 4 evaluation games/);
  expect(
    adaptationConfigError({
      ...base,
      adaptation: 'hypercontroller',
      adaptation_lr_candidates: '0.0003,0.001,0.003',
      learning_rate: 0.002,
    }),
  ).toMatchObject({ field: 'learning_rate' });
});

it('validates every learning-rate candidate like the backend', () => {
  const base = trainingConfig('brandubh', 'recommended');
  const enabled = {
    ...base,
    adaptation: 'hypercontroller',
    evaluation_opponent: 'shield_bearer',
    evaluation_games: 6,
    learning_rate: 0.001,
  };
  const error = (candidates: string, extra: Partial<TrainingConfig> = {}) =>
    adaptationConfigError({ ...enabled, adaptation_lr_candidates: candidates, ...extra });

  // Valid lists, including supported scientific notation and surrounding whitespace.
  expect(error('0.001,0.002,0.003')).toBeNull();
  expect(error(' 0.0003 , 1e-3 , 0.003 ')).toBeNull();
  // Digit-separating underscores are accepted exactly where Python's float() allows them.
  expect(error('0.0_003,0.001,0.003')).toBeNull();
  // Empty entries are skipped exactly like the backend, not treated as zero.
  expect(error('0.001,,0.003')).toBeNull();

  // Trailing garbage and partial numbers are rejected, never filtered away.
  for (const candidates of ['0.001,garbage', '0.001abc,0.003', '0.001,0.002.5.5', '_0.001,0.003']) {
    expect(error(candidates)).toMatchObject({ field: 'adaptation_lr_candidates' });
    expect(error(candidates)?.message).toMatch(/decimal numbers/);
  }

  // Non-finite values are rejected by the numeric syntax check.
  for (const candidates of ['0.001,NaN', '0.001,Infinity', '0.001,-Infinity']) {
    expect(error(candidates)).toMatchObject({ field: 'adaptation_lr_candidates' });
    expect(error(candidates)?.message).toMatch(/decimal numbers/);
  }

  // Out-of-range values are rejected after parsing.
  for (const candidates of ['0.001,0', '0.001,2', '0.001,1e-8']) {
    expect(error(candidates)).toMatchObject({ field: 'adaptation_lr_candidates' });
    expect(error(candidates)?.message).toMatch(/between 1e-7 and 1/);
  }

  // Duplicates collapse before the count check.
  expect(error('0.001,0.001')?.message).toMatch(/between 2 and 8 distinct values/);
  expect(error('0.001,0.001,0.003')).toBeNull();
  // A single (or empty) list is insufficient, and more than eight is rejected.
  expect(error('0.001,')).toMatchObject({ field: 'adaptation_lr_candidates' });
  expect(error('0.0001,0.0002,0.0003,0.0004,0.0005,0.0006,0.0007,0.0008,0.0009')?.message).toMatch(
    /between 2 and 8 distinct values/,
  );
  // Eight candidates with lag 3 produce 8**4 contexts, above the backend limit.
  expect(
    error('0.0001,0.0002,0.0003,0.0004,0.001,0.002,0.003,0.01', { adaptation_lag: 3 })?.message,
  ).toMatch(/too many controller contexts/);

  // The configured rate must be one of the parsed candidates.
  expect(error('0.0003,0.002,0.003', { learning_rate: 0.001 })).toMatchObject({
    field: 'learning_rate',
  });
  // An oversized transport is rejected before it is parsed on every render.
  expect(error('0.001,'.repeat(200))?.message).toMatch(/at most 512 characters/);
  expect(candidateRates('0.001,'.repeat(200))).toBeNull();
});

const eventsProgress = {
  adaptation: { effective_learning_rate: 0.003 },
  adaptation_events: [
    {
      iteration: 4,
      observation: 0.75,
      previous_learning_rate: 0.001,
      selected_learning_rate: 0.003,
      changed: true,
      skipped: null,
    },
    {
      iteration: 5,
      observation: null,
      previous_learning_rate: 0.003,
      selected_learning_rate: 0.003,
      changed: false,
      skipped: 'missing_observation',
    },
  ],
};

function renderLearning(
  progress: Record<string, unknown>,
  history: HistoryPoint[] = [
    { iteration: 4, learning_rate: 0.001, effective_learning_rate: 0.003 },
    { iteration: 5, learning_rate: 0.003, effective_learning_rate: 0.003 },
  ],
  range = 10,
) {
  return render(
    <TrainingLearningView progress={progress} history={history} config={{}} range={range} />,
  );
}

/** Iteration column of the opened decision log, in table order. */
function adaptationIterations() {
  const table = screen.getByRole('table', { name: 'Automatic hyperparameter updates' });
  return within(table)
    .getAllByRole('row')
    .slice(1)
    .map((row) => row.querySelector('th')!.textContent);
}

function adaptationEvent(iteration: number, changed = true) {
  return {
    iteration,
    observation: 0.5,
    previous_learning_rate: 0.001,
    selected_learning_rate: changed ? 0.002 : 0.001,
    changed,
    skipped: null,
  };
}

it('shows the effective rate and the used/selected decision history', async () => {
  renderLearning(eventsProgress);
  expect(screen.getByText('Effective learning rate')).toBeInTheDocument();
  expect(screen.getByText('0.003')).toBeInTheDocument();
  fireEvent.click(screen.getByText('Automatic hyperparameter updates'));
  expect(await screen.findByRole('table')).toBeInTheDocument();
  expect(screen.getByText('Used learning rate')).toBeInTheDocument();
  expect(screen.getByText('Selected learning rate')).toBeInTheDocument();
  expect(screen.getByText('Changed')).toBeInTheDocument();
  expect(screen.getByText('No observation')).toBeInTheDocument();
});

it('filters the decision history to the selected iteration window', async () => {
  const history = Array.from({ length: 300 }, (_, index) => ({ iteration: index + 1 }));
  const progress = {
    adaptation_events: [
      adaptationEvent(10),
      adaptationEvent(150, false),
      adaptationEvent(260),
      adaptationEvent(300),
    ],
  };
  const view = renderLearning(progress, history, 100);
  fireEvent.click(screen.getByText('Automatic hyperparameter updates'));
  await screen.findByRole('table', { name: 'Automatic hyperparameter updates' });
  // The window is [201, 300], anchored to the latest training iteration.
  expect(adaptationIterations()).toEqual(['260', '300']);
  view.rerender(
    <TrainingLearningView progress={progress} history={history} config={{}} range={250} />,
  );
  expect(adaptationIterations()).toEqual(['150', '260', '300']);
});

it('anchors the decision window to the latest training iteration, not the last event', async () => {
  const history = Array.from({ length: 300 }, (_, index) => ({ iteration: index + 1 }));
  // A trailing event beyond the recorded history must not move the window.
  renderLearning(
    { adaptation_events: [adaptationEvent(240), adaptationEvent(290), adaptationEvent(305)] },
    history,
    100,
  );
  fireEvent.click(screen.getByText('Automatic hyperparameter updates'));
  await screen.findByRole('table', { name: 'Automatic hyperparameter updates' });
  expect(adaptationIterations()).toEqual(['240', '290']);
});

it('keeps sparse decisions and resumed nonzero iterations inside the window', async () => {
  const resumed = Array.from({ length: 300 }, (_, index) => ({ iteration: 1001 + index }));
  const progress = {
    adaptation_events: [adaptationEvent(1005), adaptationEvent(1100), adaptationEvent(1250)],
  };
  renderLearning(progress, resumed, 100);
  fireEvent.click(screen.getByText('Automatic hyperparameter updates'));
  await screen.findByRole('table', { name: 'Automatic hyperparameter updates' });
  // The window is [1201, 1300] for the resumed run.
  expect(adaptationIterations()).toEqual(['1250']);
});

it('restores the complete decision history for All iterations and reacts to range changes', async () => {
  const history = Array.from({ length: 300 }, (_, index) => ({ iteration: index + 1 }));
  const progress = {
    adaptation_events: [adaptationEvent(10), adaptationEvent(150), adaptationEvent(260)],
  };
  const view = renderLearning(progress, history, 100);
  fireEvent.click(screen.getByText('Automatic hyperparameter updates'));
  await screen.findByRole('table', { name: 'Automatic hyperparameter updates' });
  expect(adaptationIterations()).toEqual(['260']);
  view.rerender(
    <TrainingLearningView progress={progress} history={history} config={{}} range={0} />,
  );
  expect(adaptationIterations()).toEqual(['10', '150', '260']);
  view.rerender(
    <TrainingLearningView progress={progress} history={history} config={{}} range={250} />,
  );
  expect(adaptationIterations()).toEqual(['150', '260']);
  // A window that contains no decisions removes the disclosure entirely.
  view.rerender(
    <TrainingLearningView progress={progress} history={history} config={{}} range={10} />,
  );
  expect(screen.queryByText('Automatic hyperparameter updates')).toBeNull();
});

it('windows the Learning charts with the same boundaries as the decision log', async () => {
  const history = Array.from({ length: 300 }, (_, index) => ({
    iteration: index + 1,
    policy_loss: 1 / (index + 1),
  }));
  const progress = { adaptation_events: [adaptationEvent(150), adaptationEvent(260)] };
  const view = renderLearning(progress, history, 100);
  fireEvent.click(screen.getByText('Automatic hyperparameter updates'));
  await screen.findByRole('table', { name: 'Automatic hyperparameter updates' });
  expect(adaptationIterations()).toEqual(['260']);
  fireEvent.click(screen.getByText('View graph data'));
  // The loss chart receives the already-windowed history: 100 rows plus header.
  expect(
    within(await screen.findByRole('table', { name: 'Training history data' })).getAllByRole('row'),
  ).toHaveLength(101);
  view.rerender(
    <TrainingLearningView progress={progress} history={history} config={{}} range={0} />,
  );
  // All restores the complete chart and decision history together.
  expect(adaptationIterations()).toEqual(['150', '260']);
  expect(
    within(screen.getByRole('table', { name: 'Training history data' })).getAllByRole('row'),
  ).toHaveLength(301);
});

it('keeps malformed historical decisions under All and excludes them from numeric windows', async () => {
  const history = Array.from({ length: 300 }, (_, index) => ({ iteration: index + 1 }));
  const progress = {
    adaptation_events: [
      adaptationEvent(10),
      {
        observation: 0.4,
        previous_learning_rate: 0.001,
        selected_learning_rate: 0.002,
        changed: true,
      },
      {
        iteration: null,
        observation: 0.3,
        previous_learning_rate: 0.001,
        selected_learning_rate: 0.002,
        changed: false,
      },
      {
        iteration: 'invalid',
        observation: 0.2,
        previous_learning_rate: 0.001,
        selected_learning_rate: 0.002,
        changed: true,
      },
      adaptationEvent(260),
    ],
  };
  const view = renderLearning(progress, history, 0);
  fireEvent.click(screen.getByText('Automatic hyperparameter updates'));
  await screen.findByRole('table', { name: 'Automatic hyperparameter updates' });
  // All iterations keeps every object-shaped record, with — for the bad ones.
  expect(adaptationIterations()).toEqual(['10', '—', '—', '—', '260']);
  view.rerender(
    <TrainingLearningView progress={progress} history={history} config={{}} range={100} />,
  );
  // Numeric windows keep only valid records inside [201, 300].
  expect(adaptationIterations()).toEqual(['260']);
  view.rerender(
    <TrainingLearningView progress={progress} history={history} config={{}} range={250} />,
  );
  expect(adaptationIterations()).toEqual(['260']);
});

it('filters valid decisions at the exact numeric window boundaries', async () => {
  const history = Array.from({ length: 300 }, (_, index) => ({ iteration: index + 1 }));
  const progress = {
    adaptation_events: [50, 51, 250, 251, 300].map((iteration) => adaptationEvent(iteration)),
  };
  const view = renderLearning(progress, history, 250);
  fireEvent.click(screen.getByText('Automatic hyperparameter updates'));
  await screen.findByRole('table', { name: 'Automatic hyperparameter updates' });
  expect(adaptationIterations()).toEqual(['51', '250', '251', '300']);
  view.rerender(
    <TrainingLearningView progress={progress} history={history} config={{}} range={100} />,
  );
  expect(adaptationIterations()).toEqual(['250', '251', '300']);
});

it('handles empty and malformed adaptation event arrays safely', async () => {
  const history = [{ iteration: 1 }];
  const view = renderLearning({ adaptation_events: 'not-an-array' }, history, 0);
  expect(screen.queryByText('Automatic hyperparameter updates')).toBeNull();
  view.rerender(
    <TrainingLearningView
      progress={{ adaptation_events: [null, 42, 'x', { observation: 1 }] }}
      history={history}
      config={{}}
      range={0}
    />,
  );
  // Only the object-shaped record survives, and All keeps it without a window.
  fireEvent.click(screen.getByText('Automatic hyperparameter updates'));
  expect(
    await screen.findByRole('table', { name: 'Automatic hyperparameter updates' }),
  ).toBeInTheDocument();
  expect(adaptationIterations()).toEqual(['—']);
});

it('keeps closely spaced learning rates distinguishable in the UI', async () => {
  renderLearning({
    adaptation: { effective_learning_rate: 0.00012341 },
    adaptation_events: [
      {
        iteration: 4,
        observation: 0.5,
        previous_learning_rate: 0.00012341,
        selected_learning_rate: 0.00012342,
        changed: true,
        skipped: null,
      },
    ],
  });
  expect(screen.getByText('Effective learning rate')).toBeInTheDocument();
  expect(screen.getAllByText('1.2341E-4').length).toBeGreaterThan(0);
  fireEvent.click(screen.getByText('Automatic hyperparameter updates'));
  const table = await screen.findByRole('table');
  expect(within(table).getByText('1.2341E-4')).toBeInTheDocument();
  expect(within(table).getByText('1.2342E-4')).toBeInTheDocument();
});

it('distinguishes the closest supported candidates in the decision history', async () => {
  renderLearning({
    adaptation: { effective_learning_rate: 0.00012345678912 },
    adaptation_events: [
      {
        iteration: 4,
        observation: 0.5,
        previous_learning_rate: 0.00012345678912,
        selected_learning_rate: 0.00012345678913,
        changed: true,
        skipped: null,
      },
    ],
  });
  expect(screen.getAllByText('1.2345678912E-4').length).toBeGreaterThan(0);
  fireEvent.click(screen.getByText('Automatic hyperparameter updates'));
  const table = await screen.findByRole('table');
  expect(within(table).getByText('1.2345678912E-4')).toBeInTheDocument();
  expect(within(table).getByText('1.2345678913E-4')).toBeInTheDocument();
});

it('renders no decision history for a legacy job without events', () => {
  renderLearning({ adaptation: { effective_learning_rate: 0.001 } });
  expect(screen.queryByText('Automatic hyperparameter updates')).toBeNull();
  expect(screen.queryByRole('table')).toBeNull();
});

it('blocks resuming an incompatible controller configuration', async () => {
  const info: ResumeCheckpoint = {
    job_id: 'source',
    checkpoint: 'source:last.pt',
    completed_iteration: 5,
    ruleset: 'brandubh',
    channels: 32,
    blocks: 2,
    optimizer: 'adamw',
    learning_rate: 0.001,
    weight_decay: 0.0001,
    momentum: 0.9,
    model_name: 'Freydis',
    can_resume: true,
    config: {
      ...trainingConfig('brandubh', 'recommended'),
      adaptation: 'hypercontroller',
      adaptation_lr_candidates: '0.0003,0.001,0.003',
      learning_rate: 0.001,
      evaluation_opponent: 'champion',
      evaluation_games: 2,
    },
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json(info)),
  );
  const job: Job = { id: 'source', kind: 'train', status: 'completed', config: {} };
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  expect(await screen.findByText(/at least 4 evaluation games/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Continue training' })).toBeDisabled();
});

it('allows resuming a Champion controller configuration', async () => {
  const info: ResumeCheckpoint = {
    job_id: 'source',
    checkpoint: 'source:last.pt',
    completed_iteration: 5,
    ruleset: 'brandubh',
    channels: 32,
    blocks: 2,
    optimizer: 'adamw',
    learning_rate: 0.001,
    weight_decay: 0.0001,
    momentum: 0.9,
    model_name: 'Freydis',
    can_resume: true,
    config: {
      ...trainingConfig('brandubh', 'recommended'),
      adaptation: 'hypercontroller',
      adaptation_lr_candidates: '0.0003,0.001,0.003',
      learning_rate: 0.001,
      evaluation_opponent: 'champion',
      evaluation_games: 4,
    },
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json(info)),
  );
  const job: Job = { id: 'source', kind: 'train', status: 'completed', config: {} };
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  expect(await screen.findByRole('combobox', { name: 'Evaluation opponent' })).toHaveValue(
    'champion',
  );
  expect(screen.getByRole('button', { name: 'Continue training' })).toBeEnabled();
});

it('explains that a resume learning-rate change moves the controller rate', async () => {
  const info: ResumeCheckpoint = {
    job_id: 'source',
    checkpoint: 'source:last.pt',
    completed_iteration: 5,
    ruleset: 'brandubh',
    channels: 32,
    blocks: 2,
    optimizer: 'adamw',
    learning_rate: 0.001,
    weight_decay: 0.0001,
    momentum: 0.9,
    model_name: 'Freydis',
    can_resume: true,
    config: {
      ...trainingConfig('brandubh', 'recommended'),
      adaptation: 'hypercontroller',
      adaptation_lr_candidates: '0.0003,0.001,0.003',
      learning_rate: 0.001,
      evaluation_opponent: 'shield_bearer',
      evaluation_games: 4,
    },
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json(info)),
  );
  const job: Job = { id: 'source', kind: 'train', status: 'completed', config: {} };
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  expect(await screen.findByText(/moves the controller’s effective rate/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Continue training' })).toBeEnabled();
});

it('fills legacy checkpoint adaptation settings from the effective rate', async () => {
  const legacy: TrainingConfig = {
    ...trainingConfig('brandubh', 'recommended'),
    learning_rate: 0.002,
    evaluation_opponent: 'shield_bearer',
    evaluation_games: 4,
  };
  for (const key of [
    'adaptation',
    'adaptation_lr_candidates',
    'adaptation_cadence',
    'adaptation_warmup',
    'adaptation_min_dwell',
    'adaptation_lag',
    'adaptation_min_trials',
  ])
    delete legacy[key];
  const info: ResumeCheckpoint = {
    job_id: 'source',
    checkpoint: 'source:last.pt',
    completed_iteration: 5,
    ruleset: 'brandubh',
    channels: 32,
    blocks: 2,
    optimizer: 'adamw',
    learning_rate: 0.002,
    weight_decay: 0.0001,
    momentum: 0.9,
    model_name: 'Freydis',
    can_resume: true,
    config: legacy,
  };
  const next = { id: 'next', status: 'running', config: {} };
  const fetcher = vi.fn((_path: string, init?: RequestInit) =>
    json(init?.method === 'POST' ? next : info),
  );
  vi.stubGlobal('fetch', fetcher);
  const job: Job = { id: 'source', kind: 'train', status: 'completed', config: {} };
  const onStarted = vi.fn();
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={onStarted} />);
  const select = await screen.findByRole('combobox', { name: 'Automatic hyperparameter updates' });
  expect(select).toHaveValue('off');
  fireEvent.change(select, { target: { value: 'hypercontroller' } });
  // Candidates are derived from the checkpoint's effective optimizer rate, and
  // the remaining controller defaults are populated for the form.
  expect(screen.getByLabelText('Learning rate candidates')).toHaveValue('0.0006667,0.002,0.006');
  expect(screen.getByLabelText('Decide every (evaluations)')).toHaveValue(1);
  expect(screen.getByLabelText('Warm-up evaluations')).toHaveValue(2);
  fireEvent.click(screen.getByRole('button', { name: 'Continue training' }));
  await waitFor(() => expect(onStarted).toHaveBeenCalled());
  const sent = fetcher.mock.calls.find(([, init]) => init?.method === 'POST')!;
  expect(JSON.parse(String(sent[1]?.body))).toMatchObject({
    adaptation: 'hypercontroller',
    adaptation_lr_candidates: '0.0006667,0.002,0.006',
  });
});

it('preserves saved controller settings on resume', async () => {
  const info: ResumeCheckpoint = {
    job_id: 'source',
    checkpoint: 'source:last.pt',
    completed_iteration: 5,
    ruleset: 'brandubh',
    channels: 32,
    blocks: 2,
    optimizer: 'adamw',
    learning_rate: 0.001,
    weight_decay: 0.0001,
    momentum: 0.9,
    model_name: 'Freydis',
    can_resume: true,
    config: {
      ...trainingConfig('brandubh', 'recommended'),
      adaptation: 'hypercontroller',
      adaptation_lr_candidates: '0.0004,0.001,0.004',
      adaptation_cadence: 3,
      learning_rate: 0.001,
      evaluation_opponent: 'shield_bearer',
      evaluation_games: 4,
    },
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json(info)),
  );
  const job: Job = { id: 'source', kind: 'train', status: 'completed', config: {} };
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  expect(
    await screen.findByRole('combobox', { name: 'Automatic hyperparameter updates' }),
  ).toHaveValue('hypercontroller');
  expect(screen.getByLabelText('Learning rate candidates')).toHaveValue('0.0004,0.001,0.004');
  expect(screen.getByLabelText('Decide every (evaluations)')).toHaveValue(3);
});

/** Render the resume dialog for a checkpoint with the given controller settings. */
function resumeHarness(controller: Partial<TrainingConfig>, learningRate: number) {
  const info: ResumeCheckpoint = {
    job_id: 'source',
    checkpoint: 'source:last.pt',
    completed_iteration: 5,
    ruleset: 'brandubh',
    channels: 32,
    blocks: 2,
    optimizer: 'adamw',
    learning_rate: learningRate,
    weight_decay: 0.0001,
    momentum: 0.9,
    model_name: 'Freydis',
    can_resume: true,
    config: {
      ...trainingConfig('brandubh', 'recommended'),
      learning_rate: learningRate,
      evaluation_opponent: 'shield_bearer',
      evaluation_games: 4,
      ...controller,
    },
  };
  const next = { id: 'next', status: 'running', config: {} };
  const fetcher = vi.fn((_path: string, init?: RequestInit) =>
    json(init?.method === 'POST' ? next : info),
  );
  vi.stubGlobal('fetch', fetcher);
  const job: Job = { id: 'source', kind: 'train', status: 'completed', config: {} };
  const onStarted = vi.fn();
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={onStarted} />);
  return { fetcher, onStarted };
}

async function enableController() {
  const select = await screen.findByRole('combobox', { name: 'Automatic hyperparameter updates' });
  fireEvent.change(select, { target: { value: 'hypercontroller' } });
  return select;
}

async function submittedBody(fetcher: ReturnType<typeof vi.fn>) {
  const sent = fetcher.mock.calls.find(([, init]) => init?.method === 'POST')!;
  return JSON.parse(String(sent[1]?.body)) as Record<string, unknown>;
}

it('regenerates inactive default candidates that miss the effective rate', async () => {
  // Tablut's recommended rate is 0.0007, but the backend's inactive default is
  // 0.0003,0.001,0.003, which does not contain it.
  const { fetcher, onStarted } = resumeHarness(
    { adaptation: 'off', adaptation_lr_candidates: '0.0003,0.001,0.003' },
    0.0007,
  );
  await enableController();
  expect(screen.getByLabelText('Learning rate candidates')).toHaveValue('0.0002333,0.0007,0.0021');
  fireEvent.click(screen.getByRole('button', { name: 'Continue training' }));
  await waitFor(() => expect(onStarted).toHaveBeenCalled());
  expect(await submittedBody(fetcher)).toMatchObject({
    adaptation: 'hypercontroller',
    adaptation_lr_candidates: '0.0002333,0.0007,0.0021',
  });
});

it.each([
  [0.0007, '0.0002333,0.0007,0.0021'],
  [0.0005, '0.0001667,0.0005,0.0015'],
  [1e-7, '1e-7,3e-7'],
  [1, '0.3333,1'],
])('derives candidates around the effective rate %s', async (learningRate, expected) => {
  resumeHarness(
    { adaptation: 'off', adaptation_lr_candidates: '0.0003,0.001,0.003' },
    learningRate,
  );
  await enableController();
  expect(screen.getByLabelText('Learning rate candidates')).toHaveValue(expected);
});

it('preserves inactive candidates that already cover the effective rate', async () => {
  const { fetcher, onStarted } = resumeHarness(
    { adaptation: 'off', adaptation_lr_candidates: '0.0005,0.0007,0.002' },
    0.0007,
  );
  await enableController();
  expect(screen.getByLabelText('Learning rate candidates')).toHaveValue('0.0005,0.0007,0.002');
  fireEvent.click(screen.getByRole('button', { name: 'Continue training' }));
  await waitFor(() => expect(onStarted).toHaveBeenCalled());
  const body = await submittedBody(fetcher);
  expect(body.adaptation).toBe('hypercontroller');
  // The saved list already covers the rate, so it is inherited, not re-sent.
  expect(body.adaptation_lr_candidates).toBeUndefined();
});

it('does not regenerate candidates for an already adapted checkpoint', async () => {
  const { fetcher, onStarted } = resumeHarness(
    { adaptation: 'hypercontroller', adaptation_lr_candidates: '0.0005,0.0007,0.002' },
    0.0007,
  );
  expect(await screen.findByLabelText('Learning rate candidates')).toHaveValue(
    '0.0005,0.0007,0.002',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Continue training' }));
  await waitFor(() => expect(onStarted).toHaveBeenCalled());
  expect((await submittedBody(fetcher)).adaptation_lr_candidates).toBeUndefined();
});

it('sends no candidate override when the controller is switched back off', async () => {
  const { fetcher, onStarted } = resumeHarness(
    { adaptation: 'off', adaptation_lr_candidates: '0.0003,0.001,0.003' },
    0.0007,
  );
  const select = await enableController();
  fireEvent.change(select, { target: { value: 'off' } });
  fireEvent.click(screen.getByRole('button', { name: 'Continue training' }));
  await waitFor(() => expect(onStarted).toHaveBeenCalled());
  const body = await submittedBody(fetcher);
  expect(body.adaptation).toBeUndefined();
  expect(body.adaptation_lr_candidates).toBeUndefined();
});

it('generates distinct candidates at the supported rate boundaries', () => {
  expect(boundedCandidates(1)).toBe('0.3333,1');
  expect(boundedCandidates(1e-7)).toBe('1e-7,3e-7');
  // The exact starting rate is always a candidate, even when rounding would
  // otherwise move it.
  expect(boundedCandidates(0.00123456).split(',')).toContain('0.00123456');
});

it('translates the new controller labels and tooltips into German', () => {
  for (const source of [
    'Effective learning rate',
    'Used learning rate',
    'Selected learning rate',
    'Observation',
    'Decision',
    'Changed',
    'Kept',
    'Warm-up',
    'Waiting for cadence',
    'Minimum dwell',
    'No observation',
    'Fallback',
    'Champion promoted · warm-up restarted',
    'Promotion resets',
    'Current Champion',
    'Controller warm-up',
    'Warm-up ({observations} / {warmup} evaluations)',
    'Active ({observations} / {warmup} evaluations)',
    'Rate kept at last promotion',
    'Each Champion promotion keeps the current learning rate but restarts the controller’s warm-up; scores from before the promotion are not reused.',
    'Unavailable while tuning: the study varies the learning rate across trials.',
    'Learning rate candidates must be decimal numbers separated by commas.',
    'Learning rate candidates must be between 1e-7 and 1.',
    'Learning rate candidates must contain between 2 and 8 distinct values.',
    'Learning rate candidates must be at most 512 characters.',
    'Learning rate candidates and the observation history window produce too many controller contexts; use fewer candidates or a smaller window.',
    'This worker does not support automatic hyperparameter updates. Choose a newer worker or disable them.',
    'This worker does not support automatic hyperparameter updates with a Champion opponent. Choose a newer worker, a fixed opponent, or disable them.',
  ]) {
    expect(translate(source, {}, 'de'), `untranslated ${source}`).not.toBe(source);
  }
});
