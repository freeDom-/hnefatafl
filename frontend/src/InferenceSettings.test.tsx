import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useState } from 'react';
import InferenceSettings, { inferenceTargetError } from './InferenceSettings';
import { changeTrainingPlan, recordedTrainingConfig, trainingConfig } from './trainingPresets';
import type { TrainingConfig } from './trainingPresets';
import type { DeviceInfo } from './types';
import { setLanguage } from './i18n';

const intel: DeviceInfo = {
  inference_backends: ['pytorch', 'openvino'],
  openvino_devices: [{ id: 'GPU', name: 'Intel Iris Xe', available: true }],
};
afterEach(() => {
  cleanup();
  setLanguage('en', false);
});
function Form({ local = intel, target = 'local' }: { local?: DeviceInfo; target?: string }) {
  const [config, setConfig] = useState<TrainingConfig>({
    ...trainingConfig('tablut', 'recommended'),
    device: 'cpu',
  });
  return (
    <>
      <InferenceSettings
        config={config}
        target={target}
        workers={[]}
        local={local}
        update={(key, value) => setConfig((previous) => ({ ...previous, [key]: value }))}
      />
      <output>{JSON.stringify(config)}</output>
    </>
  );
}
it('keeps PyTorch as default even with Intel GPU detection and opts in explicitly', () => {
  render(<Form />);
  expect(screen.getByLabelText('Neural inference backend')).toHaveValue('pytorch');
  expect(screen.getByRole('option', { name: /Intel Iris Xe/ })).toBeEnabled();
  fireEvent.change(screen.getByLabelText('Neural inference backend'), {
    target: { value: 'openvino' },
  });
  expect(screen.getByLabelText('Inference device')).toHaveValue('GPU');
  expect(screen.getByRole('status')).toHaveTextContent('"device":"cpu"');
  expect(screen.getByText(/PyTorch performs weight updates/)).toBeInTheDocument();
});
it('does not advertise unknown hardware and allows compatible-worker queuing', () => {
  const update = vi.fn();
  const { rerender } = render(
    <InferenceSettings
      config={{ inference_backend: 'pytorch' }}
      target="local"
      workers={[]}
      update={update}
    />,
  );
  expect(screen.getByRole('option', { name: 'OpenVINO — Intel GPU' })).toBeDisabled();
  rerender(
    <InferenceSettings
      config={{ inference_backend: 'openvino', device: 'cpu' }}
      target="local"
      workers={[]}
      update={update}
    />,
  );
  expect(screen.getByRole('alert')).toHaveTextContent('no reported OpenVINO GPU');
  expect(update).not.toHaveBeenCalled();
  rerender(
    <InferenceSettings
      config={{ inference_backend: 'openvino', device: 'cpu' }}
      target="remote"
      workers={[]}
      update={update}
    />,
  );
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.getByText(/remain queued/)).toBeInTheDocument();
});
it('preserves explicit choices across presets, enforces independent optimization and handles legacy saved config', () => {
  const selected = {
    ...trainingConfig('tablut', 'recommended'),
    inference_backend: 'openvino',
    inference_device: 'GPU',
  };
  for (const rule of ['brandubh', 'tablut', 'copenhagen'])
    expect(changeTrainingPlan(selected, rule, 'smoke').inference_backend).toBe('openvino');
  expect(inferenceTargetError({ ...selected, device: 'cuda' }, 'local', intel)).toMatch(
    /CPU optimization/,
  );
  expect(
    recordedTrainingConfig(selected, {
      name: 'legacy',
      ruleset: 'tablut',
      training_config: { channels: 48 },
    })?.inference_backend,
  ).toBe('pytorch');
});

it('defaults cache off, validates capacity and preserves exact settings across plans', () => {
  render(<Form />);
  expect(screen.getByLabelText('Inference cache')).toHaveValue('off');
  expect(screen.queryByLabelText('Inference cache capacity')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Inference cache'), { target: { value: 'exact' } });
  expect(screen.getByLabelText('Inference cache capacity')).toHaveValue(4096);
  fireEvent.change(screen.getByLabelText('Inference cache capacity'), { target: { value: '256' } });
  expect(screen.getByRole('status')).toHaveTextContent('"inference_cache_capacity":256');
  const selected = {
    ...trainingConfig('brandubh', 'smoke'),
    inference_cache_mode: 'exact',
    inference_cache_capacity: 256,
  };
  expect(changeTrainingPlan(selected, 'copenhagen', 'recommended')).toMatchObject({
    inference_cache_mode: 'exact',
    inference_cache_capacity: 256,
  });
});
