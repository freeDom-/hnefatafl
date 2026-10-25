import { useId } from 'react';
import { useI18n } from './i18n';
import type { DeviceInfo } from './types';
import type { TrainingConfig } from './trainingPresets';

/** A target that predates augmentation cannot run random_d4; OFF always remains valid. */
export function symmetryTargetError(config: TrainingConfig, capabilities?: DeviceInfo) {
  if (config.training_symmetry_mode !== 'random_d4' || !capabilities) return '';
  const modes = capabilities.training_symmetry_modes ?? ['off'];
  return modes.includes('random_d4')
    ? ''
    : 'This target does not support training symmetry augmentation. Choose a newer worker or disable it.';
}

export default function SymmetrySettings({
  config,
  update,
  capabilities,
  disabled = false,
}: {
  config: TrainingConfig;
  update: (key: string, value: string | number | boolean) => void;
  capabilities?: DeviceInfo;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  const helpId = useId();
  const error = symmetryTargetError(config, capabilities);
  return (
    <div className="symmetry-settings">
      <label>
        {t('Training symmetry augmentation')}
        <select
          value={String(config.training_symmetry_mode ?? 'off')}
          disabled={disabled}
          aria-describedby={helpId}
          aria-invalid={error ? true : undefined}
          onChange={(event) => update('training_symmetry_mode', event.target.value)}
        >
          <option value="off">{t('Disabled')}</option>
          <option value="random_d4">{t('Random D4 rotations and reflections')}</option>
        </select>
      </label>
      <small id={helpId}>
        {t(
          'Randomly rotate or reflect training positions and their corresponding move targets to help the network learn equivalent board orientations.',
        )}{' '}
        {t('This changes training samples only, not the game rules or MCTS search budget.')}
      </small>
      {error && (
        <small role="alert">
          {t(
            'This target does not support training symmetry augmentation. Choose a newer worker or disable it.',
          )}
        </small>
      )}
    </div>
  );
}
