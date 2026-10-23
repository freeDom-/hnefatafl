# AI workshop training dashboard

[Documentation index](README.md) · [Training guide](TRAINING.md) ·
[Checkpoint and model management](TRAINING_DATA.md)

The workshop gives the selected training job the main content width. Its compact
header reports the server's training device, active jobs and registered models.
**Remote workers** opens enrollment and worker management. A worker's one-time
credential stays in its existing setup dialog; it is excluded from diagnostics.

## Create and select jobs

**New training** opens **Forge a new strategist** across the page. It opens
initially in an empty workshop and collapses initially when jobs exist. Closing
it keeps all entered settings. General, neural-network/optimization, self-play/MCTS,
evaluation and advanced groups reuse the existing controls and validation.
Training, tuning, presets, remote targets and all evaluation opponents remain
available. Starting or resuming a job selects it, closes creation and focuses the
monitoring workspace on Overview.

A 260px job rail on wide screens shows status, name, ruleset, iteration, creation
time and execution location. Search accepts job names and associated registered
model names; status filtering and active/newest/oldest sorting affect navigation
only. The first selection favors an active job, otherwise the newest job. Polling
and sorting preserve explicit selection. **Hide job list** gives charts the full
workspace width. Tablet and phone layouts use a job selector with collapsible
search/filter controls. A filtered-out selected job remains selected and is
identified in the selector.

## Analysis views

The summary above the tabs keeps the selected name, status, ruleset, execution
location, recorded iteration/total, stage, elapsed time and remote update time
available. Stop, checkpoint/study resume, job export, training-data deletion and
refresh use their existing state restrictions and confirmation dialogs. Progress
uses recorded iteration counts; the interface does not invent an ETA.

- **Overview** shows six concise KPIs, learning/evaluation charts, the last
  completed iteration's sequential phase timings and checkpoint selection.
  Saturation, champion decisions, incomplete evaluations and failed/cancelled
  jobs receive contextual messages.
- **Learning** shows loss trends, replay positions, recorded optimizer updates,
  saved configuration and an accessible iteration table. Lower loss measures
  fit to self-play targets; it does not establish stronger play.
- **Evaluation** shows the effective opponent, side scores, wins/losses, rules
  draws, unfinished games, duration, champion decisions and fixed-benchmark
  records. A series selector defaults to the latest comparison context; all
  contexts remain available. Its detailed table retains the complete evidence.
- **Performance** groups throughput, MCTS, GPU/inference and phase timings.
  Select one historical metric at a time. CPU search, inference-host and CUDA
  activity counters stay separate from the sequential wall-time comparison,
  because their durations may overlap. The full recorded history is available
  in its table.
- **Details** contains lazily opened saved configuration, evaluation settings,
  raw progress, logs, checkpoints, execution metadata and transfer guidance.
  JSON/log panes are bounded and support copying. Credential-shaped diagnostic
  fields are redacted.

Arrow keys move between tabs; Home/End choose the first/last tab. Charts accept
left/right and Home/End keys for exact values and support pointer inspection.
All/recent 10/25/50 iteration controls are shared across analysis views and remain
selected during polling. Data tables mount when opened, keeping long histories
out of the DOM during ordinary monitoring.

## Reading charts correctly

Charts adapt their SVG coordinate width to the available space, keep readable
axis labels and metric units, and avoid animations during polling. Long learning
and performance histories omit repetitive point markers. Values remain available
through tooltips and tables.

Evaluation groups include the recorded opponent identity, effective context and
Champion incumbent. Different opponents, changed search settings and changed
incumbents never share a continuous curve. Scheduled but absent evaluations stay
gaps; they are not zero scores. Champion promotion events are marked on the
relevant evaluation series. Legacy baseline records remain readable and identify
missing effective settings. A high score from a small sample remains limited
evidence; use larger held-out tournaments before claiming general strength.

## Model collection and compatibility

The separate expandable **Model collection** remains below monitoring. Its
import/export, rename/delete, metadata/evaluation summaries, load-parameters and
source-job resume actions retain their existing behavior. A registered inference
model and the source job's latest full checkpoint are distinct artifacts.
Loading parameters opens creation; resuming a model card uses its linked job.

This is a frontend presentation change. Engine/training algorithms, API semantics,
job JSON, checkpoint formats and remote-worker protocols are unchanged. Older
workers may omit newer metrics; missing measurements display as absent. Existing
job-list polling is retained, including its bounded server-provided log tails;
Details does not add log requests.

## Browser evidence

The release's desktop and mobile screenshots use representative persisted-API
fixtures, including a 60-iteration running job, a 120-iteration completed job,
legacy records and several evaluation contexts. These are interface fixtures,
not evidence of trained-model strength or GPU throughput. See the
[v0.21.0 release notes](releases/v0.21.0.md) for executed validation and images.
