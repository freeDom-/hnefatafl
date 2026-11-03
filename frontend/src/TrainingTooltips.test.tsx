import { Profiler, useState } from 'react';
import {
  act,
  cleanup,
  createEvent,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import userEvent, { type UserEvent } from '@testing-library/user-event';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import EvaluationSettings from './EvaluationSettings';
import TrainingCreationPanel from './TrainingCreationPanel';
import TrainingResumeDialog, { type ResumeCheckpoint } from './TrainingResumeDialog';
import { trainingNumberFields, trainingParameterLabels } from './TrainingFields';
import { setLanguage, translate } from './i18n';
import { trainingChoices, trainingConfig, type TrainingConfig } from './trainingPresets';
import { RESUME_IMMUTABLE_FIELDS } from './trainingPayload';
import { trainingTooltip, trainingTooltips } from './trainingTooltips';
import type { DeviceInfo, Job, Ruleset } from './types';

/**
 * Every training parameter exposed by either dialog must carry a meaningful
 * tooltip. The coverage checks enumerate the fields the components actually
 * render (including conditional and advanced ones) and assert that each has a
 * non-empty description and a help button, so a new control without copy fails
 * here instead of shipping silently. The interaction checks drive the affordance
 * the way a browser does — real pointer movement, real focus — so a sequence that
 * only works with synthetic events cannot pass.
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
  training_symmetry_modes: ['off', 'random_d4'],
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

function CreationHarness({
  kind = 'train' as const,
  start = vi.fn(),
}: {
  kind?: 'train' | 'tune';
  start?: (event: { preventDefault: () => void }) => void;
}) {
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
      start={start}
      importingConfig={false}
      onImportConfig={vi.fn()}
    />
  );
}

/**
 * Gives the trigger and the tooltip realistic viewport rectangles. jsdom has no
 * layout, so positioning is only observable through these measurements.
 */
function mockRects(
  trigger: { left: number; top: number; width: number; height: number },
  tooltip: { width: number; height: number },
) {
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
    if (this.classList.contains('field-info-button'))
      return rect(trigger.left, trigger.top, trigger.width, trigger.height);
    if (this.classList.contains('field-tooltip')) return rect(0, 0, tooltip.width, tooltip.height);
    return rect(0, 0, 0, 0);
  });
}

/**
 * A controllable `visualViewport` for tests. jsdom has no visual viewport, so the
 * tooltip reads whatever this exposes. `resize` updates the reported size and
 * fires the captured listeners, which is how a pinch zoom reaches the tooltip.
 */
function mockVisualViewport(initial: {
  width: number;
  height: number;
  offsetLeft?: number;
  offsetTop?: number;
}) {
  const listeners = new Map<string, Set<() => void>>();
  const viewport = {
    width: initial.width,
    height: initial.height,
    offsetLeft: initial.offsetLeft ?? 0,
    offsetTop: initial.offsetTop ?? 0,
    scale: 1,
    addEventListener: (type: string, listener: () => void) => {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type)!.add(listener);
    },
    removeEventListener: (type: string, listener: () => void) => {
      listeners.get(type)?.delete(listener);
    },
  };
  Object.defineProperty(window, 'visualViewport', { configurable: true, value: viewport });
  return {
    viewport,
    resize(width: number, height: number, offsetLeft = 0, offsetTop = 0) {
      viewport.width = width;
      viewport.height = height;
      viewport.offsetLeft = offsetLeft;
      viewport.offsetTop = offsetTop;
      listeners.get('resize')?.forEach((listener) => listener());
    },
    scroll(offsetLeft = 0, offsetTop = 0) {
      viewport.offsetLeft = offsetLeft;
      viewport.offsetTop = offsetTop;
      listeners.get('scroll')?.forEach((listener) => listener());
    },
  };
}

/**
 * Gives the trigger and the tooltip realistic rectangles where the tooltip
 * actually wraps: its height grows as the applied max-width narrows. A test
 * cannot pass here with a fixed measurement that ignores the constraint, so the
 * assertions exercise the real width/height coordination.
 */
function mockWrappingTooltip(
  trigger: { left: number; top: number; width: number; height: number },
  intrinsic: { width: number; height: number },
) {
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
    if (this.classList.contains('field-info-button'))
      return rect(trigger.left, trigger.top, trigger.width, trigger.height);
    if (this.classList.contains('field-tooltip')) {
      const applied = parseFloat((this as HTMLElement).style.maxWidth);
      const width = Number.isFinite(applied) ? Math.min(intrinsic.width, applied) : intrinsic.width;
      const height = Math.round((intrinsic.height * intrinsic.width) / width);
      return rect(0, 0, width, height);
    }
    return rect(0, 0, 0, 0);
  });
}

