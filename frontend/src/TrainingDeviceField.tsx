import { t } from './i18n';
import type { DeviceInfo } from './types';

/**
 * A local target requires this server to report the accelerator. A specific
 * remote worker uses its own capabilities. An "Any remote worker" target has no
 * capabilities, so every option stays enabled and the scheduler selects a worker
 * that matches the requested device.
 */
export function deviceUnavailable(
  accelerator: 'cuda' | 'mps',
  remote: boolean,
  capabilities?: DeviceInfo,
): boolean {
  const available = capabilities?.[`${accelerator}_available`];
  return remote ? available === false : available !== true;
}

export default function TrainingDeviceField({
  value,
  onChange,
  remote,
  capabilities,
  disabled = false,
}: {
  value: string;
  onChange: (value: string) => void;
  remote: boolean;
  capabilities?: DeviceInfo;
  disabled?: boolean;
}) {
  const cudaUnavailable = deviceUnavailable('cuda', remote, capabilities);
  const mpsUnavailable = deviceUnavailable('mps', remote, capabilities);
  return (
    <label>
      {t('Training device')}
      <select value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
        <option value="auto">{t('Automatic')}</option>
        <option value="cpu">{t('CPU')}</option>
        <option value="cuda" disabled={cudaUnavailable}>
          {t('NVIDIA CUDA')}
          {cudaUnavailable ? ` (${t('unavailable')})` : ''}
        </option>
        <option value="mps" disabled={mpsUnavailable}>
          {t('Apple Metal')}
          {mpsUnavailable ? ` (${t('unavailable')})` : ''}
        </option>
      </select>
    </label>
  );
}
