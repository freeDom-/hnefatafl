import { useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import TrainingCreationPanel from './TrainingCreationPanel';
import TrainingResumeDialog, { type ResumeCheckpoint } from './TrainingResumeDialog';
import { trainingNumberFields, trainingParameterLabels } from './TrainingFields';
import { setLanguage, translate } from './i18n';
import { trainingChoices, trainingConfig, type TrainingConfig } from './trainingPresets';
import { trainingTooltip, trainingTooltips } from './trainingTooltips';
import type { DeviceInfo, Job, Ruleset } from './types';

/**
 * Every training parameter exposed by either dialog must carry a meaningful
 * tooltip. The coverage checks enumerate the fields the components actually
 * render (including conditional and advanced ones) and assert that each has a
 * non-empty description and a help button, so a new control without copy fails
 * here instead of shipping silently. The interaction checks cover mouse, keyboard
 * and touch, dismissal, positioning near a viewport edge and the guarantee that
 * the tooltips never change a value.
 */

const rulesets: Ruleset[] = [
  { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
  { id: 'tablut', name: 'Tablut', size: 9, description: '', rules_markdown: '' },
];

/** Reports OpenVINO support so the Intel-GPU-only settings become selectable. */
const openvinoDevice: DeviceInfo = {
  cuda_available: false,
  mps_available: false,
  inference_backends: ['pytorch', 'openvino'],
  openvino_devices: [{ id: 'GPU', name: 'Intel Arc', available: true }],
};

const job: Job = {
  id: 'source-job',
  kind: 'train',
  status: 'completed',
  config: { model_name: 'Freydis' },
};

function checkpoint(config: TrainingConfig): ResumeCheckpoint {
  return {
    job_id: job.id,
    completed_iteration: 5,
    ruleset: 'brandubh',
    channels: 32,
    blocks: 2,
    optimizer: 'adamw',
    learning_rate: 0.0007,
    weight_decay: 0.002,
    momentum: 0.9,
    model_name: 'Freydis',
    can_resume: true,
    config,
  };
}

const json = (value: unknown) => Promise.resolve({ ok: true, json: async () => value } as Response);

function CreationHarness({ kind = 'train' as const }) {
  const [config, setConfig] = useState<TrainingConfig>(() =>
    trainingConfig('tablut', 'recommended'),
  );
  const [activeKind, setActiveKind] = useState<'train' | 'tune'>(kind);
  const [target, setTarget] = useState('local');
  const [trials, setTrials] = useState(3);
  const [studySeed, setStudySeed] = useState(0);
  const [studyName, setStudyName] = useState('hnefatafl-search');
  const [space, setSpace] = useState('{}');
  const [advanced, setAdvanced] = useState(false);
  return (
    <TrainingCreationPanel
      config={config}
      setConfig={setConfig}
      update={(key, value) => setConfig((previous) => ({ ...previous, [key]: value }))}
      kind={activeKind}
      setKind={setActiveKind}
      target={target}
      setTarget={setTarget}
      trials={trials}
      setTrials={setTrials}
      studySeed={studySeed}
      setStudySeed={setStudySeed}
      studyName={studyName}
      setStudyName={setStudyName}
      space={space}
      setSpace={setSpace}
      advanced={advanced}
      setAdvanced={setAdvanced}
      busy={false}
      models={[]}
      rulesets={rulesets}
      workers={[]}
      device={openvinoDevice}
      start={vi.fn()}
      importingConfig={false}
      onImportConfig={vi.fn()}
    />
  );
}

/** Asserts every rendered training field has a description and a help button. */
function expectEveryFieldDocumented(): Set<string> {
  const elements = Array.from(document.querySelectorAll<HTMLElement>('[data-training-field]'));
  expect(elements.length).toBeGreaterThan(0);
  const seen = new Set<string>();
  for (const element of elements) {
    const key = element.dataset.trainingField ?? '';
    expect(key).not.toBe('');
    seen.add(key);
    expect(trainingTooltip(key), `missing tooltip for ${key}`).toBeTruthy();
    const container = element.closest('.training-field, .checkbox-field');
    expect(container, `no field container for ${key}`).not.toBeNull();
    expect(
      container?.querySelectorAll('.field-info-button').length,
      `missing help button for ${key}`,
    ).toBe(1);
  }
  return seen;
}

const helpButton = (label: string) => screen.getByRole('button', { name: `Explain ${label}` });
const combobox = (name: string) => screen.getByRole('combobox', { name });

beforeEach(() => {
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

it('documents every parameter the shared schemas expose', () => {
  for (const key of [
    ...Object.keys(trainingNumberFields),
    ...Object.keys(trainingChoices),
    ...Object.keys(trainingParameterLabels),
  ])
    expect(trainingTooltip(key), `missing tooltip for ${key}`).toBeTruthy();
  for (const [key, text] of Object.entries(trainingTooltips))
    expect(text.trim(), `empty tooltip for ${key}`).not.toBe('');
});

it('translates every description into German', () => {
  // A changed English description without a matching dictionary key would fall
  // back to English inside a German interface, so it fails here instead.
  for (const [key, text] of Object.entries(trainingTooltips))
    expect(translate(text, {}, 'de'), `untranslated tooltip for ${key}`).not.toBe(text);
});

it('documents every parameter the new-job form can show, including conditional and advanced ones', () => {
  render(<CreationHarness />);
  const seen = expectEveryFieldDocumented();

  // Conditional inference settings.
  fireEvent.change(combobox('Inference cache'), { target: { value: 'exact' } });
  fireEvent.change(combobox('Neural inference backend'), { target: { value: 'openvino' } });
  for (const key of expectEveryFieldDocumented()) seen.add(key);

  // Conditional evaluation opponents.
  fireEvent.change(combobox('Evaluation opponent'), { target: { value: 'jarl' } });
  for (const key of expectEveryFieldDocumented()) seen.add(key);
  fireEvent.change(combobox('Evaluation opponent'), { target: { value: 'champion' } });
  for (const key of expectEveryFieldDocumented()) seen.add(key);

  // Collapsed advanced section and its SGD-only momentum field.
  fireEvent.change(combobox('Evaluation opponent'), { target: { value: 'shield_bearer' } });
  fireEvent.click(screen.getByRole('button', { name: /Advanced configuration/ }));
  fireEvent.change(combobox('Optimizer'), { target: { value: 'sgd' } });
  for (const key of expectEveryFieldDocumented()) seen.add(key);

  // Tuning-only study controls.
  fireEvent.click(screen.getByRole('button', { name: 'Tune parameters' }));
  for (const key of expectEveryFieldDocumented()) seen.add(key);

  for (const key of [
    'target',
    'ruleset',
    'preset',
    'model_name',
    'device',
    'inference_backend',
    'inference_cache_mode',
    'inference_cache_capacity',
    'inference_device',
    'iterations',
    'channels',
    'blocks',
    'batch_size',
    'epochs',
    'learning_rate',
    'optimizer',
    'momentum',
    'training_symmetry_mode',
    'games',
    'mcts_iterations',
    'mcts_time_limit',
    'self_play_mode',
    'self_play_concurrency',
    'inference_batch_size',
    'exploration',
    'temperature',
    'dirichlet_alpha',
    'dirichlet_fraction',
    'replay_buffer_size',
    'weight_decay',
    'checkpoint_frequency',
    'max_game_plies',
    'cpu_threads',
    'search_workers',
    'seed',
    'mixed_precision',
    'save_all_best',
    'evaluation_opponent',
    'evaluation_mode',
    'evaluation_inference_backend',
    'evaluation_frequency',
    'evaluation_concurrency',
    'evaluation_batch_size',
    'evaluation_games',
    'evaluation_iterations',
    'evaluation_time_limit',
    'evaluation_max_plies',
    'evaluation_jarl_depth',
    'evaluation_jarl_time_limit',
    'champion_confidence',
    'champion_min_pairs',
    'champion_min_improvement',
    'study.trials',
    'study.study_name',
    'study.seed',
    'study.search_space',
  ])
    expect(seen, `form does not expose ${key}`).toContain(key);
});

it('documents every parameter the resume dialog can show', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      json(checkpoint({ ...trainingConfig('brandubh', 'recommended'), model_name: 'Freydis' })),
    ),
  );
  render(
    <TrainingResumeDialog
      job={job}
      localDevice={openvinoDevice}
      onClose={vi.fn()}
      onStarted={vi.fn()}
    />,
  );
  await screen.findByLabelText('Target total iterations');
  const seen = expectEveryFieldDocumented();

  fireEvent.change(combobox('Inference cache'), { target: { value: 'exact' } });
  fireEvent.change(combobox('Neural inference backend'), { target: { value: 'openvino' } });
  fireEvent.change(combobox('Evaluation opponent'), { target: { value: 'jarl' } });
  for (const key of expectEveryFieldDocumented()) seen.add(key);
  fireEvent.change(combobox('Evaluation opponent'), { target: { value: 'champion' } });
  for (const key of expectEveryFieldDocumented()) seen.add(key);

  for (const key of [
    'target',
    'inference_backend',
    'inference_cache_mode',
    'inference_cache_capacity',
    'inference_device',
    'training_symmetry_mode',
    'iterations',
    'model_name',
    'device',
    'self_play_mode',
    'games',
    'batch_size',
    'mcts_iterations',
    'self_play_concurrency',
    'inference_batch_size',
    'epochs',
    'learning_rate',
    'mcts_time_limit',
    'exploration',
    'temperature',
    'dirichlet_alpha',
    'dirichlet_fraction',
    'replay_buffer_size',
    'weight_decay',
    'momentum',
    'checkpoint_frequency',
    'max_game_plies',
    'cpu_threads',
    'search_workers',
    'mixed_precision',
    'save_all_best',
    'evaluation_opponent',
    'evaluation_mode',
    'evaluation_inference_backend',
    'evaluation_frequency',
    'evaluation_concurrency',
    'evaluation_batch_size',
    'evaluation_games',
    'evaluation_iterations',
    'evaluation_time_limit',
    'evaluation_max_plies',
    'evaluation_jarl_depth',
    'evaluation_jarl_time_limit',
    'champion_confidence',
    'champion_min_pairs',
    'champion_min_improvement',
  ])
    expect(seen, `resume dialog does not expose ${key}`).toContain(key);
});

it('reveals the tooltip on hover and hides it when the pointer leaves', () => {
  render(<CreationHarness />);
  expect(screen.queryByRole('tooltip')).toBeNull();
  const label = screen.getByText('Training iterations');
  fireEvent.mouseEnter(label);
  const tooltip = screen.getByRole('tooltip');
  expect(tooltip).toHaveTextContent(trainingTooltips.iterations);
  expect(tooltip).toHaveClass('field-tooltip');
  expect(helpButton('Training iterations')).toHaveAttribute('aria-describedby', tooltip.id);
  fireEvent.mouseLeave(label);
  expect(screen.queryByRole('tooltip')).toBeNull();
});

it('reveals the tooltip on keyboard focus and dismisses it with Escape', () => {
  render(<CreationHarness />);
  const button = helpButton('Network channels');
  fireEvent.focus(button);
  expect(screen.getByRole('tooltip')).toHaveTextContent(trainingTooltips.channels);
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(screen.queryByRole('tooltip')).toBeNull();
  fireEvent.focus(button);
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
  fireEvent.blur(button);
  expect(screen.queryByRole('tooltip')).toBeNull();
});

it('toggles the tooltip on tap and closes it on an outside press', () => {
  render(<CreationHarness />);
  const button = helpButton('Batch size');
  // A real tap reports a non-zero click detail; keyboard activation reports 0.
  fireEvent.click(button, { detail: 1 });
  expect(screen.getByRole('tooltip')).toHaveTextContent(trainingTooltips.batch_size);
  fireEvent.click(button, { detail: 1 });
  expect(screen.queryByRole('tooltip')).toBeNull();
  fireEvent.click(button, { detail: 1 });
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole('tooltip')).toBeNull();
});

