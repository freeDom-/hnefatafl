import { useEffect, useId, useRef, useState } from 'react';
import { DayPicker } from 'react-day-picker';
import { de, enUS } from 'react-day-picker/locale';
import { useI18n } from './i18n';
import { localDateTimeInstants, localDateTimeValue } from './localDateTime';
import './localDateTimePicker.css';

/** Inline calendar stays inside Modal's native focus boundary; no nested dialog or portal. */
export default function LocalDateTimePicker({
  name,
  label,
  value = '',
}: {
  name: string;
  label: string;
  value?: string;
}) {
  const { t, locale } = useI18n();
  const initial = value ? localDateTimeValue(new Date(value)) : '';
  const [date, setDate] = useState(initial.slice(0, 10));
  const [time, setTime] = useState(initial.slice(11) || '12:00');
  const [month, setMonth] = useState(() => (value ? new Date(value) : new Date()));
  useEffect(() => {
    const day = localDateTimeInstants(`${date}T12:00`)[0];
    if (day) setMonth(day);
  }, [date]);
  const [occurrence, setOccurrence] = useState(() =>
    value &&
    localDateTimeInstants(initial).findIndex((d) => d.getTime() === new Date(value).getTime()) === 1
      ? '1'
      : '0',
  );
  const field = useRef<HTMLInputElement>(null);
  const id = useId();
  const instants = date ? localDateTimeInstants(`${date}T${time}`) : [];
  const error =
    date && !instants.length
      ? t('This local date or time does not exist. Check the date and daylight saving time.')
      : '';
  const selected = instants[Number(occurrence)] ?? instants[0];
  useEffect(() => {
    field.current?.setCustomValidity(error);
  }, [error]);
  const chooseDate = (next: string) => {
    setDate(next);
    setOccurrence('0');
  };
  return (
    <fieldset className="local-datetime">
      <legend>{t(label)}</legend>
      <div className="form-grid">
        <label htmlFor={`${id}-date`}>{t('Date (YYYY-MM-DD)')}</label>
        <input
          ref={field}
          id={`${id}-date`}
          type="text"
          inputMode="numeric"
          placeholder="YYYY-MM-DD"
          value={date}
          onChange={(e) => chooseDate(e.target.value)}
          aria-invalid={!!error}
          aria-describedby={`${id}-help`}
        />
        <label htmlFor={`${id}-time`}>{t('Time (HH:MM)')}</label>
        <input
          id={`${id}-time`}
          type="text"
          inputMode="numeric"
          placeholder="HH:MM"
          value={time}
          onChange={(e) => {
            setTime(e.target.value);
            setOccurrence('0');
          }}
          aria-invalid={!!error}
          aria-describedby={`${id}-help`}
        />
      </div>
      <details>
        <summary className="button secondary">{t('Choose date')}</summary>
        <DayPicker
          mode="single"
          selected={selected}
          month={month}
          onMonthChange={setMonth}
          onSelect={(day) => chooseDate(day ? localDateTimeValue(day).slice(0, 10) : '')}
          locale={locale === 'de' ? de : enUS}
          className="themed-calendar"
          classNames={{
            button_previous: 'button secondary',
            button_next: 'button secondary',
            day_button: 'button secondary',
          }}
          labels={{
            labelNext: () => t('Next month'),
            labelPrevious: () => t('Previous month'),
            labelNav: () => t('Calendar navigation'),
            labelDayButton: (day, modifiers) =>
              `${new Intl.DateTimeFormat(locale, { dateStyle: 'full' }).format(day)}${modifiers.selected ? `, ${t('Selected date')}` : ''}`,
          }}
        />
      </details>
      {instants.length > 1 && (
        <label>
          {t('This time occurs twice. Choose an occurrence.')}
          <select value={occurrence} onChange={(e) => setOccurrence(e.target.value)}>
            {instants.map((instant, index) => (
              <option key={index} value={String(index)}>
                {t(index === 0 ? 'First occurrence' : 'Second occurrence')} ·{' '}
                {new Intl.DateTimeFormat(locale, {
                  hour: '2-digit',
                  minute: '2-digit',
                  timeZoneName: 'shortOffset',
                }).format(instant)}
              </option>
            ))}
          </select>
        </label>
      )}
      <p id={`${id}-help`} role={error ? 'alert' : undefined}>
        {error ||
          t('Your timezone: {zone}. Dates are saved in UTC.', {
            zone: Intl.DateTimeFormat().resolvedOptions().timeZone,
          })}
      </p>
      <button type="button" className="button secondary" onClick={() => chooseDate('')}>
        {t('Clear date')}
      </button>
      <input type="hidden" name={name} value={selected?.toISOString() ?? ''} />
    </fieldset>
  );
}
