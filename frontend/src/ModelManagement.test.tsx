import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Training from './Training';
import { NotificationProvider } from './notifications';
import ModelTrainingSummary from './ModelTrainingSummary';
import { recordedTrainingConfig, trainingConfig } from './trainingPresets';
import { setLanguage } from './i18n';
import type { Job, Model, Ruleset } from './types';

const rules: Ruleset[] = [
  { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
  { id: 'tablut', name: 'Tablut', size: 9, description: '', rules_markdown: '' },
];
const model: Model = {
  self_play_games: { version: 1, job: 2000, baseline: 4000, cumulative: 6000 },
  id: 'stable-id',
  name: 'Renamed Freydis',
  rune: 'ᚠ',
  ruleset: 'brandubh',
  iteration: 2,
  architecture: { name: 'tafl-policy-value-v1', channels: 24, blocks: 2 },
  training_config: {
    ...trainingConfig('brandubh', 'smoke'),
    channels: 24,
    blocks: 2,
    iterations: 9,
    games: 13,
    optimizer: 'sgd',
    learning_rate: 0.003,
    seed: 69,
    save_all_best: true,
    model_name: 'Original name',
    output_dir: '/private/outputs',
    resume: '/private/last.pt',
  },
  evaluation: { seed: 1000069 },
};

it('shows the checkpoint cumulative count separately from configured games in both languages', () => {
  const view = render(<ModelTrainingSummary model={model} />);
  expect(screen.getByText('Total self-play games').nextElementSibling).toHaveTextContent('6,000');
  expect(screen.getByText('Self-play').nextElementSibling).toHaveTextContent(
    '13 games / iteration',
  );
  act(() => setLanguage('de', false));
  expect(screen.getByText('Selbstspielpartien insgesamt').nextElementSibling).toHaveTextContent(
    '6.000',
  );
  view.rerender(<ModelTrainingSummary model={{ ...model, self_play_games: undefined }} />);
  expect(screen.getByText('Selbstspielpartien insgesamt').nextElementSibling).toHaveTextContent(
    'Nicht aufgezeichnet',
  );
  view.rerender(
    <ModelTrainingSummary
      model={{ ...model, self_play_games: { version: 1, job: 0, baseline: 0, cumulative: 0 } }}
    />,
  );
  expect(screen.getByText('Selbstspielpartien insgesamt').nextElementSibling).toHaveTextContent(
    /^0$/,
  );
});
const job: Job = {
  id: 'saved-job',
  status: 'completed',
  kind: 'train',
  config: { model_name: 'Original name' },
  checkpoint: 'saved-job:last.pt',
  model_id: model.id,
};
const json = (value: unknown, ok = true) =>
  Promise.resolve({ ok, json: async () => value } as Response);

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
  vi.clearAllMocks();
  setLanguage('en', false);
});
function show(jobs: Job[] = [], models = [model]) {
  const onModelsChanged = vi.fn();
  const onError = vi.fn();
  render(
    <NotificationProvider>
      <Training
        rulesets={rules}
        models={models}
        onModelsChanged={onModelsChanged}
        onError={onError}
      />
    </NotificationProvider>,
  );
  return { onModelsChanged, onError, jobs };
}