it('does not treat keyboard activation as a tap', () => {
  render(<CreationHarness />);
  const button = helpButton('Epochs per iteration');
  fireEvent.focus(button);
  fireEvent.click(button, { detail: 0 });
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
});

it('leaves the parameter value untouched while the tooltip is open', () => {
  render(<CreationHarness />);
  fireEvent.click(helpButton('Training iterations'), { detail: 1 });
  const input = screen.getByRole('spinbutton', { name: 'Training iterations' });
  fireEvent.change(input, { target: { value: '17' } });
  expect(input).toHaveValue(17);
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
});

it('keeps the tooltip inside the viewport near a corner', () => {
  vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (this: Element) {
    const rect = (left: number, top: number, width: number, height: number) =>
      ({
        left,
        top,
        width,
        height,
        right: left + width,
        bottom: top + height,
        x: left,
        y: top,
        toJSON: () => ({}),
      }) as DOMRect;
    if (this.classList.contains('field-info-button')) return rect(1000, 700, 17, 17);
    if (this.classList.contains('field-tooltip')) return rect(0, 0, 300, 100);
    return rect(0, 0, 0, 0);
  });
  render(<CreationHarness />);
  fireEvent.click(screen.getByRole('button', { name: /Advanced configuration/ }));
  fireEvent.click(helpButton('Search time limit (seconds)'), { detail: 1 });
  // The trigger sits in the bottom-right corner, so the tooltip flips above it
  // and is clamped to the right viewport edge (1024 × 768 in jsdom).
  expect(screen.getByRole('tooltip')).toHaveStyle({ top: '594px', left: '716px' });
});