/**
 * Models a description taller than the visual viewport. The applied max-height
 * clips the rendered box while `scrollHeight` still reports the full text, so the
 * component must turn the tooltip into a scrollable region. jsdom has no layout,
 * so the overflow is reported through these getters rather than real sizes.
 */
function mockOverflowingTooltip(
  trigger: { left: number; top: number; width: number; height: number },
  content: { width: number; height: number },
) {
  const rectSpy = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (
    this: Element,
  ) {
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
    if (this.classList.contains('field-info-button'))
      return rect(trigger.left, trigger.top, trigger.width, trigger.height);
    if (this.classList.contains('field-tooltip')) {
      const style = (this as HTMLElement).style;
      const appliedWidth = parseFloat(style.maxWidth);
      const appliedHeight = parseFloat(style.maxHeight);
      const width = Number.isFinite(appliedWidth)
        ? Math.min(content.width, appliedWidth)
        : content.width;
      const height = Number.isFinite(appliedHeight)
        ? Math.min(content.height, appliedHeight)
        : content.height;
      return rect(0, 0, width, height);
    }
    return rect(0, 0, 0, 0);
  });
  vi.spyOn(Element.prototype, 'scrollHeight', 'get').mockImplementation(function (this: Element) {
    return this.classList.contains('field-tooltip') ? content.height : 0;
  });
  vi.spyOn(Element.prototype, 'clientHeight', 'get').mockImplementation(function (this: Element) {
    if (!this.classList.contains('field-tooltip')) return 0;
    const appliedHeight = parseFloat((this as HTMLElement).style.maxHeight);
    return Number.isFinite(appliedHeight)
      ? Math.min(content.height, appliedHeight)
      : content.height;
  });
  return rectSpy;
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

/**
 * Asserts every read-only training parameter carries a description and a help
 * button. Editable controls are covered by `expectEveryFieldDocumented`; this is
 * the read-only counterpart, driven by the `data-training-parameter` marker on
 * `ReadOnlyFieldLabel`, so a value added to the checkpoint summary cannot ship
 * without an explanation. The set it returns is compared against the immutable
 * resume fields so a new immutable parameter is caught too.
 */
function expectEveryReadOnlyParameterDocumented(): Set<string> {
  const elements = Array.from(document.querySelectorAll<HTMLElement>('[data-training-parameter]'));
  const seen = new Set<string>();
  for (const element of elements) {
    const key = element.dataset.trainingParameter ?? '';
    expect(key).not.toBe('');
    seen.add(key);
    expect(trainingTooltip(key), `missing tooltip for ${key}`).toBeTruthy();
    expect(
      element.querySelectorAll('.field-info-button').length,
      `missing help button for ${key}`,
    ).toBe(1);
  }
  return seen;
}

const helpButton = (label: string) => screen.getByRole('button', { name: `Explain ${label}` });
const germanHelpButton = (label: string) =>
  screen.getByRole('button', { name: `${label} erklären` });
const combobox = (name: string) => screen.getByRole('combobox', { name });

/**
 * Reaches a control with the keyboard the way a user does. jsdom's DOM `focus()`
 * does not deliver a React focus event, so tabbing is the only faithful way to
 * exercise "focus opens the tooltip" — and it doubles as proof that the help
 * buttons are reachable by keyboard at all.
 */
async function tabTo(user: UserEvent, target: HTMLElement) {
  for (let step = 0; step < 100 && document.activeElement !== target; step += 1) await user.tab();
  expect(document.activeElement).toBe(target);
}

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
  Reflect.deleteProperty(window, 'visualViewport');
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

it('keeps shared descriptions true in both dialogs', () => {
  // The Viking-name suggestion sits next to the model name in the new-job form
  // only, so the shared description must not promise it.
  expect(trainingTooltips.model_name).not.toMatch(/Viking/);
  expect(trainingTooltips.model_name).toMatch(/1–100 characters/);
  // OpenVINO can serve evaluation through "Use training inference backend", so
  // the backend description must not claim evaluation always stays on PyTorch.
  expect(trainingTooltips.inference_backend).not.toMatch(/evaluation on the CPU/);
  expect(trainingTooltips.inference_backend).toMatch(
    /Evaluation uses PyTorch inference by default/,
  );
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

it('documents every immutable checkpoint parameter in the resume summary', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      json(checkpoint({ ...trainingConfig('brandubh', 'recommended'), model_name: 'Freydis' })),
    ),
  );
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  await screen.findByLabelText('Target total iterations');

  const summary = document.querySelector<HTMLElement>('.training-resume-details');
  expect(summary).not.toBeNull();
  // The definition list keeps its labels and values unchanged.
  expect(within(summary as HTMLElement).getByText('Completed iterations')).toBeInTheDocument();
  expect(within(summary as HTMLElement).getByText('5')).toBeInTheDocument();
  expect(within(summary as HTMLElement).getByText('Brandubh')).toBeInTheDocument();
  expect(within(summary as HTMLElement).getByText('32 channels · 2 blocks')).toBeInTheDocument();
  expect(within(summary as HTMLElement).getByText('42')).toBeInTheDocument();
  expect(within(summary as HTMLElement).getByText('AdamW')).toBeInTheDocument();

  // Every term other than the completed-iteration status is a parameter and
  // exposes at least one help button, so a new read-only value cannot slip in
  // without an explanation.
  for (const term of Array.from((summary as HTMLElement).querySelectorAll('dt'))) {
    const text = term.textContent?.trim() ?? '';
    if (text === 'Completed iterations') continue;
    expect(
      term.querySelectorAll('.field-info-button').length,
      `missing help for ${text}`,
    ).toBeGreaterThan(0);
  }

  // The five immutable checkpoint parameters resolve to the shared registry, and
  // every immutable field from the contract is represented except the editable
  // iteration target.
  const seen = expectEveryReadOnlyParameterDocumented();
  expect(seen).toEqual(new Set(RESUME_IMMUTABLE_FIELDS.filter((key) => key !== 'iterations')));
});

