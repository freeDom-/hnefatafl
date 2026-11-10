/** Developer-only production-build harness. Not an application entry point. */
import { useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';
import { TrainingLearningView } from '../src/TrainingAnalysisViews';
import { learningChartRows, availableLearningMetrics } from '../src/trainingLearningData';
import { iterationRanges, trainingHistory, type HistoryPoint } from '../src/trainingDashboardData';
import { setLanguage } from '../src/i18n';
import '../src/style.css';
import '../src/appearance.css';
import '../src/trainingDashboard.css';

function segment(iteration: number): number {
  if (iteration <= 1000) return 1;
  if (iteration <= 1250) return 1001;
  return 1251 + Math.floor((iteration - 1251) / 5000) * 5000;
}

let reliableFrom = 1;
/** Complete metrics, periodic evaluations, resume events, genuine zeros and missing rows. */
function point(iteration: number, legacy: boolean): HistoryPoint {
  const boundary = legacy ? Math.max(segment(iteration), reliableFrom) : segment(iteration);
  const phase = boundary === 1 ? 0 : boundary === 1001 ? 1 : 2;
  const reliable = !legacy || iteration >= reliableFrom;
  const missing = iteration % 701 === 0;
  return {
    iteration,
    policy_loss: 2.5 * Math.exp(-iteration / 1000) + 0.02 + Math.sin(iteration) * 0.005,
    value_loss: 0.5 * Math.exp(-iteration / 2000) + 0.01,
    replay_positions: Math.min(iteration * 100, 12000),
    updates: 32,
    iteration_duration: 302,
    learning_rate: [0.001, 0.0001, 0.0000001][phase],
    effective_learning_rate: [0.001, 0.0001, 0.0000001][phase],
    performance: {
      training_exposures: 4096,
      augmentation_mode: 'random_d4',
      augmentation_exposures: 4096,
      augmentation_identity: 512,
      augmentation_seconds: 0.07,
      augmentation_optimization_share: 0.004,
      self_play_mode: 'batched',
      inference_backend: 'pytorch',
      optimization_device: 'cpu',
      games_per_hour: 280,
      positions_per_second: 90,
      simulations_per_second: 4200,
      neural_evaluations_per_second: 3800,
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
      inference_batch_size_average: 24,
      inference_batch_size_min: 1,
      inference_batch_size_max: 32,
      inference_batches: 15000,
      inference_batch_latency_seconds: 0.004,
      gpu_peak_allocated_bytes: 380 * 1024 ** 2,
      gpu_peak_reserved_bytes: 512 * 1024 ** 2,
      self_play_wall_seconds: 240,
      optimization_seconds: 18,
      evaluation_seconds: iteration % 5 === 0 ? 42 : 0,
      iteration_wall_seconds: 302,
      cpu_search_seconds: 600,
      inference_time_seconds: 90,
      inference_cuda_seconds: 70,
    },
    ...(iteration % 5 === 0
      ? {
          evaluation: {
            iteration,
            opponent: 'shield_bearer',
            score: 0.5,
            wins: 4,
            losses: 4,
            draws: 2,
            truncated: 0,
            duration: 42,
            candidate_as_attacker: { score: 0.4 },
            candidate_as_defender: { score: 0.6 },
            context: {
              opponent: 'shield_bearer',
              games: 10,
              candidate_config: { iterations: 64, time_limit: 1 },
            },
          },
        }
      : {}),
    ...(reliable && iteration === boundary
      ? {
          training_parameters: {
            version: 1,
            batch_size: phase ? 128 : 64,
            epochs: 4,
            mcts_iterations: [64, 128, 256][phase],
            mcts_time_limit: 10,
            games: 64,
            replay_buffer_size: 12000,
            weight_decay: phase ? 0.000002 : 0.000001,
            exploration: 1.4,
            temperature: 1,
            dirichlet_alpha: 0.3,
            dirichlet_fraction: 0,
            max_game_plies: 300,
          },
        }
      : {}),
    ...(reliable && !missing ? { training_parameters_iteration: boundary } : {}),
  };
}

let raw: HistoryPoint[] = [];
let normalized: HistoryPoint[] = [];
let projected: HistoryPoint[] = [];
let legacy = false;
let root = createRoot(document.getElementById('root')!);

function Learning({ rows, heartbeat }: { rows: HistoryPoint[]; heartbeat: number }) {
  const [range, setRange] = useState(0);
  const history = useMemo(() => trainingHistory(rows), [rows]);
  return (
    <>
      <label className="training-range">
        Iteration range
        <select
          aria-label="Iteration range"
          value={range}
          onChange={(event) => setRange(Number(event.target.value))}
        >
          <option value="0">All iterations</option>
          {iterationRanges.map((range) => (
            <option key={range} value={range}>
              {range}
            </option>
          ))}
        </select>
      </label>
      <TrainingLearningView progress={{ duration: heartbeat }} history={history} range={range} />
    </>
  );
}

const frame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
async function commit(action: () => void) {
  const start = performance.now();
  flushSync(action);
  document.querySelector('svg')?.getBoundingClientRect();
  const commitMs = performance.now() - start;
  await frame();
  await frame();
  return { commitMs, paintMs: performance.now() - start };
}

const api = {
  prepare(count: number, legacyHistory = false) {
    legacy = legacyHistory;
    reliableFrom = legacy ? Math.max(1, count - 249) : 1;
    raw = Array.from({ length: count }, (_, index) => point(index + 1, legacy));
    const start = performance.now();
    normalized = trainingHistory(raw);
    return {
      normalizeMs: performance.now() - start,
      jsonBytes: new TextEncoder().encode(JSON.stringify(raw)).length,
    };
  },
  reconstruct() {
    const start = performance.now();
    projected = learningChartRows(normalized);
    return performance.now() - start;
  },
  availability() {
    const start = performance.now();
    const available = availableLearningMetrics(projected);
    return { ms: performance.now() - start, count: available.size };
  },
  releaseDerived() {
    normalized = [];
    projected = [];
  },
  mount: () => commit(() => root.render(<Learning rows={raw} heartbeat={0} />)),
  metric: (value: string) =>
    commit(() => {
      const select = document.querySelector<HTMLSelectElement>('[aria-label="Learning metric"]')!;
      select.value = value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }),
  range: (value: number) =>
    commit(() => {
      const select = document.querySelector<HTMLSelectElement>('[aria-label="Iteration range"]')!;
      select.value = String(value);
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }),
  append: () =>
    commit(() => {
      raw = [...raw, point(raw.length + 1, legacy)];
      root.render(<Learning rows={raw} heartbeat={0} />);
    }),
  heartbeat: () => commit(() => root.render(<Learning rows={raw} heartbeat={100} />)),
  summary: () => ({
    rows: raw.length,
    tables: document.querySelectorAll('table').length,
    charts: document.querySelectorAll('svg').length,
    paths: document.querySelectorAll('svg path').length,
    metric: document.querySelector<HTMLSelectElement>('[aria-label="Learning metric"]')?.value,
    description: document.querySelector('svg')?.getAttribute('aria-description'),
  }),
};

declare global {
  interface Window {
    learningBenchmark: typeof api;
  }
}
setLanguage('en', false);
window.learningBenchmark = api;
