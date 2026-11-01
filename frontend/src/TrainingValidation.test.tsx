import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import Training from './Training';
import { setLanguage } from './i18n';
import type { Ruleset } from './types';

/**
 * A rejected training field must be actionable: the exact field is named, the
 * matching input is marked, and every entered value survives so the user can fix
 * one field instead of rebuilding the form.
 */

const rules: Ruleset[] = [
  { id: 'brandubh', name: 'Brandubh', size: 7, description: '', rules_markdown: '' },
  { id: 'tablut', name: 'Tablut', size: 9, description: '', rules_markdown: '' },
];
const json = (value: unknown, ok = true) =>
  Promise.resolve({ ok, json: async () => value } as Response);

function rejection(fields: string[], detail: string) {
  return json({ detail, code: 'unsupported_training_field', fields }, false);
}

function harness(responses: Promise<Response>[]) {
  const posts: Record<string, unknown>[] = [];
  const error = vi.fn();
  const fetcher = vi.fn((path: string, init?: RequestInit) => {
    const base = path.split('?')[0];
    if (base === '/api/training/jobs') {
      if (init?.method === 'POST') {
        posts.push(JSON.parse(String(init.body)));
        return responses[Math.min(posts.length - 1, responses.length - 1)];
      }
      return json([]);
    }
    if (path === '/api/training/workers') return json([]);
    if (path === '/api/training/device') return json({});
    return json([]);
  });
  vi.stubGlobal('fetch', fetcher);
  render(<Training rulesets={rules} models={[]} onError={error} onModelsChanged={vi.fn()} />);
  return { posts, error };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  setLanguage('en', false);
});

it('names and highlights a rejected field, keeps the values and recovers', async () => {
  setLanguage('en', false);
  const created = json({ id: 'new-job', status: 'running', config: {} });
  const { posts, error } = harness([
    rejection(['channels'], 'Unsupported training configuration field: channels'),
    created,
  ]);
  await screen.findByText('Your next opponent starts here.');
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Training iterations' }), {
    target: { value: '9' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));

  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent(
    'Training was not started: one configuration field was rejected.',
  );
  // The technical name is always shown, even when a label exists.
  expect(alert).toHaveTextContent('Network channels');
  expect(alert).toHaveTextContent('channels');
  const channels = screen.getByRole('spinbutton', { name: 'Network channels' });
  await waitFor(() => expect(channels).toHaveAttribute('aria-invalid', 'true'));
  expect(channels).toHaveClass('field-invalid');
  // Every entered value survives, and the failure is not a global notice.
  expect(screen.getByRole('spinbutton', { name: 'Training iterations' })).toHaveValue(9);
  expect(error).not.toHaveBeenCalled();

  // Correcting the field clears its rejection; the next submit succeeds.
  fireEvent.change(channels, { target: { value: '8' } });
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  await waitFor(() => expect(channels).not.toHaveAttribute('aria-invalid'));
  expect(channels).not.toHaveClass('field-invalid');
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));
  await waitFor(() => expect(posts).toHaveLength(2));
  expect(posts[1].channels).toBe(8);
});

it('reports every rejected field and explains the ones without an input', async () => {
  setLanguage('en', false);
  harness([
    rejection(
      ['output_dir', 'registry_dir'],
      'Unsupported training configuration fields: output_dir, registry_dir',
    ),
  ]);
  await screen.findByText('Your next opponent starts here.');
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));

  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent(
    'Training was not started: 2 configuration fields were rejected.',
  );
  expect(alert).toHaveTextContent('output_dir');
  expect(alert).toHaveTextContent('registry_dir');
  expect(alert).toHaveTextContent('The server assigns each training job its own output directory.');
  expect(alert).toHaveTextContent('The server keeps trained models in its own model directory.');
  // No input exists for them, so nothing is marked invalid.
  expect(document.querySelector('.field-invalid')).toBeNull();
});

it('explains an unknown rejected field instead of showing only its name', async () => {
  setLanguage('en', false);
  harness([rejection(['future_field'], 'Unsupported training configuration field: future_field')]);
  await screen.findByText('Your next opponent starts here.');
  fireEvent.click(screen.getByRole('button', { name: 'Start training' }));

  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('future_field');
  expect(alert).toHaveTextContent(
    'This server does not accept this training field. Remove it from the configuration and try again.',
  );
});

it('marks the nested tuning field the server named', async () => {
  setLanguage('en', false);
  harness([
    rejection(
      ['training.batch_size'],
      'Unsupported training configuration field: training.batch_size',
    ),
  ]);
  await screen.findByText('Your next opponent starts here.');
  fireEvent.click(screen.getByRole('button', { name: 'Tune parameters' }));
  fireEvent.click(screen.getByRole('button', { name: 'Start tuning' }));

  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('Batch size');
  const batch = screen.getByRole('spinbutton', { name: 'Batch size' });
  await waitFor(() => expect(batch).toHaveAttribute('aria-invalid', 'true'));
});