it('gives network channels and residual blocks separate explanations', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json(checkpoint(trainingConfig('brandubh', 'recommended')))),
  );
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  await screen.findByLabelText('Target total iterations');

  // The combined row exposes two independently named help buttons.
  expect(helpButton('Network channels')).toBeInTheDocument();
  expect(helpButton('Residual blocks')).toBeInTheDocument();

  fireEvent.click(helpButton('Network channels'), { detail: 1 });
  expect(screen.getByRole('tooltip')).toHaveTextContent(trainingTooltips.channels);
  fireEvent.click(helpButton('Network channels'), { detail: 1 });
  expect(screen.queryByRole('tooltip')).toBeNull();

  fireEvent.click(helpButton('Residual blocks'), { detail: 1 });
  const tooltip = screen.getByRole('tooltip');
  expect(tooltip).toHaveTextContent(trainingTooltips.blocks);
  expect(tooltip).not.toHaveTextContent(trainingTooltips.channels);
});

it('portals a read-only summary tooltip into the resume dialog', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json(checkpoint(trainingConfig('brandubh', 'recommended')))),
  );
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  const dialog = await screen.findByRole('dialog', { name: 'Resume training' });
  await screen.findByLabelText('Target total iterations');
  fireEvent.click(helpButton('Optimizer'), { detail: 1 });
  expect(within(dialog).getByRole('tooltip')).toHaveTextContent(trainingTooltips.optimizer);
});

it('translates the read-only summary help into German', async () => {
  setLanguage('de', false);
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json(checkpoint(trainingConfig('brandubh', 'recommended')))),
  );
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  await screen.findByRole('button', { name: 'Optimierer erklären' });
  fireEvent.click(germanHelpButton('Optimierer'), { detail: 1 });
  expect(screen.getByRole('tooltip')).toHaveTextContent(
    translate(trainingTooltips.optimizer, {}, 'de'),
  );
  expect(germanHelpButton('Netzwerkkanäle')).toBeInTheDocument();
  expect(germanHelpButton('Residualblöcke')).toBeInTheDocument();
});

it('does not disturb resume submission while a read-only summary tooltip is open', async () => {
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
  const iterations = await screen.findByLabelText('Target total iterations');
  expect(iterations).toHaveValue(20);
  fireEvent.click(helpButton('Optimizer'), { detail: 1 });
  expect(screen.getByRole('tooltip')).toHaveTextContent(trainingTooltips.optimizer);
  fireEvent.click(screen.getByRole('button', { name: 'Continue training' }));
  await waitFor(() => expect(onStarted).toHaveBeenCalled());
  // The read-only help changed nothing, so only the unchanged target is sent.
  expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({ iterations: 20 });
});

it('reveals the tooltip when hovering the information icon on its own', async () => {
  const user = userEvent.setup();
  render(<CreationHarness />);
  expect(screen.queryByRole('tooltip')).toBeNull();
  await user.hover(helpButton('Training iterations'));
  const tooltip = screen.getByRole('tooltip');
  expect(tooltip).toHaveTextContent(trainingTooltips.iterations);
  expect(tooltip).toHaveClass('field-tooltip');
  expect(helpButton('Training iterations')).toHaveAttribute(
    'aria-describedby',
    `${tooltip.id}-text`,
  );
});

it('keeps the tooltip open while the pointer moves between the label and its icon', async () => {
  const user = userEvent.setup();
  render(<CreationHarness />);
  const label = screen.getByText('Training iterations');
  const button = helpButton('Training iterations');
  await user.hover(label);
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
  await user.hover(button);
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
  await user.hover(label);
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
  // Leaving the group as a whole still hides a hovered tooltip.
  await user.unhover(label);
  expect(screen.queryByRole('tooltip')).toBeNull();
});

