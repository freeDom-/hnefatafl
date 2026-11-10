import { finite, object, type HistoryPoint, type Values } from './trainingDashboardData';
import { policySeries, type ChartSeries } from './TrainingMetricChart';

/** Version-1 whitelist and bounds mirror training_parameters.py. No job-config fallback. */
export const trainingParameterFields = {
  batch_size: [1, 2048, true],
  epochs: [1, 100, true],
  mcts_iterations: [1, 100000, true],
  mcts_time_limit: [0.001, 300, false],
  games: [1, 10000, true],
  replay_buffer_size: [1, 20000, true],
  weight_decay: [0, 1, false],
  exploration: [0, 20, false],
  temperature: [0.01, 10, false],
  dirichlet_alpha: [0.001, 100, false],
  dirichlet_fraction: [0, 1, false],
  max_game_plies: [2, 5000, true],
} as const;
export type TrainingParameterKey = keyof typeof trainingParameterFields;
type ObservationKey =
  | 'policy_loss'
  | 'value_loss'
  | 'replay_positions'
  | 'updates'
  | 'learning_rate'
  | 'effective_learning_rate'
  | 'training_exposures';
export type LearningMetricKey = 'loss' | ObservationKey | TrainingParameterKey;
export interface LearningMetric {
  key: LearningMetricKey;
  label: string;
  unit: string;
  description: string;
  series: ChartSeries[];
  step?: boolean;
  smallScale?: boolean;
}
const observation = (
  key: ObservationKey,
  label: string,
  unit: string,
  description: string,
  smallScale = false,
): LearningMetric => ({
  key,
  label,
  unit,
  description,
  smallScale,
  series: [
    policySeries.find((series) => series.key === key) ?? {
      key,
      name: label,
      color: 'var(--chart-value)',
    },
  ],
});
const parameter = (
  key: TrainingParameterKey,
  label: string,
  unit: string,
  description: string,
  smallScale = false,
): LearningMetric => ({
  key,
  label,
  unit,
  description,
  step: true,
  smallScale,
  series: [{ key: `parameter_${key}`, name: label, color: 'var(--chart-value)' }],
});

/** Measured observations and configured limits keep distinct, explicit semantics. */
export const learningMetrics: LearningMetric[] = [
  {
    key: 'loss',
    label: 'Loss by iteration',
    unit: 'Loss',
    description: 'Policy and value loss against self-play targets for each completed iteration.',
    series: policySeries,
  },
  observation('policy_loss', 'Policy loss', 'Loss', 'Policy loss against self-play move targets.'),
  observation('value_loss', 'Value loss', 'Loss', 'Value loss against self-play outcome targets.'),
  observation(
    'replay_positions',
    'Replay buffer positions',
    'Positions',
    'Actual positions stored in replay after this iteration; distinct from configured capacity.',
  ),
  parameter(
    'replay_buffer_size',
    'Replay buffer capacity',
    'Positions',
    'Configured maximum replay positions, recorded for this training interval.',
  ),
  observation(
    'updates',
    'Optimizer updates',
    'Updates',
    'Optimizer updates performed during this iteration, not the cumulative update count.',
  ),
  observation(
    'learning_rate',
    'Learning rate (used)',
    'Learning rate',
    'Optimizer base rate used for this iteration. Schedule-Free warm-up rates and parameter update magnitudes differ.',
    true,
  ),
  observation(
    'effective_learning_rate',
    'Learning rate (selected)',
    'Learning rate',
    'Effective optimizer base rate selected after this iteration for the next training interval.',
    true,
  ),
  observation(
    'training_exposures',
    'Training exposures',
    'Samples',
    'Recorded training examples presented to the optimizer during this iteration.',
  ),
  parameter(
    'batch_size',
    'Training batch size',
    'Samples',
    'Configured replay samples per gradient batch.',
  ),
  parameter('epochs', 'Training epochs', 'Epochs', 'Configured optimization epochs per iteration.'),
  parameter(
    'mcts_iterations',
    'Self-play MCTS iterations',
    'Simulations',
    'Configured maximum simulations per move. Time limits and early termination can reduce actual work.',
  ),
  parameter(
    'mcts_time_limit',
    'Self-play MCTS time limit',
    'Seconds',
    'Configured self-play search time limit per move, in seconds.',
  ),
  parameter(
    'games',
    'Self-play games per iteration',
    'Games',
    'Configured target self-play games per iteration, not a measured game count.',
  ),
  parameter(
    'weight_decay',
    'Weight decay',
    'Coefficient',
    'Configured optimizer regularization. Zero disables weight decay.',
    true,
  ),
  parameter(
    'exploration',
    'MCTS exploration constant',
    'Coefficient',
    'Configured PUCT exploration constant for self-play.',
  ),
  parameter(
    'temperature',
    'Self-play temperature',
    'Temperature',
    'Configured self-play move-selection temperature.',
  ),
  parameter(
    'dirichlet_alpha',
    'Dirichlet alpha',
    'Coefficient',
    'Configured root exploration-noise alpha.',
  ),
  parameter(
    'dirichlet_fraction',
    'Dirichlet fraction',
    'Fraction',
    'Configured fraction of root exploration noise.',
  ),
  parameter(
    'max_game_plies',
    'Maximum game plies',
    'Plies',
    'Configured self-play game-length limit, not the measured game length.',
  ),
];

