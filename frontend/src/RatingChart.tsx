import { useEffect, useRef, useState } from 'react';
import { useI18n } from './i18n';

/** Lightweight SVG rating line, so no charting dependency is added.
 *
 * Only authoritative `rating_after` values are plotted. A point with no trusted
 * completion stamp is drawn as an explicit gap marker; a corrected result is
 * drawn as a diamond. A point can be both, so the two markers overlap and keep
 * their ledger sequence position. */
export const RATING_CHART_WIDTH = 640;
export const RATING_CHART_HEIGHT = 168;
export const RATING_CHART_PADDING = { top: 14, right: 14, bottom: 26, left: 48 } as const;

export type RatingChartInput = {
  rating_after: number;
  /** Null means an explicit legacy timestamp gap, not a fabricated date. */
  recorded_at: string | null;
  corrected: boolean;
};

export type RatingChartPoint = {
  x: number;
  y: number;
  gap: boolean;
  corrected: boolean;
};

export type RatingGeometry = {
  /** Oldest ledger entry first, newest last. */
  points: RatingChartPoint[];
  path: string;
  min: number;
  max: number;
  ticks: { value: number; y: number }[];
};

const round = (value: number) => Math.round(value * 100) / 100;

/** Three readable labels, deduplicated for a flat or very narrow range. */
function tickValues(min: number, max: number): number[] {
  if (min === max) return [Math.round(min)];
  return [...new Set([max, (min + max) / 2, min].map((value) => Math.round(value)))];
}

/**
 * Chronological geometry for one pool. Returns null for an empty series rather
 * than inventing a baseline. A single entry or a flat line still gets a visible
 * vertical band so the point is not drawn on the axis edge.
 */
export function ratingGeometry(
  entries: readonly RatingChartInput[],
  width = RATING_CHART_WIDTH,
  height = RATING_CHART_HEIGHT,
  padding: { top: number; right: number; bottom: number; left: number } = RATING_CHART_PADDING,
): RatingGeometry | null {
  if (!entries.length) return null;
  const chronological = [...entries].reverse();
  const values = chronological.map((entry) => entry.rating_after);
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const span = rawMax - rawMin;
  const band = span === 0 ? Math.max(8, Math.abs(rawMax) * 0.01) : span * 0.1;
  const min = rawMin - band;
  const max = rawMax + band;
  const innerWidth = width - padding.left - padding.right;
  const innerHeight = height - padding.top - padding.bottom;
  const x = (index: number) =>
    padding.left +
    (chronological.length === 1
      ? innerWidth / 2
      : (index * innerWidth) / (chronological.length - 1));
  const y = (value: number) =>
    padding.top + innerHeight - ((value - min) / (max - min)) * innerHeight;
  const points = chronological.map((entry, index) => ({
    x: round(x(index)),
    y: round(y(entry.rating_after)),
    gap: entry.recorded_at === null,
    corrected: entry.corrected,
  }));
  return {
    points,
    path: points.map((point, index) => `${index ? 'L' : 'M'}${point.x} ${point.y}`).join(' '),
    min: rawMin,
    max: rawMax,
    ticks: tickValues(rawMin, rawMax).map((value) => ({ value, y: round(y(value)) })),
  };
}

export default function RatingChart({
  entries,
  label,
}: {
  entries: readonly RatingChartInput[];
  label: string;
}) {
  const { locale } = useI18n();
  const chart = useRef<SVGSVGElement>(null);
  const [width, setWidth] = useState(RATING_CHART_WIDTH);
  // The fixed viewBox is scaled into a narrow card, so tick text would render
  // unreadably small. Match the viewBox to the measured card width instead.
  useEffect(() => {
    if (!chart.current || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.max(270, Math.round(entry.contentRect.width))),
    );
    observer.observe(chart.current);
    return () => observer.disconnect();
  }, []);
  const geometry = ratingGeometry(entries, width);
  if (!geometry) return null;
  const number = new Intl.NumberFormat(locale);
  return (
    <svg
      ref={chart}
      className="rating-chart"
      viewBox={`0 0 ${width} ${RATING_CHART_HEIGHT}`}
      role="img"
      aria-label={label}
    >
      {geometry.ticks.map((tick) => (
        <g key={tick.value} className="rating-chart-tick">
          <line
            x1={RATING_CHART_PADDING.left}
            x2={width - RATING_CHART_PADDING.right}
            y1={tick.y}
            y2={tick.y}
          />
          <text x={RATING_CHART_PADDING.left - 6} y={tick.y + 3}>
            {number.format(tick.value)}
          </text>
        </g>
      ))}
      <path className="rating-chart-line" d={geometry.path} />
      {geometry.points.map((point, index) => (
        <g key={index}>
          {/* A point can be both corrected and missing a trusted date: the gold
              diamond marks the correction and the hollow ring marks the gap, so
              neither state hides the other. */}
          {point.gap && (
            <circle
              className={`rating-chart-point rating-chart-gap${
                point.corrected ? ' rating-chart-corrected-gap' : ''
              }`}
              cx={point.x}
              cy={point.y}
              r={point.corrected ? 5 : 3}
            />
          )}
          {point.corrected && (
            <path
              className="rating-chart-point rating-chart-corrected"
              d={`M${point.x} ${point.y - 4}L${point.x + 4} ${point.y}L${point.x} ${point.y + 4}L${point.x - 4} ${point.y}Z`}
            />
          )}
          {!point.gap && !point.corrected && (
            <circle className="rating-chart-point" cx={point.x} cy={point.y} r={2.5} />
          )}
        </g>
      ))}
    </svg>
  );
}
