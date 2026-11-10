import lineage from '../../tests/fixtures/training_parameter_lineage.json' with { type: 'json' };
import type { HistoryPoint } from '../src/trainingDashboardData';

/** A resumed 1500-iteration lineage; legacy has only the last 250 reliable rows. */
export function learningHistory(legacy = false): HistoryPoint[] {
  const segments = legacy ? lineage.segments.slice(-1) : lineage.segments;
  let segment: (typeof segments)[number] | undefined;
  return Array.from({ length: lineage.iterations }, (_, index) => {
    const iteration = index + 1;
    const event = segments.find((segment) => segment.iteration === iteration);
    if (event) segment = event;
    return {
      iteration,
      policy_loss: 1 / iteration,
      value_loss: 0.5 / iteration,
      replay_positions: Math.min(iteration * 10, 12000),
      updates: 4,
      optimizer_updates: iteration * 4,
      learning_rate: iteration < 1001 ? 0.001 : iteration < 1251 ? 0.0001 : 0.0000001,
      effective_learning_rate: iteration < 1000 ? 0.001 : iteration < 1250 ? 0.0001 : 0.0000001,
      performance: { training_exposures: 512 },
      ...(event ? { training_parameters: event.training_parameters } : {}),
      ...(segment ? { training_parameters_iteration: segment.iteration } : {}),
    };
  });
}
