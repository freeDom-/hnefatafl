# Phase 2 MCTS measurements

These are CPU computation measurements, not playing-strength estimates. The
baseline is `0.26.0` at `f9f68d26578920762aa46e7e408068c86febe30b`.
Its saved `ai.py` SHA-256 is
`8786a8d69fa724d761900289b635d69ad9c0d21582c7f0e988f7e20aadd98eb0`;
the file was verified byte-for-byte against that tag. Both searches use the same
new benchmark/measurement driver, encoding, engine, inference and fixed weights.

## Host and protocol

Linux x86-64, AMD EPYC 7763 virtual CPU, three visible vCPUs, two PyTorch CPU math
threads, Python 3.12.14, PyTorch 2.14.1+cpu and OpenVINO 2026.4.1. No CUDA, MPS or
Intel GPU execution was available. No existing checkpoint was present, so weights
were initialized deterministically with seed 42. Reports fingerprint every used
weight tensor; the architecture and weights match before/after within each case.
No optimizer runs, source-checkpoint writes or model registrations occur.

Three repetitions use fresh processes per implementation/repetition, alternating
baseline/optimized order and off/exact cache order. A process measures both cache
settings; its RSS is therefore a lifetime high-water mark, not a resettable cache-
specific peak. Model initialization and warm-up are excluded. OpenVINO compiles
and warms its frozen graph before timing. Tests/builds did not compete with these
measurements. End-to-end rates use measured self-play wall time, including search,
encoding, inference scheduling, network execution and result assembly.

Brandubh uses 32 channels/two blocks, 32 games/concurrency 32, inference batches up
to 32, seed 42, cache capacity 8192 and a 16-ply cap at 128/256 simulations. Tablut
uses its recommended 48 channels/three blocks/192 simulations; Copenhagen uses
64 channels/four blocks/256 simulations. Their four-game/eight-ply workloads keep
CPU runtime manageable. Additional four-game/eight-ply Brandubh cases test
sequential PyTorch and batched OpenVINO CPU at 128 simulations. All searches have
a generous 300-second ceiling. Precision is FP32 throughout.

Ply caps produce explicitly unfinished games; those are not rule draws. Immediate
winning roots can use zero simulations while still producing training positions.
Trajectory hashes cover encoded inputs, action/visit targets, outcomes and
completed simulations; bootstrap floats are excluded because kernel rounding may
differ. Existing numerical parity tests cover those values. Inspect actual work
and trajectory hashes before interpreting throughput. A
512-simulation correctness test runs, but the throughput matrix is limited to the
two representative Brandubh budgets on this CPU host.

## Repeated before/after results

Medians of three samples; neural/reuse/work counts are identical across repetitions
within each row. Exact means Phase 1 exact-input caching at capacity 8192. Node
reuse precedes the inference pipeline; it is not a cache hit or a simulation.

| Workload / budget | Mode/backend | Cache | Before sim/s | After sim/s | Speedup | Simulations / positions | Neural before → after | Reused |
| --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: |
| brandubh / 128 | batched / pytorch | off | 2178 | 2680 | 1.23× | 54400 / 440 | 79572 → 54753 | 24819 |
| brandubh / 128 | batched / pytorch | exact | 2113 | 2301 | 1.09× | 54400 / 440 | 50925 → 50925 | 24819 |
| brandubh / 256 | batched / pytorch | off | 2232 | 2759 | 1.24× | 111616 / 448 | 162884 → 111851 | 51033 |
| brandubh / 256 | batched / pytorch | exact | 2005 | 2269 | 1.13× | 111616 / 448 | 105224 → 105233 | 51033 |
| tablut / 192 | batched / pytorch | off | 684 | 898 | 1.31× | 6144 / 32 | 8967 → 6178 | 2789 |
| tablut / 192 | batched / pytorch | exact | 689 | 804 | 1.17× | 6144 / 32 | 6021 → 6021 | 2789 |
| copenhagen / 256 | batched / pytorch | off | 400 | 522 | 1.30× | 8192 / 32 | 11931 → 8228 | 3703 |
| copenhagen / 256 | batched / pytorch | exact | 421 | 468 | 1.11× | 8192 / 32 | 8090 → 8090 | 3703 |
| brandubh / 128 | sequential / pytorch | off | 712 | 995 | 1.40× | 4096 / 32 | 6016 → 4128 | 1888 |
| brandubh / 128 | sequential / pytorch | exact | 795 | 793 | 1.00× | 4096 / 32 | 3942 → 3942 | 1888 |
| brandubh / 128 | batched / openvino | off | 1025 | 1422 | 1.39× | 4096 / 32 | 6016 → 4128 | 1888 |
| brandubh / 128 | batched / openvino | exact | 907 | 1078 | 1.19× | 4096 / 32 | 3942 → 3942 | 1888 |

