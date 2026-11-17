import { useI18n } from './i18n';
import { moveSeconds } from './moveDuration';
import type { MatchMode } from './onlineTypes';

export const matchModeNames = {
  single: 'Single game',
  sequential: 'Two-game match — sequential',
  simultaneous: 'Two-game match — simultaneous',
};
export function MatchModeField({
  value,
  onChange,
}: {
  value: MatchMode;
  onChange: (value: MatchMode) => void;
}) {
  const { t } = useI18n();
  return (
    <label>
      {t('Match mode')}
      <select
        name="match_mode"
        value={value}
        onChange={(e) => onChange(e.target.value as MatchMode)}
      >
        {Object.entries(matchModeNames).map(([key, label]) => (
          <option key={key} value={key}>
            {t(label)}
          </option>
        ))}
      </select>
    </label>
  );
}
export type ClockInput = {
  mode: 'per_move' | 'total';
  days: string;
  hours: string;
  minutes: string;
  increment: string;
};
export const defaultClockInput: ClockInput = {
  mode: 'per_move',
  days: '3',
  hours: '0',
  minutes: '0',
  increment: '0',
};
export function clockRequest(value: ClockInput) {
  if (
    !/^\d+$/.test(value.increment) ||
    !Number.isSafeInteger(Number(value.increment)) ||
    Number(value.increment) > 1209600
  )
    throw new Error('Enter an increment in whole seconds from 0 to 1209600.');
  return {
    time_per_move: moveSeconds(value.days, value.hours, value.minutes),
    time_control: value.mode,
    increment_seconds: value.mode === 'total' ? Number(value.increment) : 0,
  };
}
export function ClockFields({
  value,
  onChange,
}: {
  value: ClockInput;
  onChange: (value: ClockInput) => void;
}) {
  const { t } = useI18n();
  return (
    <>
      <label>
        {t('Time control')}
        <select
          name="time_control"
          value={value.mode}
          onChange={(e) =>
            onChange({
              ...value,
              mode: e.target.value as ClockInput['mode'],
              increment: e.target.value === 'per_move' ? '0' : value.increment,
            })
          }
        >
          <option value="per_move">{t('Per move')}</option>
          <option value="total">{t('Total per player')}</option>
        </select>
      </label>
      <fieldset className="move-duration">
        <legend>
          {t(value.mode === 'total' ? 'Total time per player' : 'Time allowed per move')}
        </legend>
        <div className="form-grid form-grid-three">
          {(
            [
              ['Days', 'days', 14],
              ['Hours', 'hours', 23],
              ['Minutes', 'minutes', 59],
            ] as const
          ).map(([label, key, max]) => (
            <label key={key}>
              {t(label)}
              <input
                name={key}
                type="number"
                min={0}
                max={max}
                step={1}
                required
                value={value[key]}
                onChange={(e) => onChange({ ...value, [key]: e.target.value })}
              />
            </label>
          ))}
        </div>
      </fieldset>
      {value.mode === 'total' && (
        <label>
          {t('Increment per move (seconds)')}
          <input
            name="increment_seconds"
            type="number"
            min={0}
            max={1209600}
            step={1}
            required
            value={value.increment}
            onChange={(e) => onChange({ ...value, increment: e.target.value })}
          />
        </label>
      )}
    </>
  );
}
