import { useI18n } from './i18n';
import { numberField } from './TrainingFields';
import type { DeviceInfo, TrainingWorker } from './types';
import type { TrainingConfig } from './trainingPresets';

export function inferenceTargetError(
  config: TrainingConfig,
  target: string,
  capabilities?: DeviceInfo,
) {
  if (config.inference_backend !== 'openvino') return '';
  if (
    !['cpu', 'auto'].includes(String(config.device ?? 'auto')) ||
    (config.device === 'auto' &&
      capabilities?.default_device &&
      capabilities.default_device !== 'cpu')
  )
    return 'OpenVINO inference requires PyTorch CPU optimization. Select CPU.';
  const requested = String(config.inference_device ?? 'GPU');
  if (
    target !== 'remote' &&
    (!capabilities?.inference_backends?.includes('openvino') ||
      !capabilities?.openvino_devices?.some(
        (device) =>
          device.available &&
          (device.id === requested ||
            (['auto', 'GPU'].includes(requested) && /^GPU(?:\.\d+)?$/.test(device.id))),
      ))
  )
    return 'This target has no reported OpenVINO GPU. Choose a compatible target or PyTorch.';
  return '';
}

export default function InferenceSettings({
  config,
  update,
  target,
  workers,
  local,
  disabled = false,
}: {
  config: TrainingConfig;
  update: (key: string, value: string | number | boolean) => void;
  target: string;
  workers: TrainingWorker[];
  local?: DeviceInfo;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  const capabilities =
    target === 'local'
      ? local
      : workers.find((worker) => `worker:${worker.id}` === target)?.capabilities;
  const gpus =
    capabilities?.openvino_devices?.filter(
      (device) =>
        capabilities?.inference_backends?.includes('openvino') &&
        device.available &&
        /^GPU(?:\.\d+)?$/.test(device.id),
    ) ?? [];
  const error = inferenceTargetError(config, target, capabilities);
  return (
    <div className="inference-settings">
      <label>
        {t('Neural inference backend')}
        <select
          value={String(config.inference_backend ?? 'pytorch')}
          disabled={disabled}
          onChange={(event) => {
            update('inference_backend', event.target.value);
            update('inference_device', event.target.value === 'openvino' ? 'GPU' : 'auto');
          }}
        >
          <option value="pytorch">{t('PyTorch — Default')}</option>
          <option value="openvino" disabled={target !== 'remote' && !gpus.length}>
            {t('OpenVINO — Intel GPU')}
            {gpus.length ? ` · ${gpus.map((gpu) => gpu.name).join(' · ')}` : ''}
          </option>
        </select>
      </label>
      <label>
        {t('Inference cache')}
        <select
          value={String(config.inference_cache_mode ?? 'off')}
          disabled={disabled}
          onChange={(event) => update('inference_cache_mode', event.target.value)}
        >
          <option value="off">{t('Off · default')}</option>
          <option value="exact">{t('Exact positions')}</option>
        </select>
      </label>
      {config.inference_cache_mode === 'exact' && (
        <>
          {numberField(
            'inference_cache_capacity',
            { ...config, inference_cache_capacity: config.inference_cache_capacity ?? 4096 },
            update,
            { disabled },
          )}
          <p className="subtle">
            {t(
              'Reuses identical positions during self-play. Measure throughput before enabling for longer runs.',
            )}
          </p>
        </>
      )}
      {config.inference_backend === 'openvino' && (
        <>
          <p className="subtle">
            {t(
              'OpenVINO accelerates self-play inference. PyTorch performs weight updates and evaluation on the CPU.',
            )}
          </p>
          <label>
            {t('Inference device')}
            <select
              value={String(config.inference_device ?? 'GPU')}
              disabled={disabled}
              onChange={(event) => update('inference_device', event.target.value)}
            >
              <option value="GPU">GPU</option>
              {gpus
                .filter((gpu) => gpu.id !== 'GPU')
                .map((gpu) => (
                  <option key={gpu.id} value={gpu.id}>
                    {gpu.id} · {gpu.name}
                  </option>
                ))}
              {!['GPU', ...gpus.map((gpu) => gpu.id)].includes(
                String(config.inference_device ?? 'GPU'),
              ) && (
                <option value={String(config.inference_device)}>
                  {String(config.inference_device)}
                </option>
              )}
            </select>
          </label>
          {target === 'remote' && (
            <p className="subtle">
              {t(
                'OpenVINO jobs require a compatible remote worker and remain queued until one has capacity.',
              )}
            </p>
          )}
        </>
      )}
      {error && (
        <p className="form-error" role="alert">
          {t(error)}
        </p>
      )}
    </div>
  );
}