Every before/after pair has identical completed simulations, generated positions,
zero deadline cutoffs and trajectory hashes. Repetitions reproduce their targets
within each mode, and cache off/exact targets match. Thus the improvements are not
explained by doing fewer simulations or removing training examples. The 256-visit
exact-cache case performs nine extra forward positions (105,224 → 105,233), because
removing node requests changes LRU recency and scheduling. That 0.009% difference
does not change measured work or visit targets. No claim is made that every neural
forward count decreases with caching already enabled.

Sequential PyTorch/exact is effectively flat: median 5.150 → 5.168 s (about 0.35%
slower), inside overlapping sample ranges of 5.100–5.289 s and 4.949–5.195 s. No
benefit is established for that case. The other eleven cases improve by 1.09–1.40×.
All measured improvements belong to these CPU workloads, not the supplied CUDA
reference results or another model/checkpoint.

## Memory and rejected prototype

The first implementation retained every leaf's complete policy dictionary. A
256-simulation deterministic, inference-free allocation probe measured 65–80%
more traced Python heap than baseline. That representation was replaced with
owned uint16 action IDs and double probabilities at deferred leaves. Expanded
nodes materialize dictionaries once; arrays preserve probability bits and order.

| Ruleset, 256 simulations | Baseline traced peak | Dictionary prototype | Final packed peak |
| --- | ---: | ---: | ---: |
| Brandubh | 884,368 B | 1,460,176 B | 951,034 B |
| Tablut | 1,562,296 B | 2,811,320 B | 1,689,382 B |
| Copenhagen | 2,133,392 B | 3,631,432 B | 2,278,842 B |

The final probe adds 6.8–8.1% traced heap (about 65–142 KiB per search in these
positions), trading bounded host storage for avoided prediction requests. It uses
uniform legal priors and static defender values, so it isolates tree allocations
rather than neural-runtime memory. After exhaustion/collection it retained
0–1,520 traced bytes and no complete trees; a weak-reference cancellation test
also checks release. Memory scales with legal branching, simulation budget and
concurrency; larger boards/budgets need their own measurements.

| Workload / budget / mode | Cache | Baseline process peak MiB | Optimized process peak MiB |
| --- | --- | ---: | ---: |
| brandubh / 128 / batched pytorch | off | 216.1 | 215.0 |
| brandubh / 128 / batched pytorch | exact | 270.2 | 277.6 |
| brandubh / 256 / batched pytorch | off | 228.3 | 230.3 |
| brandubh / 256 / batched pytorch | exact | 275.4 | 291.0 |
| tablut / 192 / batched pytorch | off | 199.7 | 199.9 |
| tablut / 192 / batched pytorch | exact | 278.0 | 281.0 |
| copenhagen / 256 / batched pytorch | off | 204.9 | 205.8 |
| copenhagen / 256 / batched pytorch | exact | 350.6 | 356.5 |
| brandubh / 128 / sequential pytorch | off | 193.1 | 193.0 |
| brandubh / 128 / sequential pytorch | exact | 219.3 | 219.6 |
| brandubh / 128 / batched openvino | off | 371.4 | 371.5 |
| brandubh / 128 / batched openvino | exact | 395.6 | 398.2 |

The largest median process-peak increase is about 15.6 MiB (5.7%) in Brandubh
256/exact at concurrency 32. Other rows range from a slight decrease to smaller
increases. This bounded memory cost accompanies the measured request/throughput
gains; it is not a claim of zero memory regression. The process measure includes
runtime, caches and both ordered scenarios; use separate per-cache processes for
strict incremental cache-memory attribution.

## Profiling and implementation decisions

