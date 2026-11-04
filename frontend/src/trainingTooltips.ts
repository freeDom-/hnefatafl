/**
 * One source of truth for the tooltip of every training parameter.
 *
 * Both the "Create New Training Job" form and the "Resume Training Job" dialog
 * look their descriptions up here by the same parameter identity the API and the
 * `data-training-field` attributes use, so a shared parameter can never explain
 * itself differently in the two places. The text is the English source string;
 * the German dictionary translates it like any other UI copy.
 *
 * Keep every description parameter-specific and grounded in the implementation:
 * say what the value controls, which phase it affects (self-play, gradient
 * training or evaluation), how raising or lowering it changes behaviour, and how
 * it interacts with other settings. Bounds and defaults are intentionally not
 * repeated as prose where the control already shows them; where they matter they
 * must match `hnefatafl/training.py` (`normalize_config`) and
 * `trainingPresets.ts` (`trainingChoices`).
 *
 * `trainingTooltips.test.ts` fails when a parameter exposed by either dialog has
 * no entry here, so adding a control without a description is caught in CI.
 */

const studyTrials =
  'Number of Optuna trials to run. Each trial trains a full model with sampled hyperparameters, so a tuning job takes much longer than a single training run. When a saved study is continued, this is the additional budget on top of its completed trials.';
const studyName =
  'Name that identifies the Optuna study inside the job’s own study database. A new tuning job always starts a fresh database, so the same name alone does not continue an earlier study; continuing uses the saved job’s “Resume study” action. Must contain 1–100 characters.';
const studySeed =
  'Seed for the Optuna sampler, separate from the model’s random seed. A fresh trial derives its model seed from the nested training seed (or this study seed when none is set), so the same inputs suggest the same parameters for a study with the same history, and a continued study keeps the saved seed.';
const studySearchSpace =
  'JSON object describing which parameters Optuna samples and their ranges. Only tunable training parameters are allowed, such as channels, blocks, learning rate, weight decay, exploration, temperature, batch size, epochs, MCTS iterations and the Dirichlet values; evaluation and champion settings cannot be tuned. Supported distributions are float (optional log), int (optional positive step, log requires step 1) and categorical choices. The text is sent as the search space, so it must stay valid JSON.';
const studyStrategy =
  'Optional JSON for advanced study settings: “opponents” is a weighted list of fixed references ({opponent, weight}, or {opponent: "frozen_model", model_id, weight}) for a multi-opponent objective, “verification” re-evaluates the winning checkpoint with a held-out seed ({games, seed, opponent}), “sampler”/“pruner” set TPE and median-pruner startup, and “max_failures” bounds consecutive failing trials. Leave empty for the default single-opponent objective.';
const additionalTuningIterations =
  'Extra training iterations each checkpoint-based trial runs on top of the source checkpoint\u2019s completed iteration. Every trial uses the same total, so hyperparameters are compared from one common state without modifying the source checkpoint.';

