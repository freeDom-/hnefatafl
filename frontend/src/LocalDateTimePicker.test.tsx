import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it } from 'vitest';
import LocalDateTimePicker from './LocalDateTimePicker';
import { localDateTimeInstants } from './localDateTime';
import { setLanguage } from './i18n';

afterEach(() => {
  cleanup();
  setLanguage('en');
});
it('rejects impossible dates and invalid times instead of silently normalizing them', () => {
  for (const value of [
    '2035-02-30T12:00',
    '2035-01-01T24:00',
    '2035-13-01T12:00',
    '2035-01-01T12:60',
    'invalid',
  ])
    expect(localDateTimeInstants(value)).toEqual([]);
  render(<LocalDateTimePicker name="start" label="Planned start" />);
  const date = screen.getByLabelText('Date (YYYY-MM-DD)');
  fireEvent.change(date, { target: { value: '2035-02-30' } });
  expect(screen.getByRole('alert')).toHaveTextContent('does not exist');
  expect(date).toBeInvalid();
  expect(document.querySelector('input[name=start]')).toHaveValue('');
});
it('restores the local date and time for a stored UTC instant and clears the optional field', async () => {
  const instant = new Date(2035, 1, 1, 18, 30).toISOString();
  render(<LocalDateTimePicker name="start" label="Planned start" value={instant} />);
  expect(screen.getByLabelText('Date (YYYY-MM-DD)')).toHaveValue('2035-02-01');
  expect(screen.getByLabelText('Time (HH:MM)')).toHaveValue('18:30');
  expect(document.querySelector('input[name=start]')).toHaveValue(instant);
  await userEvent.click(screen.getByRole('button', { name: 'Clear date' }));
  expect(document.querySelector('input[name=start]')).toHaveValue('');
  expect(screen.getByLabelText('Date (YYYY-MM-DD)')).toBeValid();
});
it('localizes the calendar navigation and validation for German readers', () => {
  setLanguage('de');
  render(<LocalDateTimePicker name="start" label="Planned start" />);
  fireEvent.click(screen.getByRole('button', { name: 'Kalender öffnen' }));
  expect(screen.getByRole('button', { name: 'Nächster Monat' })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Datum (JJJJ-MM-TT)'), {
    target: { value: '2035-02-30' },
  });
  expect(screen.getByRole('alert')).toHaveTextContent('existiert nicht');
});

it('accepts numeric keypad digits and inserts the date and time separators', async () => {
  const user = userEvent.setup();
  render(<LocalDateTimePicker name="start" label="Planned start" />);
  const date = screen.getByLabelText('Date (YYYY-MM-DD)') as HTMLInputElement;
  const time = screen.getByLabelText('Time (HH:MM)') as HTMLInputElement;
  expect(date).toHaveAttribute('inputmode', 'numeric');
  expect(time).toHaveAttribute('inputmode', 'numeric');
  await user.type(date, '20350201');
  await user.clear(time);
  await user.type(time, '1830');
  expect(date).toHaveValue('2035-02-01');
  expect(time).toHaveValue('18:30');
  expect(document.querySelector('input[name=start]')).toHaveValue(
    new Date(2035, 1, 1, 18, 30).toISOString(),
  );
});

it('lets coarse-pointer users tap the date field to type while keeping the calendar icon', async () => {
  const original = window.matchMedia;
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({ matches: query === '(pointer: coarse)' }) as MediaQueryList,
  });
  try {
    const user = userEvent.setup();
    render(<LocalDateTimePicker name="start" label="Planned start" />);
    const date = screen.getByLabelText('Date (YYYY-MM-DD)');
    await user.click(date);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await user.type(date, '20350201');
    expect(date).toHaveValue('2035-02-01');
    fireEvent.click(screen.getByRole('button', { name: 'Open calendar' }));
    expect(screen.getByRole('dialog', { name: 'Choose date' })).toBeInTheDocument();
  } finally {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: original,
    });
  }
});

it('does not regroup the reported partial date and time values', () => {
  render(<LocalDateTimePicker name="start" label="Planned start" />);
  const date = screen.getByLabelText('Date (YYYY-MM-DD)') as HTMLInputElement;
  const time = screen.getByLabelText('Time (HH:MM)') as HTMLInputElement;
  fireEvent.change(date, { target: { value: '2035-2-01' } });
  expect(date).toHaveValue('2035-2-01');
  fireEvent.change(time, { target: { value: '1:30' } });
  expect(time).toHaveValue('1:30');
});