A pre-change cProfile run used eight Brandubh games/eight plies, 32 channels/two
blocks, 128 simulations and cache off. It completed 8192 simulations/64 positions
with 12,007 inference requests and no deadline cutoffs. Profiled self-play wall
was 7.40 s; host inference activity was 4.33 s and traversal activity 2.01 s.
These counters have different boundaries and must not be added as a full wall-
time decomposition. Profiling adds overhead; these are diagnostic timings, not
the repeated throughput measurements above.

The profile recorded 2.77 s cumulative model forward, 1.68 s convolution self time,
1.75 s cumulative legal-move generation and 1.92 s cumulative MCTS traversal.
PUCT's candidate lambda consumed about 0.083 s cumulative across 79,569 calls.
Scheduler `advance` accumulated 2.02 s including the search, leaving little
standalone scheduler overhead. Cumulative timings overlap and are not additive.

This justified removing delayed-expansion neural requests first. Each neural node
now saves its first value and legal policy; every simulation still updates visits
and totals independently. PUCT hoists the square root, retains arithmetic order
and first ties, and widening uses an exactly equivalent integer threshold.
Weighted sampling/order and the scheduler remain intact. Keeping every root
candidate state or changing engine adjudication would add memory or risk history/
variant behavior; neither was needed. Extra sampler containers and a different
batch scheduler were omitted because this profile did not justify their cost or
behavioral risk. No symmetry merging, tree transpositions or algorithm change was
introduced.

For Brandubh/128, MCTS neural requests fall from 79,555 to 54,736: 24,819 avoided
requests, about 31%. Pipeline requests additionally include 17 bootstrap positions.
Exact-cache hits drop from 28,425 to 3,640, while within-batch duplicates drop from
222 to 188. Actual exact-cache forwards remain 50,925. Removing easy node-repeat
hits therefore lowers hit rate without invalidating the cache or reducing search.

Cache off is faster than exact in the representative CPU Brandubh cases after
optimization. Higher hit rate or capacity alone is not an end-to-end benefit.
Only capacity 8192 was compared; larger capacities are not recommended from these
measurements. Sequential/exact's flat timing is another example of fewer requests
failing to produce an overall speedup.

Batched traversal activity can rise: at Brandubh/128 off, its median is
5.47 → 6.38 s, while measured host inference activity falls 14.89 → 10.19 s and
self-play falls 24.98 → 20.30 s. Packing/unpacking is CPU search work; previously
repeated legal-policy processing lived in inference. Metric boundaries retain
their prior definitions. The profile and total timings, rather than an isolated
operation count, justify the combined change.

## Reproduce and interpret

Use the [Brandubh](../../configs/benchmarks/mcts-phase2-brandubh.json),
[Tablut](../../configs/benchmarks/mcts-phase2-tablut.json) and
[Copenhagen](../../configs/benchmarks/mcts-phase2-copenhagen.json) configurations:

```sh
.venv/bin/python -m hnefatafl.cli benchmark \
  --config configs/benchmarks/mcts-phase2-brandubh.json --output work/brandubh.json
```

Change `mcts_iterations` to 256 for the second Brandubh budget; use four games,
eight plies and `self_play_modes: ["sequential"]` for the sequential case. For
OpenVINO, select `devices: ["cpu"]`, `inference_backend: "openvino"`,
`inference_device: "CPU"` and batched execution. To use a real fixed checkpoint,
add `checkpoint` and omit or match its architecture settings. CUDA measurements
require a compatible wheel/hardware; select `devices: ["cuda"]` and explicitly
choose `benchmark_mixed_precision` on that host.

The release's raw JSON and reproduction bundle preserve every scenario, weight/
search fingerprint, timing, count, memory observation and trajectory hash. The
bundle's `work/phase2/measure_all.py` substitutes only the saved baseline search
for before samples and uses the same benchmark function on both implementations.
Run it from the 0.27.0 checkout after extracting the bundle; all generated files
stay under ignored `work/`. Initial exploratory samples were discarded once
validation jobs overlapped them; they are not included in the final medians.

No GPU speedup, stronger model or universal optimal cache capacity follows from
these runs. Only capacity 8192 is compared here. Retune capacities with actual
GPU/checkpoint workloads, compare end-to-end work and memory, and evaluate strength
separately. Phase 3 should test D4 training augmentation independently of search;
existing networks are not assumed invariant under symmetry. Engine/legal-mask
costs and physical GPU behavior remain follow-up measurements.
