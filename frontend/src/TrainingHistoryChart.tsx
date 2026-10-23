import { useI18n } from './i18n';
import './trainingHistory.css';
import { opponentName } from './EvaluationSettings';
import type { EvaluationRecord } from './TrainingEvaluationStatus';

interface Point {
  iteration: number;
  policy_loss: number;
  value_loss: number;
  evaluation_score?: number;
  evaluation_truncated?: number;
  evaluation_draws?: number;
  evaluation_opponent?: string;
  opponent_identity?: string;
  evaluation?: EvaluationRecord;
  attacker_score?: number;
  defender_score?: number;
}
const finite = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);

export function trainingHistory(value: unknown): Point[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter(
      (row): row is Point =>
        row &&
        typeof row === 'object' &&
        finite(row.iteration) &&
        finite(row.policy_loss) &&
        finite(row.value_loss),
    )
    .map((row) => ({
      ...row,
      attacker_score: row.evaluation?.candidate_as_attacker?.score,
      defender_score: row.evaluation?.candidate_as_defender?.score,
    }));
}

export default function TrainingHistoryChart({ history }: { history: unknown }) {
  const { t, locale } = useI18n();
  const rows = trainingHistory(history);
  if (!rows.length)
    return (
      <p className="training-chart-empty subtle">
        {t('Graphs appear after the first completed training iteration.')}
      </p>
    );
  const width = 500,
    height = 180,
    left = 46,
    right = 16,
    top = 16,
    bottom = 32;
  const minIteration = rows[0].iteration,
    maxIteration = Math.max(minIteration + 1, rows.at(-1)!.iteration);
  const x = (iteration: number) =>
    left + ((iteration - minIteration) / (maxIteration - minIteration)) * (width - left - right);
  const format = (value: number) =>
    new Intl.NumberFormat(locale, { maximumFractionDigits: 3 }).format(value);
  const identities = [
    ...new Set(
      rows
        .filter((row) => finite(row.evaluation_score))
        .map((row) => row.opponent_identity ?? 'legacy'),
    ),
  ];
  if (!identities.length) identities.push('legacy');
  const charts = [
    {
      title: 'Loss by iteration',
      max: Math.max(0.01, ...rows.flatMap((row) => [row.policy_loss, row.value_loss])) * 1.1,
      identity: undefined as string | undefined,
      series: [
        { key: 'policy_loss' as const, name: 'Policy loss', color: 'var(--chart-policy)' },
        { key: 'value_loss' as const, name: 'Value loss', color: 'var(--chart-value)' },
      ],
    },
    ...identities.map((identity) => ({
      title: 'Evaluation by iteration',
      max: 1,
      identity,
      series: [
        {
          key: 'evaluation_score' as const,
          name: 'Completion-aware score',
          color: 'var(--chart-evaluation)',
        },
        ...(rows.some((row) => row.evaluation)
          ? [
              {
                key: 'attacker_score' as const,
                name: 'Attacker-side score',
                color: 'var(--chart-policy)',
              },
              {
                key: 'defender_score' as const,
                name: 'Defender-side score',
                color: 'var(--chart-value)',
              },
            ]
          : []),
      ],
    })),
  ];
  return (
    <section className="training-history" aria-label={t('Training history graphs')}>
      {charts.map((chart, index) => {
        const record = rows.find((row) => row.opponent_identity === chart.identity)?.evaluation;
        const y = (value: number) =>
          height - bottom - (value / chart.max) * (height - top - bottom);
        return (
          <figure key={`${chart.title}-${chart.identity}`} className="training-chart">
            <figcaption title={chart.identity}>
              {t(chart.title)}
              {chart.identity &&
                ` · ${chart.identity === 'legacy' ? t('Shield bearer · historical settings') : `${t(opponentName(chart.identity.split(':')[0]))} · ${t('Series {number}', { number: index })}`}`}
              {record?.champion_iteration !== undefined &&
                ` · ${t('Accepted champion iteration')} ${record.champion_iteration}`}
            </figcaption>
            <svg role="img" aria-label={t(chart.title)} viewBox={`0 0 ${width} ${height}`}>
              <title>{t(chart.title)}</title>
              {[0, 0.5, 1].map((fraction) => (
                <g key={fraction}>
                  <line
                    x1={left}
                    x2={width - right}
                    y1={y(chart.max * fraction)}
                    y2={y(chart.max * fraction)}
                    className="chart-gridline"
                  />
                  <text x={left - 8} y={y(chart.max * fraction) + 4} textAnchor="end">
                    {format(chart.max * fraction)}
                  </text>
                </g>
              ))}
              <text x={left} y={height - 8}>
                {minIteration}
              </text>
              <text x={width - right} y={height - 8} textAnchor="end">
                {maxIteration}
              </text>
              {chart.series.map((series) => {
                const points = rows.filter(
                  (row) =>
                    finite(row[series.key]) &&
                    (!chart.identity || (row.opponent_identity ?? 'legacy') === chart.identity),
                );
                return (
                  <g key={series.key}>
                    <polyline
                      points={points
                        .map((row) => `${x(row.iteration)},${y(row[series.key]!)}`)
                        .join(' ')}
                      fill="none"
                      stroke={series.color}
                      strokeWidth="2.5"
                    />
                    {points.map((row) => (
                      <circle
                        key={row.iteration}
                        cx={x(row.iteration)}
                        cy={y(row[series.key]!)}
                        r="3.5"
                        fill={series.color}
                      >
                        <title>
                          {t('Iteration {iteration}: {value}', {
                            iteration: row.iteration,
                            value: format(row[series.key]!),
                          })}
                        </title>
                      </circle>
                    ))}
                  </g>
                );
              })}
            </svg>
            <div className="training-chart-legend">
              {chart.series.map((series) => (
                <span key={series.key}>
                  <i style={{ background: series.color }} />
                  {t(series.name)}
                </span>
              ))}
            </div>
            {record?.context && (
              <details>
                <summary>{t('Effective evaluation settings')}</summary>
                <pre className="log-view">{JSON.stringify(record.context, null, 2)}</pre>
              </details>
            )}
          </figure>
        );
      })}
      <p className="subtle">
        {t(
          'Lower loss means better fit to self-play targets, not necessarily stronger play. Evaluation gives unfinished games zero points.',
        )}
      </p>
      <p className="subtle">
        {t(
          'Evaluation series use separate opponent configurations and champion incumbents. Missing observations are not zero scores.',
        )}
      </p>
      <details>
        <summary>{t('View graph data')}</summary>
        <div className="table-scroll">
          <table aria-label={t('Training history data')}>
            <thead>
              <tr>
                {[
                  'Iteration',
                  'Policy loss',
                  'Value loss',
                  'Evaluation score',
                  'Unfinished games',
                ].map((label) => (
                  <th key={label}>{t(label)}</th>
                ))}
                {rows.some((row) => row.evaluation) &&
                  [
                    'Evaluation opponent',
                    'Attacker-side score',
                    'Defender-side score',
                    'Rules draws',
                    'Evaluation duration (seconds)',
                    'Champion promotion',
                  ].map((label) => <th key={label}>{t(label)}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.iteration}>
                  <td>{row.iteration}</td>
                  <td>{format(row.policy_loss)}</td>
                  <td>{format(row.value_loss)}</td>
                  <td>{finite(row.evaluation_score) ? format(row.evaluation_score) : '—'}</td>
                  <td>{row.evaluation_truncated ?? '—'}</td>
                  {rows.some((point) => point.evaluation) && (
                    <>
                      <td title={row.opponent_identity}>
                        {row.evaluation
                          ? t(opponentName(row.evaluation.opponent ?? 'shield_bearer'))
                          : finite(row.evaluation_score)
                            ? t('Shield bearer · historical settings')
                            : '—'}
                      </td>
                      <td>{finite(row.attacker_score) ? format(row.attacker_score) : '—'}</td>
                      <td>{finite(row.defender_score) ? format(row.defender_score) : '—'}</td>
                      <td>{row.evaluation_draws ?? '—'}</td>
                      <td>
                        {finite(row.evaluation?.duration) ? format(row.evaluation.duration) : '—'}
                      </td>
                      <td>
                        {row.evaluation?.promoted === undefined
                          ? '—'
                          : t(row.evaluation.promoted ? 'Promoted' : 'Retained')}
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}