it('preserves segments and cursor position when editing in the middle', async () => {
  const user = userEvent.setup();
  render(
    <LocalDateTimePicker
      name="start"
      label="Planned start"
      value={new Date(2035, 1, 1, 18, 30).toISOString()}
    />,
  );
  const date = screen.getByLabelText('Date (YYYY-MM-DD)') as HTMLInputElement;
  const time = screen.getByLabelText('Time (HH:MM)') as HTMLInputElement;
  const hidden = document.querySelector('input[name=start]')!;

  date.focus();
  date.setSelectionRange(5, 7);
  await user.keyboard('12');
  expect(date).toHaveValue('2035-12-01');
  expect(date.selectionStart).toBe(7);
  expect(hidden).toHaveValue(new Date(2035, 11, 1, 18, 30).toISOString());

  date.setSelectionRange(8, 10);
  await user.keyboard('09');
  expect(date).toHaveValue('2035-12-09');
  expect(hidden).toHaveValue(new Date(2035, 11, 9, 18, 30).toISOString());

  time.focus();
  time.setSelectionRange(0, 2);
  await user.keyboard('09');
  expect(time).toHaveValue('09:30');
  time.setSelectionRange(3, 5);
  await user.keyboard('45');
  expect(time).toHaveValue('09:45');
  expect(hidden).toHaveValue(new Date(2035, 11, 9, 9, 45).toISOString());
});

it('keeps partial month and time segments instead of regrouping their digits', async () => {
  const user = userEvent.setup();
  render(
    <LocalDateTimePicker
      name="start"
      label="Planned start"
      value={new Date(2035, 1, 1, 18, 30).toISOString()}
    />,
  );
  const date = screen.getByLabelText('Date (YYYY-MM-DD)') as HTMLInputElement;
  const time = screen.getByLabelText('Time (HH:MM)') as HTMLInputElement;

  date.focus();
  date.setSelectionRange(6, 6);
  await user.keyboard('{Backspace}');
  expect(date).toHaveValue('2035-2-01');
  expect(date.selectionStart).toBe(5);

  time.focus();
  time.setSelectionRange(0, 2);
  await user.keyboard('1');
  expect(time).toHaveValue('1:30');
  expect(time.selectionStart).toBe(1);
});

it('handles Backspace and Delete at separators without corrupting segments', async () => {
  const user = userEvent.setup();
  render(
    <LocalDateTimePicker
      name="start"
      label="Planned start"
      value={new Date(2035, 1, 1, 18, 30).toISOString()}
    />,
  );
  const date = screen.getByLabelText('Date (YYYY-MM-DD)') as HTMLInputElement;
  const time = screen.getByLabelText('Time (HH:MM)') as HTMLInputElement;

  date.focus();
  date.setSelectionRange(5, 5);
  await user.keyboard('{Backspace}');
  expect(date).toHaveValue('203-02-01');
  expect(date.selectionStart).toBe(3);
  await user.keyboard('5');
  expect(date).toHaveValue('2035-02-01');

  date.setSelectionRange(4, 4);
  await user.keyboard('{Delete}');
  expect(date).toHaveValue('2035-2-01');

  time.focus();
  time.setSelectionRange(3, 3);
  await user.keyboard('{Backspace}');
  expect(time).toHaveValue('1:30');
  expect(time.selectionStart).toBe(1);
});

it('supports selection replacement and pasting complete or partial values', async () => {
  const user = userEvent.setup();
  render(
    <LocalDateTimePicker
      name="start"
      label="Planned start"
      value={new Date(2035, 1, 1, 18, 30).toISOString()}
    />,
  );
  const date = screen.getByLabelText('Date (YYYY-MM-DD)') as HTMLInputElement;
  const time = screen.getByLabelText('Time (HH:MM)') as HTMLInputElement;

  date.focus();
  date.setSelectionRange(0, 10);
  await user.keyboard('20400506');
  expect(date).toHaveValue('2040-05-06');

  date.setSelectionRange(0, 10);
  await user.paste('2035-2-1');
  expect(date).toHaveValue('2035-2-1');

  date.setSelectionRange(0, date.value.length);
  await user.paste('20350201');
  expect(date).toHaveValue('2035-02-01');

  time.focus();
  time.setSelectionRange(0, 5);
  await user.paste('1830');
  expect(time).toHaveValue('18:30');
  time.setSelectionRange(0, 5);
  await user.paste('9:5');
  expect(time).toHaveValue('9:5');
});

it('keeps form validity in sync while editing dates and times', async () => {
  const user = userEvent.setup();
  render(
    <form>
      <LocalDateTimePicker name="start" label="Planned start" />
    </form>,
  );
  const date = screen.getByLabelText('Date (YYYY-MM-DD)') as HTMLInputElement;
  await user.type(date, '20350230');
  expect(date).toHaveValue('2035-02-30');
  expect(date).toBeInvalid();
  expect(screen.getByRole('alert')).toHaveTextContent('does not exist');

  date.setSelectionRange(8, 10);
  await user.keyboard('28');
  expect(date).toHaveValue('2035-02-28');
  expect(date).toBeValid();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(document.querySelector('input[name=start]')).toHaveValue(
    new Date(2035, 1, 28, 12, 0).toISOString(),
  );
  expect(document.querySelector('form')!.checkValidity()).toBe(true);
});