it('opens on keyboard focus, dismisses with Escape and reopens with the keyboard', async () => {
  const user = userEvent.setup();
  render(<CreationHarness />);
  const button = helpButton('Network channels');
  await tabTo(user, button);
  expect(screen.getByRole('tooltip')).toHaveTextContent(trainingTooltips.channels);
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('tooltip')).toBeNull();
  expect(button).toHaveFocus();
  // Enter and Space reopen it without tabbing away and back.
  await user.keyboard('{Enter}');
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
  await user.keyboard('{Enter}');
  expect(screen.queryByRole('tooltip')).toBeNull();
  await user.keyboard(' ');
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
  // Tabbing away hides it, and tabbing back reopens it without stale state.
  await user.tab();
  expect(screen.queryByRole('tooltip')).toBeNull();
  await user.tab({ shift: true });
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
});

it('closes on a second activation even though the icon keeps focus', async () => {
  const user = userEvent.setup();
  render(<CreationHarness />);
  const button = helpButton('Batch size');
  await user.click(button);
  expect(button).toHaveFocus();
  expect(screen.getByRole('tooltip')).toHaveTextContent(trainingTooltips.batch_size);
  await user.click(button);
  expect(button).toHaveFocus();
  expect(screen.queryByRole('tooltip')).toBeNull();
});

it('opens and closes a tap without any keyboard focus', () => {
  render(<CreationHarness />);
  const button = helpButton('Self-play games per iteration');
  // A touch tap that leaves the button unfocused still toggles the tooltip.
  fireEvent.click(button, { detail: 1 });
  expect(screen.getByRole('tooltip')).toHaveTextContent(trainingTooltips.games);
  fireEvent.click(button, { detail: 1 });
  expect(screen.queryByRole('tooltip')).toBeNull();
});

it('dismisses a pinned tooltip when pressing outside without blocking the control', async () => {
  const user = userEvent.setup();
  render(<CreationHarness />);
  const button = helpButton('Epochs per iteration');
  const input = screen.getByRole('spinbutton', { name: 'Epochs per iteration' });
  await user.click(button);
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
  await user.click(input);
  expect(screen.queryByRole('tooltip')).toBeNull();
  expect(input).toHaveFocus();
  await user.clear(input);
  await user.type(input, '4');
  expect(input).toHaveValue(4);
});

it('toggles a checkbox row tooltip by hover and by activation', async () => {
  const user = userEvent.setup();
  render(<CreationHarness />);
  const button = helpButton('Save every new best model');
  await user.hover(button);
  expect(screen.getByRole('tooltip')).toHaveTextContent(trainingTooltips.save_all_best);
  await user.unhover(button);
  expect(screen.queryByRole('tooltip')).toBeNull();
  await user.click(button);
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
  // Still hovered, so only the explicit dismissal can close it again.
  await user.click(button);
  expect(screen.queryByRole('tooltip')).toBeNull();
  // Hovering the checkbox label itself reveals the same description.
  await user.hover(screen.getByLabelText('Save every new best model'));
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
});

it('leaves the parameter value untouched while the tooltip is open', async () => {
  const user = userEvent.setup();
  render(<CreationHarness />);
  await user.click(helpButton('Training iterations'));
  const input = screen.getByRole('spinbutton', { name: 'Training iterations' });
  fireEvent.change(input, { target: { value: '17' } });
  expect(input).toHaveValue(17);
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
});

it('keeps the tooltip inside the viewport near a corner', () => {
  mockRects({ left: 1000, top: 700, width: 17, height: 17 }, { width: 300, height: 100 });
  render(<CreationHarness />);
  fireEvent.click(screen.getByRole('button', { name: /Advanced configuration/ }));
  fireEvent.click(helpButton('Search time limit (seconds)'), { detail: 1 });
  // The trigger sits in the bottom-right corner, so the tooltip flips above it
  // and is clamped to the right viewport edge (1024 × 768 in jsdom).
  expect(screen.getByRole('tooltip')).toHaveStyle({ top: '594px', left: '716px' });
});

it('clamps the tooltip when neither side has room for it', () => {
  vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(200);
  mockRects({ left: 100, top: 90, width: 17, height: 17 }, { width: 300, height: 150 });
  render(<CreationHarness />);
  fireEvent.click(helpButton('Training iterations'), { detail: 1 });
  // Below would start at 113 and above at -66, so the taller side wins and the
  // box is pulled back to 200 − 150 − 8 = 42 to stay fully visible.
  expect(screen.getByRole('tooltip')).toHaveStyle({ top: '42px', left: '8px' });
});

