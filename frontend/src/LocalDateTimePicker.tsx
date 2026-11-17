import { useEffect, useId, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { DayPicker } from 'react-day-picker';
import { de, enUS } from 'react-day-picker/locale';
import { useI18n } from './i18n';
import { localDateTimeInstants, localDateTimeValue } from './localDateTime';
import './localDateTimePicker.css';

/** The native top layer escapes modal scrolling while the DOM stays inside its focus boundary. */
function CalendarPopup({
  anchor,
  id,
  selected,
  openRequest,
  onSelect,
  onClose,
}: {
  anchor: RefObject<HTMLDivElement | null>;
  id: string;
  selected?: Date;
  openRequest: { focus: boolean };
  onSelect: (day: Date) => void;
  onClose: (restoreFocus: boolean) => void;
}) {
  const { t, locale } = useI18n();
  const box = useRef<HTMLDivElement>(null);
  const [month, setMonth] = useState(selected ?? new Date());
  useEffect(() => {
    if (selected) setMonth(selected);
  }, [selected?.getTime()]);
  useLayoutEffect(() => {
    const element = box.current;
    if (!element) return;
    // Older browsers retain the project's fixed-position tooltip fallback.
    if (typeof element.showPopover === 'function') element.showPopover();
    else element.style.display = 'block';
    const place = () => {
      const field = anchor.current;
      if (!field) return;
      const visual = window.visualViewport;
      const left = visual?.offsetLeft ?? 0,
        top = visual?.offsetTop ?? 0;
      const width = visual?.width ?? innerWidth,
        height = visual?.height ?? innerHeight;
      const rect = field.getBoundingClientRect();
      element.style.width = `${Math.max(0, Math.min(320, width - 16))}px`;
      element.style.maxHeight = `${Math.max(0, height - 16)}px`;
      const size = element.getBoundingClientRect();
      const below = rect.bottom + 4;
      const wantedTop =
        below + size.height <= top + height - 8 ? below : rect.top - size.height - 4;
      element.style.left = `${Math.max(left + 8, Math.min(rect.left, left + width - size.width - 8))}px`;
      element.style.top = `${Math.max(top + 8, Math.min(wantedTop, top + height - size.height - 8))}px`;
    };
    place();
    if (openRequest.focus) {
      const day =
        element.querySelector<HTMLElement>(
          '[aria-selected="true"] button, [role="grid"] button[tabindex="0"]',
        ) ?? element.querySelector<HTMLElement>('[role="grid"] button');
      day?.focus({ preventScroll: true });
    }
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    window.visualViewport?.addEventListener('resize', place);
    window.visualViewport?.addEventListener('scroll', place);
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(place) : null;
    observer?.observe(element);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
      window.visualViewport?.removeEventListener('resize', place);
      window.visualViewport?.removeEventListener('scroll', place);
      if (element.isConnected) element.hidePopover?.();
    };
  }, [anchor, openRequest]);
  useEffect(() => {
    const outside = (event: PointerEvent) => {
      if (!anchor.current?.contains(event.target as Node)) onClose(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      onClose(true);
    };
    document.addEventListener('pointerdown', outside, true);
    document.addEventListener('keydown', escape, true);
    return () => {
      document.removeEventListener('pointerdown', outside, true);
      document.removeEventListener('keydown', escape, true);
    };
  }, [anchor, onClose]);
  return (
    <div
      ref={box}
      id={id}
      popover="manual"
      role="dialog"
      aria-label={t('Choose date')}
      className="datetime-calendar-popover"
      onBlur={(event) => {
        if (event.relatedTarget && !anchor.current?.contains(event.relatedTarget as Node))
          onClose(false);
      }}
    >
      <DayPicker
        mode="single"
        required
        selected={selected}
        month={month}
        onMonthChange={setMonth}
        onSelect={(day) => {
          if (day) onSelect(day);
        }}
        locale={locale.startsWith('de') ? de : enUS}
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
    </div>
  );
}

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
  const [occurrence, setOccurrence] = useState(() =>
    value &&
    localDateTimeInstants(initial).findIndex(
      (d) => Math.floor(d.getTime() / 60000) === Math.floor(new Date(value).getTime() / 60000),
    ) === 1
      ? '1'
      : '0',
  );
  const [popup, setPopup] = useState<{ focus: boolean } | null>(null);
  const field = useRef<HTMLInputElement>(null),
    timeField = useRef<HTMLInputElement>(null);
  const anchor = useRef<HTMLDivElement>(null);
  const id = useId();
  const selectedDay = date ? localDateTimeInstants(`${date}T12:00`)[0] : undefined;
  const validTime = /^([01]\d|2[0-3]):[0-5]\d$/.test(time);
  const instants = date && validTime ? localDateTimeInstants(`${date}T${time}`) : [];
  const dateError =
    date && !selectedDay
      ? t('This local date or time does not exist. Check the date and daylight saving time.')
      : '';
  const timeError =
    !validTime && (date || time)
      ? t('Enter a time in HH:MM format.')
      : date && !dateError && !instants.length
        ? t('This local date or time does not exist. Check the date and daylight saving time.')
        : '';
  const error = dateError || timeError;
  const selected = instants[Number(occurrence)] ?? instants[0];
  useEffect(() => {
    field.current?.setCustomValidity(dateError);
    timeField.current?.setCustomValidity(timeError);
  }, [dateError, timeError]);
  const chooseDate = (next: string) => {
    setDate(next);
    if (next !== date) setOccurrence('0');
  };
  const close = (restoreFocus: boolean) => {
    setPopup(null);
    if (restoreFocus) field.current?.focus({ preventScroll: true });
  };
  const timezone = t('Your timezone: {zone}. Dates are saved in UTC.', {
    zone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  });
  // Preserve a retained instant's seconds as long as its displayed wall time and occurrence are unchanged.
  const retained =
    value &&
    `${date}T${time}` === initial &&
    selected &&
    Math.floor(selected.getTime() / 60000) === Math.floor(new Date(value).getTime() / 60000);
  return (
    <fieldset className="local-datetime">
      <legend>{t(label)}</legend>
      <div className="datetime-fields">
        <div className="datetime-date">
          <label className="sr-only" htmlFor={`${id}-date`}>
            {t('Date (YYYY-MM-DD)')}
          </label>
          <div
            ref={anchor}
            className="datetime-input"
            onBlur={(event) => {
              if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget as Node))
                close(false);
            }}
          >
            <input
              ref={field}
              id={`${id}-date`}
              type="text"
              placeholder="YYYY-MM-DD"
              value={date}
              onChange={(event) => chooseDate(event.target.value)}
              onClick={() => setPopup({ focus: false })}
              onKeyDown={(event) => {
                if (event.key === 'ArrowDown' || event.key === 'Enter') {
                  event.preventDefault();
                  setPopup({ focus: true });
                }
              }}
              aria-haspopup="dialog"
              aria-expanded={!!popup}
              aria-controls={`${id}-calendar`}
              aria-invalid={!!dateError}
              aria-describedby={`${id}-help${error ? ` ${id}-error` : ''}`}
            />
            <span className="datetime-input-actions">
              {date && (
                <button
                  className="icon-button"
                  type="button"
                  aria-label={t('Clear date')}
                  onClick={() => {
                    chooseDate('');
                    close(true);
                  }}
                >
                  <span aria-hidden="true">×</span>
                </button>
              )}
              <button
                className="icon-button"
                type="button"
                aria-label={t('Open calendar')}
                aria-haspopup="dialog"
                aria-expanded={!!popup}
                aria-controls={`${id}-calendar`}
                onClick={() => (popup ? close(true) : setPopup({ focus: true }))}
              >
                <svg
                  viewBox="0 0 24 24"
                  width="18"
                  height="18"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  aria-hidden="true"
                >
                  <rect x="3" y="5" width="18" height="16" rx="2" />
                  <path d="M7 2v6M17 2v6M3 11h18" />
                </svg>
              </button>
            </span>
            {popup && (
              <CalendarPopup
                anchor={anchor}
                id={`${id}-calendar`}
                selected={selectedDay}
                openRequest={popup}
                onSelect={(day) => {
                  chooseDate(localDateTimeValue(day).slice(0, 10));
                  close(true);
                }}
                onClose={close}
              />
            )}
          </div>
        </div>
        <label>
          <span className="sr-only">{t('Time (HH:MM)')}</span>
          <input
            ref={timeField}
            id={`${id}-time`}
            type="text"
            placeholder="HH:MM"
            value={time}
            onChange={(event) => {
              setTime(event.target.value);
              setOccurrence('0');
            }}
            aria-invalid={!!timeError}
            aria-describedby={`${id}-help${error ? ` ${id}-error` : ''}`}
          />
        </label>
      </div>
      {instants.length > 1 && (
        <label>
          {t('This time occurs twice. Choose an occurrence.')}
          <select value={occurrence} onChange={(event) => setOccurrence(event.target.value)}>
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
      <span id={`${id}-help`} className="sr-only">
        {timezone}
      </span>
      {error && (
        <p id={`${id}-error`} role="alert">
          {error}
        </p>
      )}
      <input type="hidden" name={name} value={retained ? value : (selected?.toISOString() ?? '')} />
    </fieldset>
  );
}
