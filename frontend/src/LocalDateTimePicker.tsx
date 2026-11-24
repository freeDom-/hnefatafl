import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
} from 'react';
import { DayPicker } from 'react-day-picker';
import { de, enUS } from 'react-day-picker/locale';
import { useI18n } from './i18n';
import { localDateTimeInstants, localDateTimeValue } from './localDateTime';
import './localDateTimePicker.css';

/** The native top layer escapes modal scrolling while the DOM stays inside its focus boundary. */
function formatNumericInput(value: string, groups: number[], separator: string): string {
  // Mobile numeric keypads cannot type the separators, so build them from digits.
  // Existing separators are boundaries, not stray characters: preserving them
  // keeps a partially edited month like "2035-2-01" in the intended segment.
  if ([...value].some((character) => !/\d/.test(character) && character !== separator))
    return value;
  if (!/\d/.test(value)) return value;
  const maximum = groups.reduce((total, size) => total + size, 0);
  const tokens = value.split(separator);
  const fits = tokens.length <= groups.length;
  const segments = tokens.every(
    (token, index) =>
      fits && token.length <= groups[index] && (index === tokens.length - 1 || token.length > 0),
  );
  if (segments) return tokens.map((token, index) => token.slice(0, groups[index])).join(separator);

  // Missing or overflowing separators: regroup the digits from the mask.
  const digits = value.replace(/\D/g, '').slice(0, maximum);
  const parts: string[] = [];
  let offset = 0;
  for (const size of groups) {
    if (offset >= digits.length) break;
    parts.push(digits.slice(offset, offset + size));
    offset += size;
  }
  const formatted = parts.join(separator);
  // Keep a trailing separator the user just typed before the next digit arrives.
  return value.endsWith(separator) && digits.length < maximum
    ? `${formatted}${separator}`
    : formatted;
}

function countDigits(value: string): number {
  return (value.match(/\d/g) ?? []).length;
}

function cursorAfterDigits(value: string, digits: number): number {
  if (digits <= 0) return 0;
  let seen = 0;
  for (const [index, character] of [...value].entries()) {
    if (!/\d/.test(character)) continue;
    seen += 1;
    if (seen === digits) return index + 1;
  }
  return value.length;
}

function formatWithCursor(
  value: string,
  cursor: number,
  groups: number[],
  separator: string,
): { value: string; cursor: number } {
  const formatted = formatNumericInput(value, groups, separator);
  if (formatted === value) return { value: formatted, cursor: Math.min(cursor, value.length) };
  return {
    value: formatted,
    cursor: cursorAfterDigits(formatted, countDigits(value.slice(0, cursor))),
  };
}

function handleSeparatorKey(
  event: ReactKeyboardEvent<HTMLInputElement>,
  value: string,
  separator: string,
  apply: (value: string, cursor: number) => void,
): boolean {
  const input = event.currentTarget;
  if (input.selectionStart !== input.selectionEnd || input.selectionStart === null) return false;
  const cursor = input.selectionStart;
  if (event.key === 'Backspace' && cursor > 1 && value[cursor - 1] === separator) {
    // Delete the preceding digit rather than the auto-inserted separator.
    event.preventDefault();
    apply(value.slice(0, cursor - 2) + value.slice(cursor - 1), cursor - 2);
    return true;
  }
  if (event.key === 'Delete' && cursor + 1 < value.length && value[cursor] === separator) {
    // Delete the following digit rather than the auto-inserted separator.
    event.preventDefault();
    apply(value.slice(0, cursor + 1) + value.slice(cursor + 2), cursor);
    return true;
  }
  return false;
}

const formatDateInput = (value: string, cursor: number) =>
  formatWithCursor(value, cursor, [4, 2, 2], '-');
const formatTimeInput = (value: string, cursor: number) =>
  formatWithCursor(value, cursor, [2, 2], ':');

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
  required = false,
}: {
  name: string;
  label: string;
  value?: string;
  required?: boolean;
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
  const coarsePointer =
    typeof window !== 'undefined' && window.matchMedia?.('(pointer: coarse)').matches === true;
  const field = useRef<HTMLInputElement>(null),
    timeField = useRef<HTMLInputElement>(null);
  const pendingDateCursor = useRef<number | null>(null),
    pendingTimeCursor = useRef<number | null>(null);
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
  const applyDateInput = (value: string, cursor: number) => {
    const formatted = formatDateInput(value, cursor);
    pendingDateCursor.current = formatted.cursor;
    chooseDate(formatted.value);
  };
  const applyTimeInput = (value: string, cursor: number) => {
    const formatted = formatTimeInput(value, cursor);
    pendingTimeCursor.current = formatted.cursor;
    setTime(formatted.value);
    setOccurrence('0');
  };
  useLayoutEffect(() => {
    if (pendingDateCursor.current !== null && field.current) {
      field.current.setSelectionRange(pendingDateCursor.current, pendingDateCursor.current);
      pendingDateCursor.current = null;
    }
    if (pendingTimeCursor.current !== null && timeField.current) {
      timeField.current.setSelectionRange(pendingTimeCursor.current, pendingTimeCursor.current);
      pendingTimeCursor.current = null;
    }
  });
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
              inputMode="numeric"
              autoComplete="off"
              spellCheck={false}
              placeholder="YYYY-MM-DD"
              value={date}
              onChange={(event) => {
                const cursor =
                  event.currentTarget.selectionStart ?? event.currentTarget.value.length;
                applyDateInput(event.currentTarget.value, cursor);
              }}
              onClick={() => {
                // Touch users tap the field to type; the calendar icon and keyboard
                // shortcuts still open the popup without covering the numeric entry.
                if (!coarsePointer) setPopup({ focus: false });
              }}
              onKeyDown={(event) => {
                if (handleSeparatorKey(event, date, '-', applyDateInput)) return;
                if (event.key === 'ArrowDown' || event.key === 'Enter') {
                  event.preventDefault();
                  setPopup({ focus: true });
                }
              }}
              aria-haspopup="dialog"
              aria-expanded={!!popup}
              aria-controls={`${id}-calendar`}
              aria-invalid={!!dateError}
              aria-required={required || undefined}
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
            inputMode="numeric"
            autoComplete="off"
            spellCheck={false}
            placeholder="HH:MM"
            value={time}
            onChange={(event) => {
              const cursor = event.currentTarget.selectionStart ?? event.currentTarget.value.length;
              applyTimeInput(event.currentTarget.value, cursor);
            }}
            onKeyDown={(event) => {
              handleSeparatorKey(event, time, ':', applyTimeInput);
            }}
            aria-invalid={!!timeError}
            aria-required={required || undefined}
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