it('keeps a long German description inside a narrow viewport', () => {
  setLanguage('de', false);
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(320);
  vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(480);
  mockRects({ left: 300, top: 300, width: 17, height: 17 }, { width: 296, height: 260 });
  render(<CreationHarness />);
  fireEvent.click(germanHelpButton('Inferenzcache'), { detail: 1 });
  const tooltip = screen.getByRole('tooltip');
  expect(tooltip).toHaveTextContent('Verwendet neuronale Auswertungen identischer Stellungen');
  // 320 − 296 − 8 = 16 keeps it on screen, and it flips above the trigger.
  expect(tooltip).toHaveStyle({ left: '16px', top: '34px' });
});

it('keeps the tooltip inside a displaced visual viewport', () => {
  // A pinch zoom or a mobile keyboard can move the visible area away from the
  // layout-viewport origin; the tooltip is placed in layout coordinates, so the
  // offset has to be added rather than ignored.
  Object.defineProperty(window, 'visualViewport', {
    configurable: true,
    value: {
      offsetLeft: 100,
      offsetTop: 50,
      width: 400,
      height: 300,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    },
  });
  mockRects({ left: 700, top: 500, width: 17, height: 17 }, { width: 300, height: 100 });
  render(<CreationHarness />);
  fireEvent.click(helpButton('Training iterations'), { detail: 1 });
  // The visible area is 100–500 × 50–350, so the tooltip is pulled back to its
  // right edge and its bottom edge instead of the layout viewport's.
  expect(screen.getByRole('tooltip')).toHaveStyle({ left: '192px', top: '242px' });
});

it('narrows the tooltip to a visual viewport smaller than the layout viewport', () => {
  // A pinch zoom halves the visual viewport while 100vw still reports the layout
  // width, so a fixed CSS max-width would stay physically wider than the screen.
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(390);
  mockVisualViewport({ width: 195, height: 600 });
  mockWrappingTooltip({ left: 100, top: 100, width: 17, height: 17 }, { width: 300, height: 100 });
  render(<CreationHarness />);
  fireEvent.click(helpButton('Training iterations'), { detail: 1 });
  const tooltip = screen.getByRole('tooltip');
  // 195 − 2 × 8 = 179, not the normal 300px cap.
  expect(tooltip.style.maxWidth).toBe('179px');
  const left = parseFloat(tooltip.style.left);
  const width = parseFloat(tooltip.style.maxWidth);
  expect(width).toBe(179);
  expect(left).toBeGreaterThanOrEqual(8);
  expect(left + width).toBeLessThanOrEqual(195 - 8);
});

it('keeps the normal 300px width when the visual viewport has room', () => {
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(1024);
  mockVisualViewport({ width: 1024, height: 768 });
  mockWrappingTooltip({ left: 100, top: 100, width: 17, height: 17 }, { width: 300, height: 100 });
  render(<CreationHarness />);
  fireEvent.click(helpButton('Training iterations'), { detail: 1 });
  expect(screen.getByRole('tooltip').style.maxWidth).toBe('300px');
});

it('narrows the tooltip below 300px when the visual viewport is narrow', () => {
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(280);
  mockVisualViewport({ width: 280, height: 600 });
  mockWrappingTooltip({ left: 100, top: 100, width: 17, height: 17 }, { width: 300, height: 100 });
  render(<CreationHarness />);
  fireEvent.click(helpButton('Training iterations'), { detail: 1 });
  // 280 − 2 × 8 = 264.
  expect(screen.getByRole('tooltip').style.maxWidth).toBe('264px');
});

it('narrows and clamps a tooltip inside an offset visual viewport', () => {
  // The visible area is 100–295 × 50–350, so the tooltip must use its width and
  // the offset when clamping, not the layout viewport origin.
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(390);
  mockVisualViewport({ width: 195, height: 300, offsetLeft: 100, offsetTop: 50 });
  mockWrappingTooltip({ left: 700, top: 500, width: 17, height: 17 }, { width: 300, height: 100 });
  render(<CreationHarness />);
  fireEvent.click(helpButton('Training iterations'), { detail: 1 });
  const tooltip = screen.getByRole('tooltip');
  const left = parseFloat(tooltip.style.left);
  const top = parseFloat(tooltip.style.top);
  const width = parseFloat(tooltip.style.maxWidth);
  const height = Math.round((100 * 300) / width);
  expect(width).toBe(179);
  expect(left).toBeGreaterThanOrEqual(100 + 8);
  expect(left + width).toBeLessThanOrEqual(100 + 195 - 8);
  expect(top).toBeGreaterThanOrEqual(50 + 8);
  expect(top + height).toBeLessThanOrEqual(50 + 300 - 8);
});