/** Availability uses the complete projected lineage, before selecting a window. */
export function availableLearningMetrics(rows: HistoryPoint[]): Set<LearningMetricKey> {
  // Every configured value originates in a snapshot row. Inspect those sparse
  // rows once instead of scanning a long unknown legacy prefix twelve times.
  const snapshots = rows.filter((row) => Object.hasOwn(row, 'training_parameters'));
  return new Set(
    learningMetrics
      .filter((metric) =>
        (metric.step ? snapshots : rows).some((row) =>
          metric.series.some((series) => finite(row[series.key])),
        ),
      )
      .map((metric) => metric.key),
  );
}

const parameterKeys = Object.keys(trainingParameterFields) as TrainingParameterKey[];
type Parameters = Partial<Record<TrainingParameterKey, number>>;
function recordedParameters(value: unknown): Parameters {
  const snapshot = object(value);
  if (snapshot?.version !== 1) return {};
  const parameters: Parameters = {};
  for (const key of parameterKeys) {
    const value = snapshot[key];
    const [minimum, maximum, integer] = trainingParameterFields[key];
    if (
      finite(value) &&
      value >= minimum &&
      value <= maximum &&
      (!integer || Number.isInteger(value))
    )
      parameters[key] = value;
  }
  return parameters;
}

const parameterColumns = (parameters: Parameters) =>
  Object.fromEntries(parameterKeys.map((key) => [`parameter_${key}`, parameters[key]]));
const missingParameterColumns = parameterColumns({});

const recordedUpdateCount = (value: unknown): number | undefined =>
  finite(value) && Number.isSafeInteger(value) && value >= 0 ? value : undefined;

/**
 * The historical top-level alias describes this iteration, just like updates.
 * Prefer the current field, including zero. Never use cumulative optimizer
 * diagnostics (such as Schedule-Free completed_steps) or infer missing counts.
 */
export const recordedOptimizerUpdates = (row: Values | undefined): number | undefined =>
  recordedUpdateCount(row?.updates) ?? recordedUpdateCount(row?.optimizer_updates);

/**
 * One linear pass over the complete chronological lineage, before range filtering.
 * A row's small numeric reference proves which snapshot its worker used. Rows
 * appended by older workers have no reference, so cannot inherit stale settings.
 * Events replace the whole snapshot: invalid/missing fields stay unknown.
 */
export function learningChartRows(history: HistoryPoint[]): HistoryPoint[] {
  let columns = missingParameterColumns;
  let snapshotIteration: number | undefined;
  return history.map((row) => {
    const event = Object.hasOwn(row, 'training_parameters');
    if (event) {
      // Build the twelve columns once per sparse event, rather than once per row.
      columns = parameterColumns(recordedParameters(row.training_parameters));
      snapshotIteration = row.iteration;
    }
    const recorded =
      event ||
      (snapshotIteration !== undefined && row.training_parameters_iteration === snapshotIteration);
    return {
      ...row,
      // Include missing columns too: imported columns never establish provenance.
      ...(recorded ? columns : missingParameterColumns),
      updates: recordedOptimizerUpdates(row),
      training_exposures: object(row.performance)?.training_exposures,
    };
  });
}

/** Preserve all recorded significant digits and genuine zero, in the viewer's locale. */
export const formatLearningValue = (value: unknown, locale: string): string =>
  finite(value)
    ? new Intl.NumberFormat(locale, {
        maximumSignificantDigits: 17,
        ...(value !== 0 && Math.abs(value) < 0.0001 ? { notation: 'scientific' as const } : {}),
      }).format(value)
    : '—';
