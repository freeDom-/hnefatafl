export const birthDateError = 'Enter a valid date between 1900-01-01 and today (UTC).';

/** Match strict ISO calendar validation on the server, without inventing a birthday. */
export function profileDate(value: string | null, today = new Date().toISOString().slice(0, 10)) {
  if (value === null || value === '') return null;
  const parsed = new Date(`${value}T00:00:00Z`);
  if (
    !/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value) ||
    value < '1900-01-01' ||
    value > today ||
    Number.isNaN(parsed.getTime()) ||
    parsed.toISOString().slice(0, 10) !== value
  )
    throw new Error(birthDateError);
  return value;
}