it('uses the wrapped height when choosing a side', () => {
  // At 179px the 300×50 content wraps to about 84px tall, which no longer fits
  // below the trigger, so it flips above instead of overflowing.
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(390);
  mockVisualViewport({ width: 195, height: 300 });
  mockWrappingTooltip({ left: 100, top: 200, width: 17, height: 17 }, { width: 300, height: 50 });
  render(<CreationHarness />);
  fireEvent.click(helpButton('Training iterations'), { detail: 1 });
  // 200 − round(50 × 300 / 179) − 6 = 110.
  expect(screen.getByRole('tooltip')).toHaveStyle({ top: '110px' });
});

it('re-measures and narrows the tooltip when the visual viewport shrinks', () => {
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(390);
  const visual = mockVisualViewport({ width: 390, height: 600 });
  mockWrappingTooltip({ left: 100, top: 100, width: 17, height: 17 }, { width: 300, height: 100 });
  render(<CreationHarness />);
  fireEvent.click(helpButton('Training iterations'), { detail: 1 });
  expect(screen.getByRole('tooltip').style.maxWidth).toBe('300px');
  // A pinch zoom reports a smaller visual viewport; the open tooltip follows.
  act(() => visual.resize(195, 600));
  expect(screen.getByRole('tooltip').style.maxWidth).toBe('179px');
});

it('falls back to the layout width when there is no visual viewport', () => {
  Reflect.deleteProperty(window, 'visualViewport');
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(280);
  mockWrappingTooltip({ left: 100, top: 100, width: 17, height: 17 }, { width: 300, height: 100 });
  render(<CreationHarness />);
  fireEvent.click(helpButton('Training iterations'), { detail: 1 });
  expect(screen.getByRole('tooltip').style.maxWidth).toBe('264px');
});

it('turns a description taller than the viewport into a scrollable region', () => {
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(390);
  mockVisualViewport({ width: 390, height: 200 });
  mockOverflowingTooltip(
    { left: 100, top: 80, width: 17, height: 17 },
    { width: 300, height: 500 },
  );
  render(<CreationHarness />);
  const button = helpButton('Training iterations');
  fireEvent.click(button, { detail: 1 });
  const panel = screen.getByRole('region', { name: 'Training iterations' });
  expect(panel).toHaveClass('field-tooltip', 'field-tooltip-scroll');
  expect(panel).toHaveAttribute('tabindex', '0');
  // 200 − 2 × 8 = 184, and the full text is still present in the panel.
  expect(panel.style.maxHeight).toBe('184px');
  expect(panel).toHaveTextContent(trainingTooltips.iterations);
  expect(button).toHaveAttribute('aria-describedby', `${panel.id}-text`);
});

it('keeps the tooltip role and no tab stop for a description that fits', () => {
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(390);
  mockVisualViewport({ width: 390, height: 600 });
  mockOverflowingTooltip(
    { left: 100, top: 80, width: 17, height: 17 },
    { width: 300, height: 120 },
  );
  render(<CreationHarness />);
  fireEvent.click(helpButton('Training iterations'), { detail: 1 });
  const tooltip = screen.getByRole('tooltip');
  expect(tooltip).not.toHaveClass('field-tooltip-scroll');
  expect(tooltip).not.toHaveAttribute('tabindex');
  expect(tooltip.style.maxHeight).toBe('584px');
});

it('updates the presentation when the visual viewport grows', () => {
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(390);
  const visual = mockVisualViewport({ width: 390, height: 200 });
  mockOverflowingTooltip(
    { left: 100, top: 80, width: 17, height: 17 },
    { width: 300, height: 500 },
  );
  render(<CreationHarness />);
  fireEvent.click(helpButton('Training iterations'), { detail: 1 });
  expect(screen.getByRole('region', { name: 'Training iterations' })).toBeInTheDocument();
  // The taller viewport fits the whole description, so it is a tooltip again.
  act(() => visual.resize(390, 600));
  expect(screen.queryByRole('region')).toBeNull();
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
});

it('returns focus to the button when Escape closes the scroll panel', async () => {
  const user = userEvent.setup();
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(390);
  mockVisualViewport({ width: 390, height: 200 });
  mockOverflowingTooltip(
    { left: 100, top: 80, width: 17, height: 17 },
    { width: 300, height: 500 },
  );
  render(<CreationHarness />);
  const button = helpButton('Training iterations');
  await user.click(button);
  const panel = screen.getByRole('region', { name: 'Training iterations' });
  act(() => panel.focus());
  expect(panel).toHaveFocus();
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('region')).toBeNull();
  expect(button).toHaveFocus();
  // Returning focus must not immediately reopen the help the user dismissed.
  expect(screen.queryByRole('tooltip')).toBeNull();
});

