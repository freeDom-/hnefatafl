import { useMemo, useState } from 'react';
import { useI18n } from './i18n';
import { opponentName } from './EvaluationSettings';
import TrainingMetricChart, { policySeries, type ChartSeries } from './TrainingMetricChart';
import {
  trainingHistory,
  evaluationGroups,
  evaluationIdentity,
  finite,
  recentHistory,
  type HistoryPoint,
} from './trainingDashboardData';
import TrainingDataDisclosure from './TrainingDataDisclosure';
import './trainingHistory.css';
export { trainingHistory } from './trainingDashboardData';

const scoreSeries: ChartSeries[] = [
  { key: 'evaluation_score', name: 'Completion-aware score', color: 'var(--chart-evaluation)' },
];
const sideSeries: ChartSeries[] = [
  ...scoreSeries,
  { key: 'attacker_score', name: 'Attacker-side score', color: 'var(--chart-policy)' },
  { key: 'defender_score', name: 'Defender-side score', color: 'var(--chart-value)' },
];
interface DisplayedSeries {
  identity: string;
  points: HistoryPoint[];
  merged?: boolean;
}
export default function TrainingHistoryChart({
  history,
  view = 'all',
  range = 0,
  compact = false,
  selectable = false,
}: {
  history: unknown;
  view?: 'all' | 'learning' | 'evaluation';
  range?: number;
  compact?: boolean;
  selectable?: boolean;
}) {
  const { t, locale } = useI18n();
  const [selectedSeries, setSelectedSeries] = useState('latest');
  const rows = useMemo(() => recentHistory(trainingHistory(history), range), [history, range]);
  const groups = useMemo(() => evaluationGroups(rows), [rows]);
  // Every observation in one series: the merged chart keeps each iteration's own
  // score, so changed opponents or incumbents stay visible without splitting the graph.
  const mergedGroup = useMemo(
    () => ({
      identity: 'merged',
      merged: true,
      points: rows.filter((row) => finite(row.evaluation_score)),
    }),
    [rows],
  );
  const mergedSeriesSettings = useMemo(
    () =>
      groups.map((group, index) => {
        const first = group.points[0];
        const last = group.points.at(-1)!;
        return {
          series: index + 1,
          opponent:
            last.evaluation?.opponent ??
            last.evaluation_opponent ??
            last.opponent_identity?.split(':')[0] ??
            'shield_bearer',
          iterations: [first.iteration, last.iteration],
          context: last.evaluation?.context ?? null,
        };
      }),
    [groups],
  );
  if (!rows.length)
    return (
      <p className="training-chart-empty subtle">
        {t('Graphs appear after the first completed training iteration.')}
      </p>
    );
  const format = (value: unknown) =>
    finite(value)
      ? new Intl.NumberFormat(locale, { maximumSignificantDigits: 10 }).format(value)
      : '—';
  const detailedEvaluation = rows.some((row) => row.evaluation);
  const selection =
    selectedSeries === 'all' ? groups : groups.filter((group) => group.identity === selectedSeries);
  // The job overview and the "all series" selection read better as one merged chart
  // than as one chart per series. A single series is still drawn on its own.
  const displayedGroups: DisplayedSeries[] = compact
    ? groups.length > 1
      ? [mergedGroup]
      : groups.slice(-1)
    : selectable && selectedSeries === 'all'
      ? [mergedGroup]
      : selectable
        ? selection.length
          ? selection
          : groups.slice(-1)
        : groups;
  const headers = [
    'Iteration',
    'Policy loss',
    'Value loss',
    'Evaluation score',
    'Unfinished games',
    ...(detailedEvaluation
      ? [
          'Evaluation opponent',
          'Attacker-side score',
          'Defender-side score',
          'Rules draws',
          'Evaluation duration (seconds)',
          'Champion promotion',
        ]
      : []),
    ...(view === 'learning' ? ['Replay buffer positions', 'Optimizer updates'] : []),
    ...(view === 'evaluation'
      ? [
          'Wins',
          'Losses',
          'Opponent identity',
          'Effective evaluation settings',
          'Accepted champion iteration',
        ]
      : []),
  ];
  return (
    <section
      className={`training-history ${compact ? 'history-compact' : ''}`}
      aria-label={t('Training history graphs')}
    >
      {selectable && groups.length > 1 && (
        <label className="evaluation-series-control">
          {t('Evaluation series')}
          <select
            aria-label={t('Evaluation series')}
            value={
              selectedSeries === 'all' || groups.some((group) => group.identity === selectedSeries)
                ? selectedSeries
                : 'latest'
            }
            onChange={(event) => setSelectedSeries(event.target.value)}
          >
            <option value="latest">{t('Latest series')}</option>
            <option value="all">{t('All evaluation series')}</option>
            {groups.map((group, index) => (
              <option key={group.identity} value={group.identity}>
                {t(
                  opponentName(
                    group.points[0].evaluation?.opponent ??
                      group.points[0].evaluation_opponent ??
                      'shield_bearer',
                  ),
                )}{' '}
                · {t('Series {number}', { number: index + 1 })} · {t('Iteration')}{' '}
                {group.points[0].iteration}–{group.points.at(-1)!.iteration}
              </option>
            ))}
          </select>
        </label>
      )}
      {view !== 'evaluation' && (
        <TrainingMetricChart title="Loss by iteration" rows={rows} series={policySeries} />
      )}
      {view !== 'learning' &&
        (displayedGroups.length ? displayedGroups : [{ identity: 'empty', points: [] }]).map(
          (group) => {
            const record = group.points.at(-1);
            if (!record)
              return (
                <div key={group.identity} className="evaluation-series">
                  <p className="series-context">{t('No evaluation recorded yet.')}</p>
                  <TrainingMetricChart
                    title="Evaluation by iteration"
                    rows={rows}
                    series={scoreSeries}
                    score
                    gaps
                  />
                </div>
              );
            const opponent =
              record?.evaluation?.opponent ??
              record?.evaluation_opponent ??
              record?.opponent_identity?.split(':')[0] ??
              'shield_bearer';
            const series = !compact && detailedEvaluation ? sideSeries : scoreSeries;
            const chartRows = group.merged
              ? rows
              : rows.map((row) =>
                  evaluationIdentity(row) === group.identity
                    ? row
                    : {
                        ...row,
                        evaluation_score: undefined,
                        attacker_score: undefined,
                        defender_score: undefined,
                        evaluation: undefined,
                      },
                );
            return (
              <div key={group.identity} className="evaluation-series">
                {group.merged ? (
                  <p className="series-context">
                    {t('Merged evaluation series')} ·{' '}
                    {t('{count} series', { count: groups.length })}
                  </p>
                ) : (
                  <p className="series-context" title={record?.opponent_identity}>
                    {t(opponentName(opponent))} ·{' '}
                    {t('Series {number}', {
                      number: groups.findIndex((item) => item.identity === group.identity) + 1,
                    })}
                    {record?.evaluation?.champion_iteration !== undefined &&
                      ` · ${t('Accepted champion iteration')} ${record.evaluation.champion_iteration}`}
                    {!record?.opponent_identity &&
                      !record?.evaluation?.context &&
                      ` · ${t('Historical settings')}`}
                  </p>
                )}
                <TrainingMetricChart
                  title="Evaluation by iteration"
                  rows={chartRows}
                  series={series}
                  score
                  gaps
                />
                {!compact &&
                  (group.merged ? (
                    <details>
                      <summary>{t('Effective evaluation settings')}</summary>
                      <pre className="log-view">
                        {JSON.stringify(mergedSeriesSettings, null, 2)}
                      </pre>
                    </details>
                  ) : (
                    record?.evaluation?.context && (
                      <details>
                        <summary>{t('Effective evaluation settings')}</summary>
                        <pre className="log-view">
                          {JSON.stringify(record.evaluation.context, null, 2)}
                        </pre>
                      </details>
                    )
                  ))}
              </div>
            );
          },
        )}
      {!compact && (
        <>
          <p className="subtle">
            {t(
              'Lower loss means better fit to self-play targets, not necessarily stronger play. Evaluation gives unfinished games zero points.',
            )}
          </p>
          {view !== 'learning' && (
            <p className="subtle">
              {t(
                'Evaluation series use separate opponent configurations and champion incumbents. Missing observations are not zero scores.',
              )}
            </p>
          )}
          <TrainingDataDisclosure title="View graph data">
            {() => (
              <div className="table-scroll bounded-table">
                <table aria-label={t('Training history data')}>
                  <thead>
                    <tr>
                      {headers.map((label) => (
                        <th scope="col" key={label}>
                          {t(label)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => (
                      <tr key={row.iteration}>
                        <th scope="row">{row.iteration}</th>
                        <td>{format(row.policy_loss)}</td>
                        <td>{format(row.value_loss)}</td>
                        <td>{format(row.evaluation_score)}</td>
                        <td>{format(row.evaluation_truncated ?? row.evaluation?.truncated)}</td>
                        {detailedEvaluation && (
                          <>
                            <td>
                              {finite(row.evaluation_score)
                                ? t(
                                    opponentName(
                                      row.evaluation?.opponent ??
                                        row.evaluation_opponent ??
                                        'shield_bearer',
                                    ),
                                  )
                                : '—'}
                            </td>
                            <td>{format(row.attacker_score)}</td>
                            <td>{format(row.defender_score)}</td>
                            <td>{format(row.evaluation_draws ?? row.evaluation?.draws)}</td>
                            <td>{format(row.evaluation?.duration)}</td>
                            <td>
                              {row.evaluation?.promoted === undefined
                                ? '—'
                                : t(row.evaluation.promoted ? 'Promoted' : 'Retained')}
                            </td>
                          </>
                        )}
                        {view === 'learning' && (
                          <>
                            <td>{format(row.replay_positions)}</td>
                            <td>{format(row.optimizer_updates ?? row.updates)}</td>
                          </>
                        )}
                        {view === 'evaluation' && (
                          <>
                            <td>{format(row.evaluation_wins ?? row.evaluation?.wins)}</td>
                            <td>{format(row.evaluation_losses ?? row.evaluation?.losses)}</td>
                            <td>
                              {finite(row.evaluation_score)
                                ? (row.opponent_identity ?? t('Historical settings'))
                                : '—'}
                            </td>
                            <td>
                              {row.evaluation?.context ? (
                                <code>{JSON.stringify(row.evaluation.context)}</code>
                              ) : (
                                '—'
                              )}
                            </td>
                            <td>{format(row.evaluation?.champion_iteration)}</td>
                          </>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </TrainingDataDisclosure>
        </>
      )}
    </section>
  );
}
