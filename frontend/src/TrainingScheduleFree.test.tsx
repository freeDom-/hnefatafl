import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import Training from './Training';
import TrainingResumeDialog from './TrainingResumeDialog';
import TrainingOptimizerStatus from './TrainingOptimizerStatus';
import TrainingTarget, { workerEligible } from './TrainingTarget';
import { ConfigImportError, parseImportedConfig } from './trainingConfigImport';
import { adaptationConfigError, trainingConfig } from './trainingPresets';
import { resumeOverrides } from './trainingPayload';
import { setLanguage, translate } from './i18n';
import type { Job, Ruleset, TrainingWorker } from './types';

const rules: Ruleset[] = [
  { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
];
const base = {
  ...trainingConfig('brandubh', 'smoke'),
  optimizer: 'adamw_schedulefree',
  schedulefree_warmup_steps: 25,
};
const problem =
  'Schedule-Free AdamW with HyperController is not supported yet; set adaptation to off.';
const json = (value: unknown) => Promise.resolve({ ok: true, json: async () => value } as Response);

beforeEach(() => {
  setLanguage('en', false);
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.setAttribute('open', '');
  });
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.removeAttribute('open');
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  setLanguage('en', false);
});

it('selects Schedule-Free, conditionally shows bounded warm-up and explains the base rate in English and German', async () => {
  const posts: Record<string, unknown>[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        posts.push(JSON.parse(String(init.body)));
        return json({ id: 'new', status: 'running', config: {} });
      }
      return json(path === '/api/training/device' ? {} : []);
    }),
  );
  render(<Training rulesets={rules} models={[]} onError={vi.fn()} onModelsChanged={vi.fn()} />);
  const select = screen.getByRole('combobox', { name: 'Optimizer' });
  expect(select).toHaveValue('adamw');
  expect(screen.queryByLabelText('Schedule-Free warm-up steps')).not.toBeInTheDocument();
  fireEvent.change(select, { target: { value: 'adamw_schedulefree' } });
  const warmup = screen.getByRole('spinbutton', { name: 'Schedule-Free warm-up steps' });
  expect(warmup).toHaveValue(0);
  expect(warmup).toHaveAttribute('min', '0');
  expect(warmup).toHaveAttribute('max', '1000000');
  expect(screen.getByRole('spinbutton', { name: 'Base learning rate' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Explain Schedule-Free warm-up steps' }));
  expect(screen.getByRole('tooltip')).toHaveTextContent(
    'One training iteration can contain many optimizer updates',
  );
  fireEvent.change(warmup, { target: { value: '25' } });
  act(() => setLanguage('de', false));
  expect(screen.getByRole('spinbutton', { name: 'Schedule-Free-Aufwärmschritte' })).toHaveValue(25);
  expect(screen.getByLabelText('Basislernrate')).toBeVisible();
  act(() => setLanguage('en', false));
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toMatchObject({
    optimizer: 'adamw_schedulefree',
    schedulefree_warmup_steps: 25,
    adaptation: 'off',
  });
});

it('blocks HyperController in the new-training form with a clear validation message', async () => {
  const post = vi.fn();
  vi.stubGlobal(
    'fetch',
    vi.fn((_path: string, init?: RequestInit) => {
      if (init?.method === 'POST') post();
      return json([]);
    }),
  );
  render(<Training rulesets={rules} models={[]} onError={vi.fn()} onModelsChanged={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('Optimizer'), { target: { value: 'adamw_schedulefree' } });
  fireEvent.change(screen.getByLabelText('Automatic hyperparameter updates'), {
    target: { value: 'hypercontroller' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(problem);
  expect(post).not.toHaveBeenCalled();
});

it('marks an invalid imported warm-up field accessibly and preserves the current value', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json([])),
  );
  render(<Training rulesets={rules} models={[]} onError={vi.fn()} onModelsChanged={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('Optimizer'), { target: { value: 'adamw_schedulefree' } });
  fireEvent.change(screen.getByLabelText('Import config.json'), {
    target: {
      files: [
        new File([JSON.stringify({ schedulefree_warmup_steps: -1 })], 'config.json', {
          type: 'application/json',
        }),
      ],
    },
  });
  const alert = await screen.findByRole('alert');
  const field = screen.getByRole('spinbutton', { name: 'Schedule-Free warm-up steps' });
  expect(alert).toHaveTextContent('Schedule-Free warm-up steps');
  expect(field).toHaveAttribute('aria-invalid', 'true');
  expect(field).toHaveAttribute('aria-describedby', alert.id);
  expect(field).toHaveValue(0);
});

it('round-trips configurations, rejects malformed warm-up with its field, and rejects the unsupported combination', () => {
  expect(
    parseImportedConfig(JSON.parse(JSON.stringify(base)), trainingConfig('brandubh', 'smoke'), {
      mode: 'create',
    }).config,
  ).toEqual(base);
  for (const value of [-1, 1.5, 1000001, true, '25', null]) {
    try {
      parseImportedConfig({ ...base, schedulefree_warmup_steps: value }, base, { mode: 'create' });
      throw new Error('invalid warm-up was accepted');
    } catch (error) {
      expect(error).toBeInstanceOf(ConfigImportError);
      expect((error as ConfigImportError).fields).toEqual(['schedulefree_warmup_steps']);
    }
  }
  expect(() =>
    parseImportedConfig({ ...base, adaptation: 'hypercontroller' }, base, { mode: 'create' }),
  ).toThrow(problem);
  expect(() =>
    parseImportedConfig({ schedulefree_warmup_steps: 4 }, base, {
      mode: 'resume',
      immutable: { schedulefree_warmup_steps: 25 },
    }),
  ).toThrow('cannot change on resume');
  expect(adaptationConfigError({ ...base, adaptation: 'hypercontroller' })?.field).toBe(
    'adaptation',
  );
  expect(resumeOverrides({ ...base, schedulefree_warmup_steps: 4 }, base)).toEqual({});
});

it('shows the immutable optimizer and saved warm-up on resume, keeps rate overrides and rejects HyperController', async () => {
  const job: Job = { id: 'source', kind: 'train', status: 'completed', config: base };
  const fetcher = vi.fn((_path: string, init?: RequestInit) =>
    json(
      init?.method === 'POST'
        ? { ...job, id: 'continued' }
        : {
            job_id: job.id,
            can_resume: true,
            completed_iteration: 1,
            ruleset: 'brandubh',
            channels: 4,
            blocks: 0,
            optimizer: 'adamw_schedulefree',
            model_name: 'Schedule-Free',
            learning_rate: 0.001,
            config: base,
          },
    ),
  );
  vi.stubGlobal('fetch', fetcher);
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  await screen.findByRole('spinbutton', { name: 'Target total iterations' });
  expect(screen.getByText('Schedule-Free AdamW')).toBeInTheDocument();
  expect(screen.getByText('Schedule-Free warm-up steps')).toBeInTheDocument();
  expect(screen.queryByRole('combobox', { name: 'Optimizer' })).not.toBeInTheDocument();
  expect(
    screen.queryByRole('spinbutton', { name: 'Schedule-Free warm-up steps' }),
  ).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Automatic hyperparameter updates'), {
    target: { value: 'hypercontroller' },
  });
  expect(screen.getByRole('button', { name: 'Continue training' })).toBeDisabled();
  expect(screen.getByText(problem)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Automatic hyperparameter updates'), {
    target: { value: 'off' },
  });
  fireEvent.change(screen.getByLabelText('Base learning rate'), { target: { value: '0.004' } });
  fireEvent.click(screen.getByRole('button', { name: 'Continue training' }));
  await waitFor(() =>
    expect(fetcher.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true),
  );
  const [, init] = fetcher.mock.calls.find(([, init]) => init?.method === 'POST')!;
  expect(JSON.parse(String(init?.body))).toMatchObject({ iterations: 2, learning_rate: 0.004 });
  expect(JSON.parse(String(init?.body))).not.toHaveProperty('schedulefree_warmup_steps');
});

