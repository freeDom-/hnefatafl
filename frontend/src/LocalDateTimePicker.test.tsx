import { cleanup, fireEvent, render, screen } from '@testing-library/react';
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
  fireEvent.click(screen.getByText('Datum auswählen'));
  expect(screen.getByRole('button', { name: 'Nächster Monat' })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Datum (JJJJ-MM-TT)'), {
    target: { value: '2035-02-30' },
  });
  expect(screen.getByRole('alert')).toHaveTextContent('existiert nicht');
});
