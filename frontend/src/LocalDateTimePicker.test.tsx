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