it('portals the resume tooltip into the dialog so a modal never clips it', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json(checkpoint(trainingConfig('brandubh', 'recommended')))),
  );
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  const dialog = await screen.findByRole('dialog', { name: 'Resume training' });
  await screen.findByLabelText('Target total iterations');
  fireEvent.click(helpButton('Target total iterations'), { detail: 1 });
  expect(within(dialog).getByRole('tooltip')).toHaveTextContent(trainingTooltips.iterations);
});

it('does not disturb resume prepopulation or the submitted changes', async () => {
  const next = { ...job, id: 'resumed', status: 'running' };
  const fetcher = vi.fn((_path: string, init?: RequestInit) =>
    json(
      init?.method === 'POST'
        ? next
        : checkpoint({ ...trainingConfig('brandubh', 'recommended'), model_name: 'Freydis' }),
    ),
  );
  vi.stubGlobal('fetch', fetcher);
  const onStarted = vi.fn();
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={onStarted} />);
  // The checkpoint is prepopulated before any tooltip is opened.
  const iterations = await screen.findByLabelText('Target total iterations');
  expect(iterations).toHaveValue(20);
  expect(screen.getByLabelText('Batch size')).toHaveValue(64);
  fireEvent.click(helpButton('Target total iterations'), { detail: 1 });
  fireEvent.click(helpButton('Batch size'), { detail: 1 });
  fireEvent.change(screen.getByLabelText('Batch size'), { target: { value: '17' } });
  fireEvent.click(screen.getByRole('button', { name: 'Continue training' }));
  await waitFor(() => expect(onStarted).toHaveBeenCalled());
  // Only the deliberate edit is sent; the tooltips changed nothing.
  expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({
    iterations: 20,
    batch_size: 17,
  });
});

it('uses one description for a parameter shared by both dialogs', async () => {
  render(<CreationHarness />);
  fireEvent.mouseEnter(screen.getByText('Batch size'));
  const fromCreation = screen.getByRole('tooltip').textContent;
  cleanup();
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json(checkpoint(trainingConfig('brandubh', 'recommended')))),
  );
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  await screen.findByLabelText('Target total iterations');
  fireEvent.mouseEnter(screen.getByText('Batch size'));
  await waitFor(() => expect(screen.getByRole('tooltip')).toHaveTextContent(fromCreation ?? ''));
  expect(fromCreation).toBe(trainingTooltips.batch_size);
});

it('translates the help affordance and its descriptions into German', () => {
  setLanguage('de', false);
  render(<CreationHarness />);
  fireEvent.mouseEnter(screen.getByText('Trainingsiterationen'));
  expect(screen.getByRole('tooltip')).toHaveTextContent(
    'Anzahl der Trainingsiterationen. Jede Iteration erzeugt Selbstspielpartien',
  );
  expect(screen.getByRole('button', { name: 'Trainingsiterationen erklären' })).toBeInTheDocument();
});
