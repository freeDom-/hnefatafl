import { describe, expect, it } from 'vitest';
import { ratingGeometry, type RatingChartInput } from './RatingChart';

const point = (rating: number, recorded: string | null, corrected = false): RatingChartInput => ({
  rating_after: rating,
  recorded_at: recorded,
  corrected,
});

describe('ratingGeometry', () => {
  it('plots the applied ledger chronologically with authoritative rating values', () => {
    const geometry = ratingGeometry(
      [
        point(1530, '2025-01-03T00:00:00Z'),
        point(1520, '2025-01-02T00:00:00Z', true),
        point(1510, null),
      ],
      100,
      100,
      { top: 0, right: 0, bottom: 0, left: 0 },
    );
    expect(geometry).not.toBeNull();
    // Newest-first input becomes oldest-first geometry.
    expect(geometry!.points.map((entry) => entry.x)).toStrictEqual([0, 50, 100]);
    expect(geometry!.points[0]).toStrictEqual({ x: 0, y: 91.67, gap: true, corrected: false });
    expect(geometry!.points[1].corrected).toBe(true);
    expect(geometry!.points[1].gap).toBe(false);
    expect(geometry!.path).toBe('M0 91.67 L50 50 L100 8.33');
    expect(geometry!.min).toBe(1510);
    expect(geometry!.max).toBe(1530);
    expect(geometry!.ticks.map((tick) => tick.value)).toStrictEqual([1530, 1520, 1510]);
  });

  it('keeps a single entry and a flat series visible instead of dividing by a zero range', () => {
    const single = ratingGeometry([point(1500, '2025-01-01T00:00:00Z')], 100, 100, {
      top: 0,
      right: 0,
      bottom: 0,
      left: 0,
    })!;
    expect(single.points).toStrictEqual([{ x: 50, y: 50, gap: false, corrected: false }]);
    const flat = ratingGeometry(
      [point(1500, '2025-01-02T00:00:00Z'), point(1500, '2025-01-01T00:00:00Z')],
      100,
      100,
      { top: 0, right: 0, bottom: 0, left: 0 },
    )!;
    expect(flat.points.map((entry) => entry.y)).toStrictEqual([50, 50]);
    expect(flat.ticks).toStrictEqual([{ value: 1500, y: 50 }]);
  });

  it('returns nothing for an empty series rather than inventing a baseline', () => {
    expect(ratingGeometry([])).toBeNull();
  });
});
