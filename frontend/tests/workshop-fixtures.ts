import type { Job } from '../src/types';
export function workshopFixture(
  id: string,
  count: number,
  status = 'running',
  legacy = false,
): Job {
  const history = Array.from({ length: count }, (_, index) => {
    const iteration = index + 1;
    const opponent = iteration <= 20 ? 'shield_bearer' : iteration <= 40 ? 'jarl' : 'champion';
    const incumbent = iteration <= 50 ? 40 : 50;
    const context = {
      opponent,
      games: opponent === 'champion' ? 48 : 12,
      max_plies: 300,
      candidate_config: { iterations: 64, time_limit: 1 },
      opponent_config: { depth: opponent === 'jarl' ? 3 : undefined, time_limit: 0.2 },
    };
    const score = opponent === 'champion' ? 0.58 : Math.min(1, 0.25 + iteration * 0.014);
    const performance = {
      self_play_mode: 'batched',
      games_per_hour: 280 + iteration * 2,
      positions_per_second: 90 + iteration * 0.6,
      simulations_per_second: 4200 + iteration * 18,
      neural_evaluations_per_second: 3800 + iteration * 12,
      average_game_length: 124,
      requested_mcts_simulations: 400000,
      mcts_simulations: 390000,
      immediate_win_shortcuts: 14,
      search_deadline_cutoffs: 3,
      attacker_wins: 12,
      defender_wins: 10,
      drawn_games: 0,
      truncated_games: 2,
      completed_games: 22,
      inference_batch_size_average: 21 + iteration * 0.02,
      inference_batch_size_min: 1,
      inference_batch_size_max: 32,
      inference_batches: 15000,
      inference_batch_latency_seconds: 0.004,
      gpu_peak_allocated_bytes: 380 * 1024 ** 2,
      gpu_peak_reserved_bytes: 512 * 1024 ** 2,
      self_play_wall_seconds: 240,
      optimization_seconds: 18,
      evaluation_seconds: iteration % 5 === 0 ? 42 : 0,
      iteration_wall_seconds: iteration % 5 === 0 ? 302 : 260,
      cpu_search_seconds: 600,
      inference_time_seconds: 90,
      inference_cuda_seconds: 70,
    };
    return {
      iteration,
      policy_loss: 2.5 * Math.exp(-iteration / 70) + 0.04 * Math.sin(iteration),
      value_loss: 0.7 * Math.exp(-iteration / 50),
      replay_positions: Math.min(iteration * 600, 20000),
      iteration_duration: performance.iteration_wall_seconds,
      ...(!legacy ? { performance } : {}),
      ...(iteration % 5 === 0
        ? {
            evaluation_score: legacy ? 0.5 : score,
            evaluation_truncated: 0,
            evaluation_draws: 2,
            evaluation_wins: 8,
            evaluation_losses: 2,
            ...(!legacy
              ? {
                  opponent_identity: `${opponent}:context-${opponent === 'champion' ? `incumbent-${incumbent}` : 'fixed'}`,
                  evaluation_opponent: opponent,
                  evaluation: {
                    opponent,
                    opponent_identity: `${opponent}:context`,
                    context,
                    iteration,
                    score,
                    candidate_as_attacker: { score: score - 0.15 },
                    candidate_as_defender: { score: Math.min(1, score + 0.15) },
                    wins: 8,
                    losses: 2,
                    draws: 2,
                    truncated: 0,
                    duration: 42,
                    ...(opponent === 'champion'
                      ? { champion_iteration: incumbent, promoted: iteration === 50 }
                      : {}),
                    benchmark_saturated: opponent === 'shield_bearer' && score >= 0.95,
                  },
                }
              : {}),
          }
        : {}),
    };
  });
  const last = history.at(-1)!;
  return {
    id,
    kind: 'train',
    status,
    config: {
      model_name:
        id === 'running'
          ? 'Eir · Tablut strategist'
          : id === 'completed'
            ? 'Bjorn · Northern campaign'
            : id === 'legacy'
              ? 'Legacy raven'
              : `${id} strategist`,
      ruleset: 'tablut',
      iterations: status === 'completed' ? count : 120,
      optimizer: 'adamw',
      learning_rate: 0.001,
      batch_size: 96,
      epochs: 3,
      channels: 48,
      blocks: 3,
      replay_buffer_size: 20000,
      evaluation_opponent: 'champion',
      evaluation_frequency: 5,
    },
    ruleset: 'tablut',
    created_at: '2026-10-22T08:30:00Z',
    execution: legacy ? 'local' : 'remote',
    worker_name: legacy ? undefined : 'North forge · CUDA',
    last_reported_at: legacy ? undefined : '2026-10-23T10:14:30Z',
    checkpoint: 'last.pt',
    resumable: true,
    logs: ['Worker initialized.', 'Self-play, optimization and evaluation complete.'],
    ...(status === 'failed' ? { error: 'Worker disconnected before the next checkpoint.' } : {}),
    progress: {
      iteration: count,
      total_iterations: status === 'completed' ? count : 120,
      stage: status === 'running' ? 'self_play' : status,
      duration: count * 280,
      history,
      metrics: {
        policy_loss: last.policy_loss,
        value_loss: last.value_loss,
        replay_positions: last.replay_positions,
        performance: last.performance,
      },
      ...(!legacy
        ? {
            evaluation_selection: {
              opponent: 'champion',
              champion_iteration: 50,
              context: last.evaluation?.context,
              last_evaluation: [...history].reverse().find((row) => row.evaluation)?.evaluation,
              benchmarks: [
                {
                  iteration: 20,
                  score: 0.53,
                  opponent_identity: 'shield_bearer:fixed',
                  context: { opponent: 'shield_bearer' },
                },
              ],
            },
          }
        : {}),
    },
  };
}
export const workshopFixtures = () => [
  workshopFixture('completed', 120, 'completed'),
  workshopFixture('running', 60),
  workshopFixture('legacy', 30, 'completed', true),
  workshopFixture('failed', 15, 'failed'),
  workshopFixture('cancelled', 25, 'cancelled'),
];
