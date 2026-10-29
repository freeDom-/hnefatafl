export const MOVE_MIN_SECONDS = 60;
export const MOVE_MAX_SECONDS = 14 * 86400;
export function durationParts(seconds: number) {
  return {
    days: String(Math.floor(seconds / 86400)),
    hours: String(Math.floor((seconds % 86400) / 3600)),
    minutes: String(Math.floor((seconds % 3600) / 60)),
    remainder: seconds % 60,
  };
}
export function moveSeconds(days: string, hours: string, minutes: string): number {
  const parts = [days, hours, minutes];
  if (
    parts.some((value) => !/^\d+$/.test(value)) ||
    parts.some((value) => !Number.isSafeInteger(Number(value)))
  )
    throw new Error('Enter whole numbers for days, hours and minutes.');
  const [d, h, m] = parts.map(Number);
  if (d > 14 || h > 23 || m > 59) throw new Error('Use 0–14 days, 0–23 hours and 0–59 minutes.');
  const total = d * 86400 + h * 3600 + m * 60;
  if (total < MOVE_MIN_SECONDS || total > MOVE_MAX_SECONDS)
    throw new Error('Time limit must be at least 1 minute and at most 14 days in total.');
  return total;
}
