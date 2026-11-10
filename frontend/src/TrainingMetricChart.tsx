import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { useI18n } from './i18n';
import { finite, type HistoryPoint } from './trainingDashboardData';

export interface ChartSeries {
  key: string;
  name: string;
  color: string;
}
export const policySeries: ChartSeries[] = [
  { key: 'policy_loss', name: 'Policy loss', color: 'var(--chart-policy)' },
  { key: 'value_loss', name: 'Value loss', color: 'var(--chart-value)' },
];
/**
 * Indices of the markers to draw for one series. Connected stretches are
 * already visible through the path, so a dense evaluation series only keeps the
 * markers that would otherwise disappear: the first and last observation and
 * both sides of a missing value or an iteration gap. Small series and explicit
 * scatter plots keep every recorded point. `rows` is chronological, so the
 * result preserves order.
 */
function markerIndices(
  rows: HistoryPoint[],
  key: string,
  gaps: boolean,
  all: boolean,
  endpoints: boolean,
): number[] {
  const recorded: number[] = [];
  for (let index = 0; index < rows.length; index += 1)
    if (finite(rows[index][key])) recorded.push(index);
  if (all) return recorded;
  // A continuous path already joins every point, so a lone observation is the
  // only one that would vanish without a marker.
  if (!gaps) return recorded.length === 1 ? recorded : [];
  return recorded.filter((index) => {
    const previous = index > 0 ? rows[index - 1] : undefined;
    const next = index < rows.length - 1 ? rows[index + 1] : undefined;
    const connectedBefore =
      !!previous && finite(previous[key]) && previous.iteration + 1 === rows[index].iteration;
    const connectedAfter =
      !!next && finite(next[key]) && next.iteration === rows[index].iteration + 1;
    return endpoints ? !connectedBefore || !connectedAfter : !connectedBefore && !connectedAfter;
  });
}

/**
 * Constant step stretches need only their endpoints. Keep each true transition
 * at its exact iteration, finish before every gap, and retain all input rows for
 * pointer/keyboard inspection and the exact-value table.
 */
function steppedPath(
  rows: HistoryPoint[],
  key: string,
  gaps: boolean,
  x: (iteration: number) => number,
  y: (value: number) => number,
): string {
  const commands: string[] = [];
  let previousIteration: number | undefined;
  let previousValue: number | undefined;
  let lastEmitted: number | undefined;
  const finish = () => {
    if (previousIteration !== undefined && previousIteration !== lastEmitted)
      commands.push(`H${x(previousIteration).toFixed(2)}`);
    previousIteration = undefined;
    previousValue = undefined;
    lastEmitted = undefined;
  };
  for (const row of rows) {
    const value = row[key];
    if (!finite(value)) {
      if (gaps) finish();
      continue;
    }
    if (gaps && previousIteration !== undefined && row.iteration > previousIteration + 1) finish();
    if (previousIteration === undefined) {
      commands.push(`M${x(row.iteration).toFixed(2)},${y(value).toFixed(2)}`);
      lastEmitted = row.iteration;
    } else if (value !== previousValue) {
      commands.push(`H${x(row.iteration).toFixed(2)}V${y(value).toFixed(2)}`);
      lastEmitted = row.iteration;
    }
    previousIteration = row.iteration;
    previousValue = value;
  }
  finish();
  return commands.join(' ');
}
/**
 * Upper bound of the y axis. Evaluation scores keep a fixed 0-1 scale. A
 * percentage series never exceeds 100% but still zooms in on small rates, and
 * falls back to a full scale when every observation is zero so the axis stays
 * readable. Other metrics keep their data-driven padding.
 */