it('localizes the rejected-field report in German', async () => {
  setLanguage('de', false);
  harness([rejection(['output_dir'], 'Unsupported training configuration field: output_dir')]);
  await screen.findByRole('button', { name: 'Training starten' });
  fireEvent.click(screen.getByRole('button', { name: 'Training starten' }));

  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent(
    'Das Training wurde nicht gestartet: Ein Konfigurationsfeld wurde abgelehnt.',
  );
  expect(alert).toHaveTextContent('output_dir');
  expect(alert).toHaveTextContent(
    'Der Server weist jedem Trainingsauftrag ein eigenes Ausgabeverzeichnis zu.',
  );
});

const START = 'Start training';
const TUNE = 'Start tuning';
const SEARCH_SPACE = 'Parameter search space (JSON)';

function tune() {
  fireEvent.click(screen.getByRole('button', { name: 'Tune parameters' }));
}

function typeSearchSpace(value: string) {
  fireEvent.change(screen.getByRole('textbox', { name: SEARCH_SPACE }), { target: { value } });
}

it('submits ordinary training even when the tuning search space is malformed', async () => {
  setLanguage('en', false);
  const created = json({ id: 'new-job', status: 'running', config: {} });
  const { posts } = harness([created]);
  await screen.findByText('Your next opponent starts here.');
  // Break the tuning text, then switch the form back to ordinary training.
  tune();
  typeSearchSpace('{ not json');
  fireEvent.click(screen.getByRole('button', { name: 'Train a model' }));
  fireEvent.click(screen.getByRole('button', { name: START }));

  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].kind).toBe('train');
  expect(posts[0].search_space).toBeUndefined();
  expect(posts[0].training).toBeUndefined();
  expect(screen.queryByRole('alert')).toBeNull();
});

it('reports malformed tuning JSON against the search-space input', async () => {
  setLanguage('en', false);
  const { posts, error } = harness([json({ id: 'new-job', status: 'running', config: {} })]);
  await screen.findByText('Your next opponent starts here.');
  tune();
  typeSearchSpace('{ broken');
  fireEvent.click(screen.getByRole('button', { name: TUNE }));

  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('Parameter search space (JSON)');
  expect(alert).toHaveTextContent('search_space');
  expect(alert).toHaveTextContent('The parameter search space is not valid JSON.');
  const input = screen.getByRole('textbox', { name: SEARCH_SPACE });
  await waitFor(() => expect(input).toHaveAttribute('aria-invalid', 'true'));
  await waitFor(() => expect(input).toHaveAttribute('aria-describedby', alert.id));
  // Nothing is submitted, the entered text survives, and it is not a global notice.
  expect(posts).toHaveLength(0);
  expect(error).not.toHaveBeenCalled();
  expect(input).toHaveValue('{ broken');

  // Fixing the text clears the rejection so the tuning job can start.
  typeSearchSpace('{"learning_rate": {"type": "float", "low": 0.0001, "high": 0.01}}');
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  fireEvent.click(screen.getByRole('button', { name: TUNE }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].kind).toBe('tune');
  expect(posts[0].search_space).toEqual({
    learning_rate: { type: 'float', low: 0.0001, high: 0.01 },
  });
});