describe('model and training collection controls', () => {
  it('loads exact recorded budgets into the training form without starting work or copying paths', async () => {
    const fetcher = vi.fn((_path: string, _init?: RequestInit) => json([]));
    vi.stubGlobal('fetch', fetcher);
    show();
    fireEvent.click(screen.getByRole('button', { name: 'Tune parameters' }));
    fireEvent.click(
      screen.getByRole('button', { name: 'Load training parameters for Renamed Freydis' }),
    );
    expect(screen.getByLabelText('Model name', { exact: true })).toHaveValue(model.name);
    expect(screen.getByLabelText('Battlefield', { exact: true })).toHaveValue('brandubh');
    expect(screen.getByLabelText('Training preset', { exact: true })).toHaveValue('smoke');
    expect(screen.getByLabelText('Training iterations', { exact: true })).toHaveValue(9);
    expect(screen.getByLabelText('Self-play games per iteration', { exact: true })).toHaveValue(13);
    expect(screen.getByLabelText('Network channels', { exact: true })).toHaveValue(24);
    expect(screen.getByLabelText('Residual blocks', { exact: true })).toHaveValue(2);
    expect(screen.getByLabelText('Optimizer', { exact: true })).toHaveValue('sgd');
    expect(screen.getByLabelText('Learning rate', { exact: true })).toHaveValue(0.003);
    expect(screen.getByLabelText('Random seed', { exact: true })).toHaveValue(69);
    expect(screen.getByRole('checkbox', { name: 'Save every new best model' })).toBeChecked();
    expect(screen.queryByRole('spinbutton', { name: 'Trial budget' })).not.toBeInTheDocument();
    expect(document.querySelector('.notice')).toHaveTextContent(
      'Training parameters loaded from Renamed Freydis',
    );
    const loaded = recordedTrainingConfig(trainingConfig('tablut', 'recommended'), model)!;
    expect(loaded).not.toHaveProperty('output_dir');
    expect(loaded).not.toHaveProperty('resume');
    await waitFor(() =>
      expect(fetcher).toHaveBeenCalledWith(
        '/api/training/jobs?summary=true',
        expect.objectContaining({ method: 'GET' }),
      ),
    );
    expect(fetcher.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true);
    expect(screen.getByLabelText('Learning rate', { exact: true })).toBeValid();
  });

  it('does not invent missing original training settings and leaves unknown fields out', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => json([])),
    );
    const partial = {
      ...model,
      training_config: {
        seed: 77,
        optimizer: 'unsafe',
        learning_rate: Infinity,
        output_dir: '/tmp',
      },
    };
    const previous = trainingConfig('tablut', 'recommended');
    const loaded = recordedTrainingConfig(previous, partial)!;
    expect(loaded.seed).toBe(77);
    expect(loaded.learning_rate).toBe(previous.learning_rate);
    expect(loaded.optimizer).toBe(previous.optimizer);
    expect(loaded.model_name).toBe(model.name);
    expect(loaded).not.toHaveProperty('output_dir');
    expect(recordedTrainingConfig(previous, { ...model, training_config: {} })).toBeNull();
    show([], [{ ...model, training_config: undefined }]);
    expect(
      screen.getByRole('button', { name: 'Load training parameters for Renamed Freydis' }),
    ).toBeDisabled();
    await screen.findByRole('button', { name: /Model collection/ });
  });

  it('confirms training-data deletion, retains models and exposes failed deletions for retry', async () => {
    let removed = false;
    let deletes = 0;
    const fetcher = vi.fn((path: string, init?: RequestInit) => {
      if (init?.method === 'DELETE') {
        deletes++;
        if (deletes === 1)
          return json(
            { detail: 'The training data could not be completely removed. Try again.' },
            false,
          );
        removed = true;
        return json({ deleted: true, id: job.id });
      }
      if (path.split('?')[0].endsWith('/training/jobs')) return json(removed ? [] : [job]);
      if (path.endsWith(`/training/jobs/${job.id}`)) return json(job);
      return json({});
    });
    vi.stubGlobal('fetch', fetcher);
    const callbacks = show([job]);
    fireEvent.click(
      await screen
        .findByText('Original name', { selector: 'strong' })
        .then((label) => label.closest('button')!),
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Delete training data' }));
    let dialog = within(screen.getByRole('dialog', { name: 'Delete training data?' }));
    expect(dialog.getByText(/can no longer be resumed/)).toHaveTextContent('Registered models');
    fireEvent.click(dialog.getByRole('button', { name: 'Keep it' }));
    expect(deletes).toBe(0);
    fireEvent.click(screen.getByRole('button', { name: 'Delete training data' }));
    dialog = within(screen.getByRole('dialog', { name: 'Delete training data?' }));
    fireEvent.click(dialog.getByRole('button', { name: 'Delete' }));
    await dialog.findByRole('alert');
    expect(screen.getByRole('button', { name: 'Resume from checkpoint' })).toBeEnabled();
    fireEvent.click(dialog.getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Model collection/ }));
    expect(screen.getByRole('heading', { name: model.name })).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Resume from checkpoint' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: `Resume training for ${model.name}` }),
    ).not.toBeInTheDocument();
    expect(document.querySelector('.notice')).toHaveTextContent(
      'Registered models and saved game history have been kept',
    );
    expect(callbacks.onModelsChanged).not.toHaveBeenCalled();
    expect(fetcher).toHaveBeenCalledWith(
      '/api/training/jobs/saved-job',
      expect.objectContaining({ method: 'DELETE' }),
    );
  });

  it('does not offer deletion for an active job', async () => {
    const active = { ...job, status: 'running' };
    vi.stubGlobal(
      'fetch',
      vi.fn((path: string) =>
        json(
          path.split('?')[0].endsWith('/training/jobs')
            ? [active]
            : path.endsWith('/saved-job')
              ? active
              : {},
        ),
      ),
    );
    show([active]);
    fireEvent.click(
      await screen
        .findByText('Original name', { selector: 'strong' })
        .then((label) => label.closest('button')!),
    );
    await screen.findByRole('button', { name: 'Stop training' });
    expect(screen.queryByRole('button', { name: 'Delete training data' })).not.toBeInTheDocument();
  });

  it('renames by stable model ID and keeps the dialog available after server errors', async () => {
    let attempts = 0;
    const fetcher = vi.fn((path: string, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        attempts++;
        return attempts === 1
          ? json({ detail: 'The model name could not be saved. Try again.' }, false)
          : json({ ...model, name: 'New name' });
      }
      return json(path.split('?')[0].endsWith('/training/jobs') ? [] : {});
    });
    vi.stubGlobal('fetch', fetcher);
    const { onModelsChanged } = show();
    fireEvent.click(screen.getByRole('button', { name: 'Rename model Renamed Freydis' }));
    const dialog = within(screen.getByRole('dialog', { name: 'Rename model' }));
    fireEvent.change(dialog.getByRole('textbox', { name: 'Model name' }), {
      target: { value: '   New name  ' },
    });
    fireEvent.click(dialog.getByRole('button', { name: 'Save name' }));
    expect(
      await screen.findByText('The model name could not be saved. Try again.'),
    ).toBeInTheDocument();
    expect(onModelsChanged).not.toHaveBeenCalled();
    fireEvent.click(dialog.getByRole('button', { name: 'Save name' }));
    await waitFor(() => expect(onModelsChanged).toHaveBeenCalledOnce());
    expect(fetcher).toHaveBeenCalledWith(
      '/api/models/stable-id',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ name: 'New name' }) }),
    );
    expect(model.name).toBe('Renamed Freydis');
    expect(model.rune).toBe('ᚠ');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('shows actual network and training provenance separately from evaluation settings, including German', () => {
    const { container } = render(<ModelTrainingSummary model={model} />);
    expect(container).toHaveTextContent('24 channels · 2 blocks');
    expect(container).toHaveTextContent('2 / 9 planned');
    expect(container).toHaveTextContent('13 games / iteration');
    expect(container).toHaveTextContent('SGD · LR 0.003');
    expect(container.querySelector('.model-training-seed')).toHaveTextContent('Training seed 69');
    expect(container).not.toHaveTextContent('1000069');
    act(() => setLanguage('de', false));
    expect(container).toHaveTextContent('24 Kanäle · 2 Blöcke');
    expect(container).toHaveTextContent('2 / 9 geplant');
    expect(container).toHaveTextContent('Lernrate 0,003');
  });

  it('does not infer a training iteration from the default version of an imported network', () => {
    const { container } = render(
      <ModelTrainingSummary
        model={{ id: 'imported', name: 'Imported', ruleset: 'brandubh', version: 1 }}
      />,
    );
    const row = [...container.querySelectorAll('dl > div')].find((entry) =>
      entry.textContent?.startsWith('Training iteration'),
    );
    expect(row).toHaveTextContent('Not recorded');
  });
});