export function chartMaximum(
  rows: HistoryPoint[],
  series: ChartSeries[],
  score: boolean,
  percent: boolean,
  smallScale = false,
): number {
  if (score) return 1;
  const dataMax = rows.reduce(
    (max, row) =>
      series.reduce(
        (max, item) => (finite(row[item.key]) ? Math.max(max, row[item.key] as number) : max),
        max,
      ),
    0,
  );
  if (percent) return dataMax > 0 ? Math.min(1, dataMax * 1.08) : 1;
  return (smallScale && dataMax > 0 ? dataMax : Math.max(dataMax, 0.001)) * 1.08;
}
export default memo(function TrainingMetricChart({
  title,
  rows,
  series,
  unit = 'Loss',
  score = false,
  percent = false,
  gaps = false,
  xLabel = 'Iteration',
  scatter = false,
  step = false,
  smallScale = false,
  description,
  precision = 10,
}: {
  title: string;
  rows: HistoryPoint[];
  series: ChartSeries[];
  unit?: string;
  score?: boolean;
  /** Format values as percentages without the fixed 0-1 scale that `score` implies. */
  percent?: boolean;
  gaps?: boolean;
  xLabel?: string;
  scatter?: boolean;
  /** Configuration changes take effect at the new iteration, without interpolation. */
  step?: boolean;
  /** Opt-in data scale for tiny rates/coefficients; other metric scales stay unchanged. */
  smallScale?: boolean;
  description?: string;
  precision?: number;
}) {
  const { t, locale } = useI18n();
  const container = useRef<HTMLElement>(null);
  const [width, setWidth] = useState(520);
  const [selected, setSelected] = useState<number | null>(null);
  useEffect(() => {
    if (!container.current || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.max(270, Math.round(entry.contentRect.width))),
    );
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  const height = 230,
    left = 58,
    right = 16,
    top = 24,
    bottom = 43;
  const minIteration = rows[0]?.iteration ?? 0;
  const lastIteration = rows.at(-1)?.iteration ?? minIteration;
  const maxIteration = lastIteration > minIteration ? lastIteration : minIteration + 1;
  const maximum = useMemo(
    () => chartMaximum(rows, series, score, percent, smallScale),
    [rows, series, score, percent, smallScale],
  );
  const hasData = rows.some((row) => series.some((item) => finite(row[item.key])));
  const x = (value: number) =>
    left + ((value - minIteration) / (maxIteration - minIteration)) * (width - left - right);
  const y = (value: number) => height - bottom - (value / maximum) * (height - top - bottom);
  const format = (value: unknown, precise = true) =>
    finite(value)
      ? new Intl.NumberFormat(locale, {
          maximumSignificantDigits: precise ? precision : 3,
          ...(value !== 0 && Math.abs(value) < 0.0001 && (!precise || smallScale)
            ? { notation: 'scientific' as const }
            : {}),
          ...(score || percent ? { style: 'percent' as const } : {}),
        }).format(value)
      : '—';
  const paths = useMemo(
    () =>
      series.map((item) => {
        if (step) return { ...item, path: steppedPath(rows, item.key, gaps, x, y) };
        let drawing = false;
        let previousIteration: number | null = null;
        return {
          ...item,
          path: rows
            .map((row) => {
              if (!finite(row[item.key])) {
                if (gaps) drawing = false;
                return '';
              }
              if (gaps && previousIteration !== null && row.iteration > previousIteration + 1)
                drawing = false;
              previousIteration = row.iteration;
              const coordinate = `${x(row.iteration).toFixed(2)},${y(row[item.key] as number).toFixed(2)}`;
              const command = drawing ? `L${coordinate}` : `M${coordinate}`;
              drawing = true;
              return command;
            })
            .join(' '),
        };
        // Coordinate functions are entirely determined by these scalar bounds.
      }),
    [rows, series, width, maximum, minIteration, maxIteration, gaps, step],
  );
  // Dense charts follow the data instead of forcing a circle per observation:
  // non-score series only mark small datasets, while dense evaluation series
  // keep the endpoints their broken path cannot draw. Scatter plots stay exact.
  const markers = useMemo(() => {
    const all = scatter || rows.length <= 25;
    return new Map(
      series.map((item): [string, number[]] => [
        item.key,
        all || score || gaps ? markerIndices(rows, item.key, gaps, all, score) : [],
      ]),
    );
  }, [rows, series, gaps, scatter, score]);
  const point = selected === null ? undefined : rows[Math.min(selected, rows.length - 1)];
  return (
    <figure ref={container} className="training-chart">
      <figcaption>{t(title)}</figcaption>
      <svg
        role="img"
        aria-label={t(title)}
        aria-description={
          (description ? `${t(title)} · ${t(unit)}. ${t(description)} ` : '') +
          t(
            'Use left and right arrow keys to inspect iterations. Exact values are available in the data table.',
          )
        }
        tabIndex={hasData ? 0 : undefined}
        viewBox={`0 0 ${width} ${height}`}
        onKeyDown={(event) => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          setSelected((current) =>
            event.key === 'Home'
              ? 0
              : event.key === 'End'
                ? rows.length - 1
                : Math.max(
                    0,
                    Math.min(
                      rows.length - 1,
                      (current ?? rows.length - 1) + (event.key === 'ArrowLeft' ? -1 : 1),
                    ),
                  ),
          );
        }}
        onPointerMove={(event) => {
          const bounds = event.currentTarget.getBoundingClientRect();
          const iteration =
            minIteration +
            ((((event.clientX - bounds.left) / bounds.width) * width - left) /
              (width - left - right)) *
              (maxIteration - minIteration);
          let low = 0,
            high = rows.length - 1;
          while (low < high) {
            const middle = Math.floor((low + high) / 2);
            if (rows[middle].iteration < iteration) low = middle + 1;
            else high = middle;
          }
          if (
            low > 0 &&
            Math.abs(rows[low - 1].iteration - iteration) <
              Math.abs((rows[low]?.iteration ?? 0) - iteration)
          )
            low--;
          setSelected(low);
        }}
        onPointerLeave={() => setSelected(null)}
      >
        <title>{t(title)}</title>
        <text x={left} y={14} className="chart-unit">
          {t(score ? 'Score (%)' : unit)}
        </text>
        {[0, 0.25, 0.5, 0.75, 1].map((fraction) => (
          <g key={fraction}>
            <line
              x1={left}
              x2={width - right}
              y1={y(maximum * fraction)}
              y2={y(maximum * fraction)}
              className="chart-gridline"
            />
            <text x={left - 8} y={y(maximum * fraction) + 4} textAnchor="end">
              {format(maximum * fraction, false)}
            </text>
          </g>
        ))}
        {[
          minIteration,
          ...(lastIteration - minIteration > 2
            ? [
                xLabel === 'Iteration' || xLabel === 'Trial'
                  ? Math.round((minIteration + lastIteration) / 2)
                  : (minIteration + lastIteration) / 2,
              ]
            : []),
          ...(lastIteration !== minIteration ? [lastIteration] : []),
        ].map((iteration) => (
          <text key={iteration} x={x(iteration)} y={height - 24} textAnchor="middle">
            {iteration}
          </text>
        ))}
        <text x={(left + width - right) / 2} y={height - 5} textAnchor="middle">
          {t(xLabel)}
        </text>
        {paths.map((item) => (
          <g key={item.key}>
            {!scatter && (
              <path
                d={item.path}
                fill="none"
                stroke={item.color}
                strokeWidth="2"
                vectorEffect="non-scaling-stroke"
              />
            )}
            {markers.get(item.key)!.map((index) => {
              const row = rows[index];
              return (
                <circle
                  key={index}
                  cx={x(row.iteration)}
                  cy={y(row[item.key] as number)}
                  r={gaps ? 3 : 2.5}
                  fill={item.color}
                >
                  <title>{`${t(xLabel)} ${row.iteration}: ${format(row[item.key])}`}</title>
                </circle>
              );
            })}
          </g>
        ))}
        {(score ? rows : [])
          .filter((row) => row.evaluation?.promoted)
          .map((row) => (
            <g key={`promotion-${row.iteration}`} className="chart-promotion">
              <line
                x1={x(row.iteration)}
                x2={x(row.iteration)}
                y1={top}
                y2={height - bottom}
                stroke="var(--gold)"
                strokeDasharray="4 4"
              />
              <text x={x(row.iteration)} y={top - 7} textAnchor="middle">
                ◆
                <title>
                  {t('Champion promotion')} · {t(xLabel)} {row.iteration}
                </title>
              </text>
            </g>
          ))}
        {point && (
          <line
            className="chart-crosshair"
            x1={x(point.iteration)}
            x2={x(point.iteration)}
            y1={top}
            y2={height - bottom}
          />
        )}
        {!hasData && (
          <text x={(left + width - right) / 2} y={height / 2} textAnchor="middle">
            {t('No recorded observations')}
          </text>
        )}
      </svg>
      <div className="training-chart-legend">
        {series.map((item) => (
          <span key={item.key}>
            <i style={{ background: item.color }} />
            {t(item.name)}
          </span>
        ))}
      </div>
      <output className="chart-tooltip" aria-live="polite">
        {point
          ? `${t(xLabel)} ${point.iteration} · ${series.map((item) => `${t(item.name)}: ${format(point[item.key])}`).join(' · ')}`
          : t('Point to the chart or use arrow keys for exact values.')}
      </output>
    </figure>
  );
});
