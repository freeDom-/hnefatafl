import { useId } from 'react';
import { useI18n } from './i18n';
import { fieldErrorAttributes, numberField } from './TrainingFields';
import { FieldLabel } from './FieldTooltip';
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
  invalidFields = [],
  errorId,
}: {
  config: TrainingConfig;
  update: (key: string, value: string | number | boolean) => void;
  target: string;
  workers: TrainingWorker[];
  local?: DeviceInfo;
  disabled?: boolean;
  /** Input identities the server rejected, so each control marks itself. */
  invalidFields?: string[];
  errorId?: string;
}) {
  const { t } = useI18n();
  const backendId = useId();
  const cacheId = useId();
  const deviceId = useId();
  const rejected = new Set(invalidFields);
  const invalid = (identity: string) => fieldErrorAttributes(rejected.has(identity), errorId);
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
      <div className="training-field">
        <FieldLabel
          htmlFor={backendId}
          label="Neural inference backend"
          tooltipKey="inference_backend"
        />
        <select
          id={backendId}
          data-training-field="inference_backend"
          {...invalid('inference_backend')}
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
      </div>
      <div className="training-field">
        <FieldLabel htmlFor={cacheId} label="Inference cache" tooltipKey="inference_cache_mode" />
        <select
          id={cacheId}
          data-training-field="inference_cache_mode"
          {...invalid('inference_cache_mode')}
          value={String(config.inference_cache_mode ?? 'off')}
          disabled={disabled}
          onChange={(event) => update('inference_cache_mode', event.target.value)}
        >
          <option value="off">{t('Off · default')}</option>
          <option value="exact">{t('Exact positions')}</option>
        </select>
      </div>
      {config.inference_cache_mode === 'exact' && (
        <>
          {numberField(
            'inference_cache_capacity',
            { ...config, inference_cache_capacity: config.inference_cache_capacity ?? 4096 },
            update,
            {
              disabled,
              invalid: rejected.has('inference_cache_capacity'),
              errorId,
            },
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
          <div className="training-field">
            <FieldLabel htmlFor={deviceId} label="Inference device" tooltipKey="inference_device" />
            <select
              id={deviceId}
              data-training-field="inference_device"
              {...invalid('inference_device')}
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
          </div>
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