it('keeps the scroll panel open while focus moves from the button into it', async () => {
  const user = userEvent.setup();
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(390);
  mockVisualViewport({ width: 390, height: 200 });
  mockOverflowingTooltip(
    { left: 100, top: 80, width: 17, height: 17 },
    { width: 300, height: 500 },
  );
  render(<CreationHarness />);
  const button = helpButton('Training iterations');
  await tabTo(user, button);
  const panel = screen.getByRole('region', { name: 'Training iterations' });
  // The panel is the next tab stop after the button and focus moving into it
  // must not close the help.
  await user.tab();
  expect(panel).toHaveFocus();
  expect(screen.getByRole('region', { name: 'Training iterations' })).toBeInTheDocument();
  // Tabbing out of the panel closes the help and leaves the form usable.
  await user.tab();
  expect(screen.queryByRole('region')).toBeNull();
});

it('keeps the scroll panel open for a press inside it and closes on a press outside', () => {
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(390);
  mockVisualViewport({ width: 390, height: 200 });
  mockOverflowingTooltip(
    { left: 100, top: 80, width: 17, height: 17 },
    { width: 300, height: 500 },
  );
  render(<CreationHarness />);
  fireEvent.click(helpButton('Training iterations'), { detail: 1 });
  const panel = screen.getByRole('region', { name: 'Training iterations' });
  // Pressing the scrollbar or text is inside the help group, so it stays open.
  fireEvent.pointerDown(panel);
  expect(screen.getByRole('region', { name: 'Training iterations' })).toBeInTheDocument();
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole('region')).toBeNull();
});

it('removes the document listeners when the scroll panel is dismissed', () => {
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(390);
  mockVisualViewport({ width: 390, height: 200 });
  mockOverflowingTooltip(
    { left: 100, top: 80, width: 17, height: 17 },
    { width: 300, height: 500 },
  );
  render(<CreationHarness />);
  fireEvent.click(helpButton('Training iterations'), { detail: 1 });
  expect(screen.getByRole('region', { name: 'Training iterations' })).toBeInTheDocument();
  const first = createEvent.keyDown(document, { key: 'Escape' });
  fireEvent(document, first);
  expect(first.defaultPrevented).toBe(true);
  expect(screen.queryByRole('region')).toBeNull();
  expect(document.querySelector('.field-tooltip')).toBeNull();
  // With the help closed its Escape listener is gone, so the key is untouched.
  const second = createEvent.keyDown(document, { key: 'Escape' });
  fireEvent(document, second);
  expect(second.defaultPrevented).toBe(false);
});

it('does not re-measure when the scroll panel itself is scrolled', () => {
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(390);
  mockVisualViewport({ width: 390, height: 200 });
  const rect = mockOverflowingTooltip(
    { left: 100, top: 80, width: 17, height: 17 },
    { width: 300, height: 500 },
  );
  render(<CreationHarness />);
  fireEvent.click(helpButton('Training iterations'), { detail: 1 });
  const panel = screen.getByRole('region', { name: 'Training iterations' });
  rect.mockClear();
  // A scroll from inside the panel must not reposition or re-measure it.
  fireEvent.scroll(panel, { bubbles: true });
  expect(rect).not.toHaveBeenCalled();
  // A scroll on the surrounding page must still reposition it.
  fireEvent.scroll(document, { bubbles: true });
  expect(rect).toHaveBeenCalled();
});

it('does not re-render when a scroll leaves the position unchanged', () => {
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(390);
  mockVisualViewport({ width: 390, height: 200 });
  mockOverflowingTooltip(
    { left: 100, top: 80, width: 17, height: 17 },
    { width: 300, height: 500 },
  );
  let commits = 0;
  render(
    <Profiler
      id="help"
      onRender={() => {
        commits += 1;
      }}
    >
      <CreationHarness />
    </Profiler>,
  );
  fireEvent.click(helpButton('Training iterations'), { detail: 1 });
  expect(screen.getByRole('region', { name: 'Training iterations' })).toBeInTheDocument();
  const before = commits;
  // The mocked rects do not move, so setPosition keeps the same object and React
  // does not commit again.
  fireEvent.scroll(document, { bubbles: true });
  expect(commits).toBe(before);
});

it('repositions when the visual viewport scrolls', () => {
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(390);
  const visual = mockVisualViewport({ width: 390, height: 200 });
  const rect = mockOverflowingTooltip(
    { left: 100, top: 80, width: 17, height: 17 },
    { width: 300, height: 500 },
  );
  render(<CreationHarness />);
  fireEvent.click(helpButton('Training iterations'), { detail: 1 });
  rect.mockClear();
  act(() => visual.scroll(40, 20));
  expect(rect).toHaveBeenCalled();
});

it('stops listening for scroll once the help is dismissed', () => {
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(390);
  mockVisualViewport({ width: 390, height: 200 });
  const rect = mockOverflowingTooltip(
    { left: 100, top: 80, width: 17, height: 17 },
    { width: 300, height: 500 },
  );
  render(<CreationHarness />);
  fireEvent.click(helpButton('Training iterations'), { detail: 1 });
  expect(screen.getByRole('region', { name: 'Training iterations' })).toBeInTheDocument();
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(screen.queryByRole('region')).toBeNull();
  rect.mockClear();
  fireEvent.scroll(document, { bubbles: true });
  expect(rect).not.toHaveBeenCalled();
});

