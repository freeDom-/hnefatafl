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
export default memo(function TrainingMetricChart({
  title,
  rows,
  series,
  unit = 'Loss',
  score = false,
  gaps = false,
}: {
  title: string;
  rows: HistoryPoint[];
  series: ChartSeries[];
  unit?: string;
  score?: boolean;
  gaps?: boolean;
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
  const maxIteration = Math.max(minIteration + 1, lastIteration);
  const maximum = useMemo(
    () =>
      score
        ? 1
        : rows.reduce(
            (max, row) =>
              series.reduce(
                (max, item) =>
                  finite(row[item.key]) ? Math.max(max, row[item.key] as number) : max,
                max,
              ),
            0.001,
          ) * 1.08,
    [rows, series, score],
  );
  const hasData = rows.some((row) => series.some((item) => finite(row[item.key])));
  const x = (value: number) =>
    left + ((value - minIteration) / (maxIteration - minIteration)) * (width - left - right);
  const y = (value: number) => height - bottom - (value / maximum) * (height - top - bottom);
  const format = (value: unknown, precise = true) =>
    finite(value)
      ? new Intl.NumberFormat(locale, {
          maximumSignificantDigits: precise ? 10 : 3,
          ...(typeof value === 'number' && value !== 0 && Math.abs(value) < 0.0001 && !precise
            ? { notation: 'scientific' as const }
            : {}),
          ...(score ? { style: 'percent' as const } : {}),
        }).format(value)
      : '—';
  const paths = useMemo(
    () =>
      series.map((item) => {
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
              const command = drawing ? 'L' : 'M';
              drawing = true;
              return `${command}${x(row.iteration).toFixed(2)},${y(row[item.key] as number).toFixed(2)}`;
            })
            .join(' '),
        };
        // Coordinate functions are entirely determined by these scalar bounds.
      }),
    [rows, series, width, maximum, minIteration, maxIteration, gaps],
  );
  const point = selected === null ? undefined : rows[Math.min(selected, rows.length - 1)];
  return (
    <figure ref={container} className="training-chart">
      <figcaption>{t(title)}</figcaption>
      <svg
        role="img"
        aria-label={t(title)}
        aria-description={t(
          'Use left and right arrow keys to inspect iterations. Exact values are available in the data table.',
        )}
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
            ? [Math.round((minIteration + lastIteration) / 2)]
            : []),
          ...(lastIteration !== minIteration ? [lastIteration] : []),
        ].map((iteration) => (
          <text key={iteration} x={x(iteration)} y={height - 24} textAnchor="middle">
            {iteration}
          </text>
        ))}
        <text x={(left + width - right) / 2} y={height - 5} textAnchor="middle">
          {t('Iteration')}
        </text>
        {paths.map((item) => (
          <g key={item.key}>
            <path
              d={item.path}
              fill="none"
              stroke={item.color}
              strokeWidth="2"
              vectorEffect="non-scaling-stroke"
            />
            {(score || rows.length <= 25) &&
              rows
                .filter((row) => finite(row[item.key]))
                .map((row) => (
                  <circle
                    key={row.iteration}
                    cx={x(row.iteration)}
                    cy={y(row[item.key] as number)}
                    r={gaps ? 3 : 2.5}
                    fill={item.color}
                  >
                    <title>
                      {t('Iteration {iteration}: {value}', {
                        iteration: row.iteration,
                        value: format(row[item.key]),
                      })}
                    </title>
                  </circle>
                ))}
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
                  {t('Champion promotion')} · {t('Iteration')} {row.iteration}
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
          ? `${t('Iteration')} ${point.iteration} · ${series.map((item) => `${t(item.name)}: ${format(point[item.key])}`).join(' · ')}`
          : t('Point to the chart or use arrow keys for exact values.')}
      </output>
    </figure>
  );
});