it('localizes the malformed tuning error in German', async () => {
  setLanguage('de', false);
  harness([json({ id: 'new-job', status: 'running', config: {} })]);
  await screen.findByRole('button', { name: 'Parameter optimieren' });
  fireEvent.click(screen.getByRole('button', { name: 'Parameter optimieren' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Parametersuchraum (JSON)' }), {
    target: { value: '{' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Optimierung starten' }));

  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('Der Parameter-Suchraum ist kein gültiges JSON.');
});

it('clears a stale rejection when the plan is replaced', async () => {
  setLanguage('en', false);
  harness([rejection(['channels'], 'Unsupported training configuration field: channels')]);
  await screen.findByText('Your next opponent starts here.');
  fireEvent.click(screen.getByRole('button', { name: START }));
  await screen.findByRole('alert');

  // Changing the battlefield or preset replaces every training parameter.
  fireEvent.change(screen.getByRole('combobox', { name: 'Battlefield' }), {
    target: { value: 'brandubh' },
  });
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  fireEvent.click(screen.getByRole('button', { name: START }));
  await screen.findByRole('alert');

  fireEvent.change(screen.getByRole('combobox', { name: 'Training preset' }), {
    target: { value: 'balanced' },
  });
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
});

it('expands Advanced to reach a rejected field and marks it', async () => {
  setLanguage('en', false);
  harness([rejection(['seed'], 'Unsupported training configuration field: seed')]);
  await screen.findByText('Your next opponent starts here.');
  // The model seed lives in the collapsed Advanced section.
  expect(screen.queryByRole('spinbutton', { name: 'Random seed' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: START }));

  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('Random seed');
  const seed = await screen.findByRole('spinbutton', { name: 'Random seed' });
  await waitFor(() => expect(seed).toHaveAttribute('aria-invalid', 'true'));
  await waitFor(() => expect(document.activeElement).toBe(seed));
});

it('keeps the study seed and the model seed distinct', async () => {
  setLanguage('en', false);
  harness([
    rejection(
      ['seed', 'training.seed'],
      'Unsupported training configuration fields: seed, training.seed',
    ),
  ]);
  await screen.findByText('Your next opponent starts here.');
  tune();
  fireEvent.click(screen.getByRole('button', { name: TUNE }));

  const alert = await screen.findByRole('alert');
  // Both seeds are named, and their full paths stay visible.
  expect(alert).toHaveTextContent('Study random seed');
  expect(alert).toHaveTextContent('Random seed');
  expect(alert).toHaveTextContent('(seed)');
  expect(alert).toHaveTextContent('(training.seed)');
  const study = screen.getByRole('spinbutton', { name: 'Study random seed' });
  const model = await screen.findByRole('spinbutton', { name: 'Random seed' });
  await waitFor(() => expect(study).toHaveAttribute('aria-invalid', 'true'));
  await waitFor(() => expect(model).toHaveAttribute('aria-invalid', 'true'));

  // Correcting one seed clears only its own rejection.
  fireEvent.change(study, { target: { value: '7' } });
  await waitFor(() => expect(study).not.toHaveAttribute('aria-invalid'));
  expect(model).toHaveAttribute('aria-invalid', 'true');
  const remaining = screen.getByRole('alert');
  expect(remaining).not.toHaveTextContent('Study random seed');
  expect(remaining).toHaveTextContent('Random seed');
});

it('reports a rejected field that is hidden behind another setting', async () => {
  setLanguage('en', false);
  harness([
    rejection(
      ['evaluation_jarl_depth'],
      'Unsupported training configuration field: evaluation_jarl_depth',
    ),
  ]);
  await screen.findByText('Your next opponent starts here.');
  // The Jarl depth only exists while the Jarl opponent is selected.
  fireEvent.click(screen.getByRole('button', { name: START }));

  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('Jarl evaluation depth');
  expect(alert).toHaveTextContent('evaluation_jarl_depth');
  expect(document.querySelector('.field-invalid')).toBeNull();
  // The fallback is focused so it is announced instead of silently ignored.
  expect(document.activeElement).toBe(alert);
});

it('does not move focus when Advanced is toggled manually', async () => {
  setLanguage('en', false);
  harness([rejection(['channels'], 'Unsupported training configuration field: channels')]);
  await screen.findByText('Your next opponent starts here.');
  fireEvent.click(screen.getByRole('button', { name: START }));
  await screen.findByRole('alert');

  // The user moves focus to the toggle and activates it; the error must not
  // pull focus back to the marked input.
  const toggle = screen.getByRole('button', { name: /Advanced configuration/ });
  toggle.focus();
  fireEvent.click(toggle);
  expect(document.activeElement).toBe(toggle);
  expect(screen.getByRole('alert')).toBeInTheDocument();
});

it('clears a rejection that the other form cannot own', async () => {
  setLanguage('en', false);
  harness([rejection(['seed'], 'Unsupported training configuration field: seed')]);
  await screen.findByText('Your next opponent starts here.');
  fireEvent.click(screen.getByRole('button', { name: START }));
  await screen.findByRole('alert');

  // `seed` is the study seed while tuning and the model seed while training, so
  // switching forms drops the rejection instead of marking the other field.
  fireEvent.click(screen.getByRole('button', { name: 'Tune parameters' }));
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  fireEvent.click(screen.getByRole('button', { name: 'Train a model' }));
  expect(screen.queryByRole('alert')).toBeNull();
});

it('marks and describes a rejected symmetry setting next to its help text', async () => {
  setLanguage('en', false);
  harness([
    rejection(
      ['training_symmetry_mode'],
      'Unsupported training configuration field: training_symmetry_mode',
    ),
  ]);
  await screen.findByText('Your next opponent starts here.');
  fireEvent.click(screen.getByRole('button', { name: START }));

  const alert = await screen.findByRole('alert');
  const select = screen.getByRole('combobox', { name: 'Training symmetry augmentation' });
  await waitFor(() => expect(select).toHaveAttribute('aria-invalid', 'true'));
  expect(select).toHaveClass('field-invalid');
  // Both the field help and the rejection alert describe the control.
  const described = (await select.getAttribute('aria-describedby'))!.split(' ');
  expect(described).toContain(alert.id);
  expect(described.length).toBe(2);
});

it('marks a rejected checkbox setting', async () => {
  setLanguage('en', false);
  harness([
    rejection(['save_all_best'], 'Unsupported training configuration field: save_all_best'),
  ]);
  await screen.findByText('Your next opponent starts here.');
  fireEvent.click(screen.getByRole('button', { name: START }));

  await screen.findByRole('alert');
  const checkbox = screen.getByRole('checkbox', { name: 'Save every new best model' });
  await waitFor(() => expect(checkbox).toHaveAttribute('aria-invalid', 'true'));
  expect(checkbox).toHaveClass('field-invalid');
});