it('uses a block-level hover group so the portaled panel nests validly', () => {
  render(<CreationHarness />);
  const button = helpButton('Training iterations');
  const group = button.closest('.field-help');
  expect(group?.tagName).toBe('DIV');
  // The label and the button stay siblings, so the button never joins the
  // control's accessible name.
  expect(group?.querySelector('label')?.contains(button)).toBe(false);
  fireEvent.click(button, { detail: 1 });
  const panel = group?.querySelector('.field-tooltip');
  expect(panel).not.toBeNull();
  expect(panel?.parentElement).toBe(group);
  // The scrollable region has no nested interactive controls of its own.
  expect(panel?.querySelector('button, a, input, select, textarea')).toBeNull();
});

it('uses a block-level read-only hover group in the resume summary', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json(checkpoint(trainingConfig('brandubh', 'recommended')))),
  );
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  await screen.findByLabelText('Target total iterations');
  const group = document.querySelector('[data-training-parameter="ruleset"]');
  expect(group?.tagName).toBe('DIV');
  expect(group?.querySelector('.field-info-button')).not.toBeNull();
});

it('keeps parameter help available while evaluation controls are disabled', () => {
  render(
    <EvaluationSettings
      config={trainingConfig('brandubh', 'recommended')}
      update={vi.fn()}
      disabled
    />,
  );
  expect(screen.getByLabelText('Evaluation games (even)')).toBeDisabled();
  const button = helpButton('Evaluation games (even)');
  expect(button).toBeEnabled();
  fireEvent.click(button, { detail: 1 });
  expect(screen.getByRole('tooltip')).toHaveTextContent(trainingTooltips.evaluation_games);
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

it('dismisses the tooltip on Escape without cancelling the dialog', async () => {
  const user = userEvent.setup();
  const onClose = vi.fn();
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json(checkpoint(trainingConfig('brandubh', 'recommended')))),
  );
  render(<TrainingResumeDialog job={job} onClose={onClose} onStarted={vi.fn()} />);
  await screen.findByLabelText('Target total iterations');
  await user.click(helpButton('Target total iterations'));
  expect(screen.getByRole('tooltip')).toBeInTheDocument();
  // The first Escape is consumed by the tooltip, so the dialog stays open.
  const first = createEvent.keyDown(document, { key: 'Escape' });
  fireEvent(document, first);
  expect(first.defaultPrevented).toBe(true);
  expect(screen.queryByRole('tooltip')).toBeNull();
  expect(onClose).not.toHaveBeenCalled();
  // With no tooltip open the dialog's own Escape handling is untouched.
  const second = createEvent.keyDown(document, { key: 'Escape' });
  fireEvent(document, second);
  expect(second.defaultPrevented).toBe(false);
});

it('does not disturb starting a new training job', async () => {
  const user = userEvent.setup();
  const start = vi.fn((event: { preventDefault: () => void }) => event.preventDefault());
  render(<CreationHarness start={start} />);
  await user.click(helpButton('Training iterations'));
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Training iterations' }), {
    target: { value: '9' },
  });
  await user.click(screen.getByRole('button', { name: 'Start training' }));
  expect(start).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('spinbutton', { name: 'Training iterations' })).toHaveValue(9);
  // Submitting is a press outside the help group, so the tooltip closes itself.
  expect(screen.queryByRole('tooltip')).toBeNull();
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
  const user = userEvent.setup();
  render(<CreationHarness />);
  await user.hover(screen.getByText('Batch size'));
  const fromCreation = screen.getByRole('tooltip').textContent;
  cleanup();
  vi.stubGlobal(
    'fetch',
    vi.fn(() => json(checkpoint(trainingConfig('brandubh', 'recommended')))),
  );
  render(<TrainingResumeDialog job={job} onClose={vi.fn()} onStarted={vi.fn()} />);
  await screen.findByLabelText('Target total iterations');
  await user.hover(screen.getByText('Batch size'));
  await waitFor(() => expect(screen.getByRole('tooltip')).toHaveTextContent(fromCreation ?? ''));
  expect(fromCreation).toBe(trainingTooltips.batch_size);
});

it('translates the help affordance and its descriptions into German', () => {
  setLanguage('de', false);
  render(<CreationHarness />);
  fireEvent.click(germanHelpButton('Trainingsiterationen'), { detail: 1 });
  expect(screen.getByRole('tooltip')).toHaveTextContent(
    'Anzahl der Trainingsiterationen. Jede Iteration erzeugt Selbstspielpartien',
  );
  expect(screen.getByRole('button', { name: 'Trainingsiterationen erklären' })).toBeInTheDocument();
});