it('disables workers without the capability even when a version override allows their version', () => {
  const worker = {
    id: 'old',
    name: 'Old worker',
    status: 'online',
    active_jobs: 0,
    capacity: 1,
    capabilities: {},
    compatibility: { eligible: true },
    version_override_enabled: true,
  } as TrainingWorker;
  expect(workerEligible(worker, base)).toBe(false);
  render(<TrainingTarget value="worker:old" onChange={vi.fn()} workers={[worker]} config={base} />);
  expect(screen.getByRole('option', { name: /Old worker/ })).toBeDisabled();
  expect(screen.getByRole('status')).toHaveTextContent('does not support Schedule-Free AdamW');
  expect(workerEligible({ ...worker, capabilities: { schedule_free_adamw: true } }, base)).toBe(
    true,
  );
});

it('reports scalar warm-up diagnostics without labelling them as controller decisions', () => {
  render(
    <TrainingOptimizerStatus
      progress={{
        optimizer: {
          name: 'adamw_schedulefree',
          base_learning_rate: 0.004,
          warmup_learning_rate: 0.001,
          completed_steps: 25,
          warmup_steps: 100,
          warmup_progress: 0.25,
        },
      }}
    />,
  );
  const status = screen.getByLabelText('Schedule-Free AdamW');
  expect(within(status).getByText('0.004')).toBeInTheDocument();
  expect(within(status).getByText('0.001')).toBeInTheDocument();
  expect(within(status).getByText('25%')).toBeInTheDocument();
  expect(translate(problem, {}, 'de')).toContain('noch nicht unterstützt');
});
