/** Return every instant matching a wall time, including both sides of a DST fold. */
export function localDateTimeInstants(value: string): Date[] {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) return [];
  const [year, month, day, hour, minute] = value.split(/[-T:]/).map(Number);
  if (year < 1000 || month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 || minute > 59)
    return [];
  const nominal = Date.UTC(year, month - 1, day, hour, minute);
  const offsets = new Set<number>();
  for (let hours = -36; hours <= 36; hours++)
    offsets.add(new Date(nominal + hours * 3600000).getTimezoneOffset());
  return [...offsets]
    .map((offset) => new Date(nominal + offset * 60000))
    .filter(
      (date) =>
        date.getFullYear() === year &&
        date.getMonth() === month - 1 &&
        date.getDate() === day &&
        date.getHours() === hour &&
        date.getMinutes() === minute,
    )
    .sort((a, b) => a.getTime() - b.getTime());
}
export function localDateTimeValue(date: Date) {
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
