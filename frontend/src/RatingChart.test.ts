import { createElement } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import RatingChart, { ratingGeometry, type RatingChartInput } from './RatingChart';

afterEach(cleanup);

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

describe('RatingChart markers', () => {
  it('shows both the corrected diamond and the gap ring for a corrected entry with no date', () => {
    const { container } = render(
      createElement(RatingChart, { entries: [point(1510, null, true)], label: 'progression' }),
    );
    expect(container.querySelector('.rating-chart-corrected')).not.toBeNull();
    expect(container.querySelector('.rating-chart-gap')).not.toBeNull();
    // The ring is hollow so it never paints over the diamond.
    expect(container.querySelector('.rating-chart-corrected-gap')).not.toBeNull();
    expect(container.querySelectorAll('.rating-chart-point')).toHaveLength(2);
  });

  it('keeps one marker when only one state applies', () => {
    const { container } = render(
      createElement(RatingChart, {
        entries: [point(1530, '2025-01-03T00:00:00Z', true), point(1510, null, false)],
        label: 'progression',
      }),
    );
    expect(container.querySelectorAll('.rating-chart-corrected')).toHaveLength(1);
    expect(container.querySelectorAll('.rating-chart-gap')).toHaveLength(1);
    expect(container.querySelectorAll('.rating-chart-corrected-gap')).toHaveLength(0);
    expect(container.querySelectorAll('.rating-chart-point')).toHaveLength(2);
  });
});