it('preserves DST gaps and repeated-hour selection when editing', async () => {
  const original = process.env.TZ;
  process.env.TZ = 'Europe/Berlin';
  try {
    const user = userEvent.setup();
    render(
      <LocalDateTimePicker name="start" label="Planned start" value="2035-10-28T00:30:00.000Z" />,
    );
    const date = screen.getByLabelText('Date (YYYY-MM-DD)');
    const time = screen.getByLabelText('Time (HH:MM)');
    const occurrence = screen.getByRole('combobox');
    expect(date).toHaveValue('2035-10-28');
    expect(time).toHaveValue('02:30');
    expect(occurrence).toHaveValue('0');
    await user.selectOptions(occurrence, '1');
    expect(document.querySelector('input[name=start]')).toHaveValue('2035-10-28T01:30:00.000Z');
    await user.clear(date);
    await user.type(date, '20350325');
    await user.clear(time);
    await user.type(time, '0230');
    expect(screen.getByRole('alert')).toHaveTextContent('does not exist');
    expect(document.querySelector('input[name=start]')).toHaveValue('');
  } finally {
    process.env.TZ = original;
  }
});

it('opens from the date field, closes after selection, and retains the typed time', async () => {
  const user = userEvent.setup();
  render(
    <LocalDateTimePicker
      name="start"
      label="Planned start"
      value={new Date(2035, 1, 1, 18, 30).toISOString()}
    />,
  );
  const date = screen.getByLabelText('Date (YYYY-MM-DD)');
  expect(screen.queryByRole('button', { name: 'Choose date' })).not.toBeInTheDocument();
  await user.click(date);
  const calendar = screen.getByRole('dialog', { name: 'Choose date' });
  await user.click(within(calendar).getByRole('button', { name: /Friday, 2 February 2035/ }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(date).toHaveValue('2035-02-02');
  expect(date).toHaveFocus();
  expect(screen.getByLabelText('Time (HH:MM)')).toHaveValue('18:30');
  expect(document.querySelector('input[name=start]')).toHaveValue(
    new Date(2035, 1, 2, 18, 30).toISOString(),
  );
});

it('opens by keyboard and dismisses with Escape or an outside click without losing input', async () => {
  const user = userEvent.setup();
  render(
    <LocalDateTimePicker
      name="start"
      label="Planned start"
      value={new Date(2035, 1, 1, 18, 30).toISOString()}
    />,
  );
  const date = screen.getByLabelText('Date (YYYY-MM-DD)');
  date.focus();
  await user.keyboard('{ArrowDown}');
  expect(screen.getByRole('dialog')).toContainElement(document.activeElement as HTMLElement);
  date.focus();
  await user.keyboard('{ArrowDown}');
  expect(screen.getByRole('dialog')).toContainElement(document.activeElement as HTMLElement);
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(date).toHaveFocus();
  await user.click(screen.getByRole('button', { name: 'Open calendar' }));
  await user.click(screen.getByLabelText('Time (HH:MM)'));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(date).toHaveValue('2035-02-01');
  expect(screen.getByLabelText('Time (HH:MM)')).toHaveValue('18:30');
});

it('blocks invalid time entry and keeps a retained instant precise until edited', async () => {
  const value = new Date(2035, 1, 1, 18, 30, 42, 123).toISOString();
  render(
    <form>
      <LocalDateTimePicker name="start" label="Planned start" value={value} />
    </form>,
  );
  expect(document.querySelector('input[name=start]')).toHaveValue(value);
  await userEvent.click(screen.getByLabelText('Date (YYYY-MM-DD)'));
  await userEvent.click(document.querySelector('[aria-selected=true] button')!);
  expect(document.querySelector('input[name=start]')).toHaveValue(value);
  const time = screen.getByLabelText('Time (HH:MM)');
  fireEvent.change(time, { target: { value: '25:30' } });
  expect(time).toBeInvalid();
  expect(document.querySelector('form')!.checkValidity()).toBe(false);
  expect(screen.getByRole('alert')).toHaveTextContent('HH:MM');
  fireEvent.change(time, { target: { value: '18:31' } });
  expect(time).toBeValid();
  expect(document.querySelector('input[name=start]')).toHaveValue(
    new Date(2035, 1, 1, 18, 31).toISOString(),
  );
});
