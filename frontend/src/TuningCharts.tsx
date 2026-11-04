import { useMemo, useState } from 'react';
import { useI18n } from './i18n';
import TrainingMetricChart from './TrainingMetricChart';
import TrainingDataDisclosure from './TrainingDataDisclosure';
import { finite, object, type Values, type HistoryPoint } from './trainingDashboardData';
import { trainingParameterLabel } from './TrainingFields';

const objectiveSeries = [
  { key: 'score', name: 'Optuna objective score', color: 'var(--chart-evaluation)' },
];
const bestSeries = [{ key: 'best', name: 'Best objective so far', color: 'var(--chart-policy)' }];
const runtimeSeries = [{ key: 'duration', name: 'Duration', color: 'var(--chart-value)' }];

// Bound SVG work; tables retain every trial. Preserve the highest objective in
// each bucket and its final observation. Best-so-far is computed before sampling.
function bounded(rows: HistoryPoint[]): HistoryPoint[] {
  if (rows.length <= 400) return rows;
  const step = Math.ceil(rows.length / 200);
  const sampled: HistoryPoint[] = [];
  for (let index = 0; index < rows.length; index += step) {
    const group = rows.slice(index, index + step);
    const peak = group.reduce((best, row) =>
      Number(row.score ?? 0) > Number(best.score ?? 0) ? row : best,
    );
    sampled.push(peak);
    if (peak !== group.at(-1)) sampled.push(group.at(-1)!);
  }
  return sampled.sort((a, b) => a.iteration - b.iteration);
}

export default function TuningCharts({
  trials,
  importance,
}: {
  trials: Values[];
  importance?: Values;
}) {
  const { t } = useI18n();
  const [parameter, setParameter] = useState('');
  const data = useMemo(() => {
    let best = -Infinity;
    const complete = trials
      .filter((row) => row.state === 'COMPLETE' && finite(row.score) && finite(row.number))
      .sort((a, b) => Number(a.number) - Number(b.number));
    const rows: HistoryPoint[] = complete.map((row) => {
      best = Math.max(best, Number(row.score));
      return { ...row, iteration: Number(row.number), best };
    });
    const parameters = [
      ...new Set(complete.flatMap((row) => Object.keys(object(row.parameters) ?? {}))),
    ].sort();
    const runtimes = trials
      .filter((row) => finite(row.duration) && finite(row.number))
      .map((row) => ({ ...row, iteration: Number(row.number), score: row.duration }))
      .sort((a, b) => a.iteration - b.iteration);
    return { rows, parameters, plotted: bounded(rows), runtimes: bounded(runtimes) };
  }, [trials]);
  const selected = data.parameters.includes(parameter) ? parameter : data.parameters[0];
  const relationship = useMemo(
    () =>
      bounded(
        data.rows
          .flatMap((row) => {
            const value = object(row.parameters)?.[selected];
            return finite(value) ? [{ ...row, iteration: value }] : [];
          })
          .sort((a, b) => a.iteration - b.iteration),
      ),
    [data, selected],
  );
  return (
    <TrainingDataDisclosure title="Tuning charts">
      {() => (
        <>
          <p className="subtle">
            {t(
              'Charts show completed objective values. Large studies are sampled for display; the trial table contains all observations.',
            )}
          </p>
          <TrainingMetricChart
            title="Trial objective scores"
            rows={data.plotted}
            series={objectiveSeries}
            score
            xLabel="Trial"
            scatter
          />
          <TrainingMetricChart
            title="Best objective so far"
            rows={data.plotted}
            series={bestSeries}
            score
            xLabel="Trial"
          />
          <TrainingMetricChart
            title="Trial runtime comparisons"
            rows={data.runtimes}
            series={runtimeSeries}
            xLabel="Trial"
            unit="Seconds"
          />
          {selected && (
            <>
              <label className="training-field">
                {t('Parameter versus objective')}
                <select value={selected} onChange={(event) => setParameter(event.target.value)}>
                  {data.parameters.map((key) => (
                    <option key={key} value={key}>
                      {t(trainingParameterLabel(key))}
                    </option>
                  ))}
                </select>
              </label>
              <TrainingMetricChart
                title="Parameter versus objective"
                rows={relationship}
                series={objectiveSeries}
                score
                scatter
                xLabel={trainingParameterLabel(selected)}
              />
            </>
          )}
          <h4>{t('Exploratory parameter importance')}</h4>
          {object(importance?.values) ? (
            <>
              <p className="subtle">
                {t(
                  'Relative binned variance explained, with at least 20 completed trials. Exploratory associations can be confounded and do not establish causality or statistical significance.',
                )}
              </p>
              <dl className="training-kpis">
                {Object.entries(object(importance?.values)!).map(([key, value]) => (
                  <div key={key}>
                    <dt>{t(trainingParameterLabel(key))}</dt>
                    <dd>
                      {finite(value) ? `${(value * 100).toFixed(1)}%` : '—'}{' '}
                      <meter
                        aria-label={t(trainingParameterLabel(key))}
                        min="0"
                        max="1"
                        value={Number(value)}
                      />
                    </dd>
                  </div>
                ))}
              </dl>
            </>
          ) : (
            <p className="subtle">
              {t(
                'Importance requires at least 20 completed trials, varying parameters and varying objectives. Fewer observations are insufficient.',
              )}
            </p>
          )}
        </>
      )}
    </TrainingDataDisclosure>
  );
}