export const trainingTooltips: Record<string, string> = {
  // ── General ──────────────────────────────────────────────────────────────
  target:
    'Where the job runs: this server, any compatible remote worker, or one specific worker. Remote jobs wait in the queue until a matching worker has capacity. The target determines which training devices and inference backends are available.',
  ruleset:
    'Battlefield the model is trained for. Each battlefield has a different board size and starting position, so a model does not transfer directly between them. Changing this replaces the training values with the plan for the new battlefield.',
  preset:
    'Starting set of training values. Recommended applies the plan tailored to the selected battlefield, Quick check is a tiny real run for verifying the setup, and Beginner and Balanced are progressively longer experiments. Selecting a preset overwrites the training values below; the model name, random seed, device and inference settings are kept.',
  model_name:
    'Name this model is registered and listed under, and the label stored in its checkpoints. Must contain 1–100 characters.',

  // ── Hardware and inference ───────────────────────────────────────────────
  device:
    'Device the training process runs on. Automatic picks NVIDIA CUDA when available, then Apple Metal, otherwise CPU. CUDA enables batched self-play and mixed precision; CPU training works everywhere but is slower. Apple Metal uses the integrated GPU on Apple silicon.',
  inference_backend:
    'Backend that evaluates the network during self-play. PyTorch is the default and runs anywhere. OpenVINO accelerates inference on a supported Intel GPU while the network’s weight updates stay in PyTorch on the CPU. Evaluation uses PyTorch inference by default, and “Use training inference backend” lets it reuse this backend, OpenVINO included. OpenVINO requires CPU training and a compatible execution environment with supported Intel hardware.',
  inference_cache_mode:
    'Reuses neural evaluations of identical positions instead of recomputing them. Off disables caching. Exact positions keys results by ruleset, board and side to move, removes duplicates inside one batch, and is dropped whenever the model, backend or precision changes. Every phase builds its own cache, so nothing carries over between iterations; it pays off when the same position recurs, so measure throughput before relying on it.',
  inference_cache_capacity:
    'Maximum number of positions one phase’s cache keeps. The least recently used entry is evicted when it is full. A larger cache can raise the hit rate but holds more results in memory, and the value is ignored while the cache is off.',
  inference_device:
    'Intel GPU that OpenVINO runs inference on. GPU selects the default device and a numbered entry a specific one. Only meaningful for the OpenVINO backend: PyTorch ignores it and uses the training device instead.',

  // ── Neural network and optimization ──────────────────────────────────────
  channels:
    'Feature width of the network’s first convolution and of every residual block; both output heads also take their width from it. More channels raise capacity and memory use per evaluation and enlarge checkpoints, and usually need more self-play games to train well. It is part of the model architecture, so a resumed run must keep the same value.',
  blocks:
    'Number of residual blocks in the network trunk. More blocks make the network deeper and more capable but raise the compute and memory of every evaluation. Zero keeps only the initial convolution stage. Part of the model architecture, so a resumed run must keep the same value.',
  batch_size:
    'Replay positions used in one gradient update. Larger batches give smoother gradients and better device utilization but use more memory; smaller batches take more update steps over the same data. The optimizer steps per iteration are the number of epochs multiplied by the number of batches in the replay buffer, rounded up so the final, partly filled batch is trained as well.',
  epochs:
    'Gradient-training passes over the whole replay buffer per iteration. More epochs extract more from the collected positions but take longer and can overfit. Total passes are the iterations multiplied by this value.',
  learning_rate:
    'Step size the optimizer applies to the network weights. Higher values learn faster but can become unstable or prevent convergence; lower values are steadier but need more training. Reduce it if losses fluctuate strongly. On resume, changing it overrides the restored value while the accumulated optimizer state is kept.',
  optimizer:
    'Algorithm that updates the network weights. AdamW is Adam with decoupled weight decay and is the default; Adam adapts a step size per parameter; SGD uses the momentum setting and often needs a higher learning rate. The optimizer is part of the checkpoint identity, so a resumed run cannot change it.',
  weight_decay:
    'Regularization the optimizer applies to the weights. Higher values shrink weights more strongly and reduce overfitting at the cost of slower fitting; 0 disables it. AdamW applies it decoupled from the gradient update, Adam and SGD apply it through the optimizer itself.',
  momentum:
    'Momentum of the SGD optimizer, which carries previous updates forward. Higher values smooth the update direction and speed convergence but can overshoot; 0 is plain SGD. Ignored by Adam and AdamW, so it only matters when SGD is selected and it is disabled while resuming a non-SGD run.',
  replay_buffer_size:
    'Maximum number of self-play positions kept for gradient training. New positions are appended and the oldest are discarded once the cap is reached, so this bounds the data each epoch trains on and the memory it uses. A larger buffer keeps more history and diversifies batches; a smaller one keeps training focused on recent play.',
  mixed_precision:
    'Runs the network and gradient updates in half precision when the training device is CUDA, speeding up training and reducing GPU memory use. On CPU and Apple Metal it has no effect. Numerical results can differ slightly from a full-precision run, so it is not bit-exact.',

  // ── Reproducibility and resources ────────────────────────────────────────
  seed: 'Seed for the training random number generators: model initialization, self-play move sampling, symmetry augmentation and the per-iteration and per-game streams derived from it. Repeating a run with the same seed replays the same random choices, so runs stay comparable, but GPU kernels, parallel search processes and floating-point reductions can still introduce small numerical differences. A resumed run must keep the checkpoint’s seed.',
  cpu_threads:
    'Number of CPU threads the training process lets PyTorch use. Higher values speed up CPU-bound work on a multi-core machine but compete with other jobs and the server. It is also passed to OpenVINO as the CPU inference thread count. CPU search workers ignore it and pin one thread each.',
  search_workers:
    'Number of extra CPU processes that run MCTS search during self-play and evaluation. 0 disables the pool. Workers start on demand, are capped by the phase’s game concurrency and are reused across phases. They are only used for CPU models with the PyTorch backend; more workers can raise CPU utilization and throughput but add memory and scheduling overhead.',

  // ── Training loop ────────────────────────────────────────────────────────
  iterations:
    'Number of training iterations. Each iteration generates self-play games, trains the network from the replay buffer, optionally evaluates the model and writes a checkpoint. More iterations mean more self-play data and longer training; the final iteration always evaluates and checkpoints regardless of the frequency settings.',
  checkpoint_frequency:
    'Writes the rolling last checkpoint every N iterations, so less progress is lost if a run is interrupted. The final iteration always writes. This is separate from best-model snapshots, which are written when evaluation records an improvement.',
  save_all_best:
    'Keeps and registers a model file for every strict evaluation improvement instead of only the best model so far. Ties do not create extra models. Enabling it uses additional disk space.',

  // ── Self-play and MCTS ───────────────────────────────────────────────────
  games:
    'Self-play games generated in each training iteration. More games per iteration produce more training positions and a better-filled replay buffer but lengthen the iteration. Multiplied by the iterations, this is the total self-play volume of the run.',
  mcts_iterations:
    'Monte Carlo tree search simulations per move during self-play. More simulations search deeper and produce stronger, more reliable move targets, but every move costs more computation, so self-play games take longer to generate. The search also stops at the time limit, so the effective budget is whichever limit is reached first.',
  mcts_time_limit:
    'Wall-clock limit for the search on each move during self-play, in seconds. The search stops once this time has passed, after at least one simulation, even if the iteration budget is unused. It also becomes the default evaluation time limit when that is not set separately.',
  self_play_mode:
    'How self-play games are executed. Automatic selects batched when the model runs on CUDA or OpenVINO, otherwise sequential. Sequential plays one game at a time. Batched plays up to the concurrency limit together and groups neural evaluations into batches, which is faster on a GPU. Batched and automatic also use the CPU search worker pool when it is enabled.',
  self_play_concurrency:
    'Maximum number of self-play games running at once in batched mode, and an upper bound on how large a neural batch can grow. More concurrent games can raise throughput but use more memory. It has no effect in sequential mode and does not size the CPU search worker pool.',
  max_game_plies:
    'Maximum plies in one self-play game. A game that reaches the limit ends unfinished and its final position is scored from the network’s value estimate rather than a win or loss. Higher limits let long games finish naturally; lower limits shorten iterations.',
  temperature:
    'Sampling temperature used to pick self-play moves from the search policy. Higher values sample more randomly for variety; lower values play closer to the strongest move. After the first 16 plies the effective value drops to a quarter of this setting, with a minimum of 0.1. It affects self-play only: evaluation plays deterministically.',
  exploration:
    'Exploration constant of the search. Higher values favour less-visited moves and widen the search; lower values follow the network’s move priors more greedily. It affects self-play only; evaluation uses a fixed value.',
  dirichlet_alpha:
    'Concentration of the Dirichlet noise added to the root move priors during self-play. Smaller values concentrate the noise on a few moves; larger values spread it more evenly. It only applies while the exploration noise fraction is above zero.',
  dirichlet_fraction:
    'Share of the root move priors replaced by Dirichlet noise during self-play, which encourages trying alternatives to the network’s first choice. 0 disables the noise and 1 replaces the priors entirely. It affects self-play only; evaluation uses no noise.',

  // ── Inference performance ────────────────────────────────────────────────
  inference_batch_size:
    'Maximum number of positions evaluated in one neural network forward pass. Larger batches improve device throughput but use more memory and can only be filled when enough positions are waiting, so the scheduler never waits to complete a batch. Also caps the batch OpenVINO compiles for self-play.',
  training_symmetry_mode:
    'Randomly rotates and reflects training positions together with their move targets so the network learns equivalent board orientations. Off trains on unmodified positions. It changes training samples only, not the game rules or the search budget, and requires a target that supports augmentation.',

  // ── Evaluation ───────────────────────────────────────────────────────────
  evaluation_opponent:
    'Opponent every evaluation compares the candidate against. Shield bearer is a fast one-ply heuristic benchmark for early training; Shield maiden is a stronger rules-aware heuristic with low overhead; Jarl uses alpha-beta search and is the most expensive. Champion compares against the current best model and is the only opponent that can promote the candidate. Changing it starts a new comparison series.',
  evaluation_mode:
    'How evaluation games are executed. Automatic uses batched evaluation when the model runs on CUDA or the inference backend is OpenVINO, otherwise sequential. Batched plays games concurrently and groups neural evaluations; sequential runs one game at a time. The mode does not change the rules of the comparison, but under a search time limit the games share the CPU differently, so the resulting score can differ slightly between the two.',
  evaluation_inference_backend:
    'Inference backend used for evaluation games. PyTorch always evaluates with PyTorch. Use training inference backend follows the job’s neural inference backend, so it only changes anything when that backend is OpenVINO.',
  evaluation_frequency:
    'Runs evaluation every N iterations. The final iteration is always evaluated regardless of this value. More frequent evaluation tracks progress closely but spends more time not generating training data, and it is independent of the checkpoint frequency.',
  evaluation_games:
    'Number of games in one evaluation. It must be even so the candidate plays both sides, and each pair of games shares a seed with the sides swapped. More games give a more reliable score and are needed to decide champion promotion.',
  evaluation_concurrency:
    'Maximum number of evaluation games played at once in batched mode, and an upper bound on how many CPU search workers the phase uses. More concurrency can speed evaluation up but uses more memory; sequential mode ignores it.',
  evaluation_batch_size:
    'Maximum positions per neural forward pass during evaluation. Larger batches improve throughput but use more memory. It has no effect in the sequential path.',
  evaluation_max_plies:
    'Ply limit for one evaluation game. A game that reaches it counts as unfinished: it contributes no score but still counts in the denominator, which lowers the score, and it makes champion promotion inconclusive for that round.',
  evaluation_iterations:
    'MCTS simulations per move for the neural candidate during evaluation, and for the champion when that is the opponent. Higher values make evaluation stronger and slower. It is not used by the heuristic or Jarl opponents.',
  evaluation_time_limit:
    'Wall-clock search limit per move for evaluation, in seconds. It defaults to the self-play search time limit when not set. It applies to the neural candidate and to the Shield bearer and Shield maiden opponents; Jarl uses its own limit instead.',
  evaluation_jarl_depth:
    'Alpha-beta search depth for the Jarl opponent. Higher depth makes Jarl stronger and slower. Only used while Jarl is the selected opponent.',
  evaluation_jarl_time_limit:
    'Wall-clock limit per move for the Jarl opponent’s alpha-beta search, in seconds. Jarl ignores the general evaluation time limit. Only used while Jarl is the selected opponent.',
  champion_confidence:
    'Confidence level of the one-sided bound used to decide champion promotion. Higher values make the bound stricter, so a candidate needs a clearer lead over the current champion to be promoted.',
  champion_min_pairs:
    'Minimum number of complete side-swapped game pairs required before promotion can be decided. Fewer pairs leave the result inconclusive. The evaluation game count must be at least twice this value.',
  champion_min_improvement:
    'Extra score above 50% that the candidate’s lower confidence bound must exceed for promotion. 0 requires the bound to be strictly above 50%; higher values demand a clearer improvement.',

  // ── Hyperparameter tuning ────────────────────────────────────────────────
  // The tuning form edits these as `study.*` identities, while import reports
  // and the shared label table name them by their API key, so both spellings
  // resolve to one description.
  'study.trials': studyTrials,
  'study.study_name': studyName,
  'study.seed': studySeed,
  'study.search_space': studySearchSpace,
  strategy: studyStrategy,
  additional_tuning_iterations: additionalTuningIterations,
  trials: studyTrials,
  study_name: studyName,
  study_seed: studySeed,
  search_space: studySearchSpace,
};

/** Description for one parameter identity, or `undefined` when none is defined. */
export function trainingTooltip(key: string): string | undefined {
  const text = trainingTooltips[key];
  return text && text.trim() ? text : undefined;
}
