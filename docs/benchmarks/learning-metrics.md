# Learning metric performance measurements

[Training dashboard](../TRAINING_DASHBOARD.md) · [Development](../DEVELOPMENT.md)

Production React/SVG measurements on 2026-11-10 cover the Learning view through 100,000 iterations. They motivated four small optimizations: expand parameter columns once per sparse snapshot, reuse normalized chart rows, omit redundant SVG commands on constant step stretches, and inspect sparse snapshots for configured-metric availability. No observations are removed from keyboard/pointer inspection or the exact-value table.

## Method

- Headless Chromium 154.0.8037.57, Node v24.19.0, Linux, AMD EPYC 9V74 80-Core Processor, 2 CPUs reported by Node; 1280 × 900 viewport. No CPU throttling was applied.
- The [benchmark harness](../../frontend/benchmarks/learning.tsx) imports the actual Learning view, metric registry, history normalization and shared chart. The [runner](../../frontend/benchmarks/run.mjs) builds a separate production entry and serves it locally; it does not start the API or access an account.
- Five independent pages per normal history size; three for the 100,000-row legacy case. Tables below report medians. Other test processes were stopped for the final timing run. These are measurements, not CI timing thresholds.
- Each synthetic row includes both losses, replay occupancy, optimizer updates, rates and a 37-field performance snapshot. Evaluations occur every fifth iteration. Sparse parameter snapshots preserve the 1–1000 / 1001–1250 / 1251-onward boundaries, then resume every 5,000 iterations. Every 701st row omits its parameter reference, and a genuine zero noise fraction is retained.
- The legacy case records reliable parameters only in its final 250 iterations; the preceding 99,750 remain unknown. Input JSON sizes are identical between paired before/after runs: about 129.6 MiB at 100,000 normal rows, and 126.0 MiB for legacy.
- Assets and fixture construction are outside measured UI operations. Initial rendering includes the same history normalization as the selected-job dashboard. Metric changes use the actual select event; range changes use the shared numeric windows. Appending supplies a new array with one more iteration; a heartbeat reuses the existing history.
- Commit timings include React commit and forced synchronous layout. The frame column waits for two animation frames and includes subsequent SVG/layout work; it is not a measured network-to-screen latency. ResizeObserver is enabled.
- JavaScript heap deltas use CDP `Runtime.getHeapUsage` after forced GC. Projection overhead is above the raw-plus-normalized input; total UI overhead is above the raw input alone. Native DOM/SVG/GPU allocations and transient peaks are not included. The disclosure stays closed: the final scene has one chart, zero tables and 285 DOM nodes at every size.

## Final reconstruction and memory

All times are milliseconds; heap deltas are MiB.

| Iterations | Normalize | Reconstruct parameters | Availability | Projection heap | Total UI heap |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1,500 | 0.9 | 1.5 | 0.2 | 0.3 | 1.2 |
| 10,000 | 6.1 | 12.1 | 0.4 | 2.1 | 4.3 |
| 50,000 | 12.4 | 38.1 | 1.6 | 10.7 | 20.0 |
| 100,000 | 48.6 | 76.8 | 3.0 | 21.3 | 39.5 |

## Final interaction times

| Iterations | Initial commit | Two frames | Range 250 | Range 10 | All iterations | Append one | Heartbeat |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,500 | 66.0 | 77.9 | 3.4 | 3.4 | 4.3 | 5.6 | 0.6 |
| 10,000 | 81.8 | 93.9 | 4.0 | 3.8 | 12.5 | 23.6 | 0.7 |
| 50,000 | 155.0 | 197.3 | 4.8 | 4.6 | 52.2 | 119.8 | 0.7 |
| 100,000 | 298.6 | 412.1 | 4.8 | 5.1 | 107.3 | 255.5 | 0.6 |

Metric switches below retain All iterations. They select weight decay, batch size, MCTS iterations, used learning rate, then the default two-loss graph.

| Iterations | Weight decay | Batch size | MCTS iterations | Used rate | Both losses |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1,500 | 6.7 | 4.5 | 3.7 | 4.3 | 4.3 |
| 10,000 | 11.0 | 5.5 | 4.6 | 9.6 | 13.1 |
| 50,000 | 27.1 | 12.4 | 12.5 | 35.2 | 85.7 |
| 100,000 | 63.3 | 18.0 | 17.3 | 73.1 | 152.6 |

## Before and after at 100,000 iterations

The baseline uses the original PR data/plot pipeline with the weight-decay scale correction and the same measurement-function extraction. Final source adds the optimizations described above.

| Operation | Baseline | Final |
| --- | ---: | ---: |
| Parameter reconstruction (ms) | 143.4 | 76.8 |
| Initial commit (ms) | 469.0 | 298.6 |
| Append one iteration (ms) | 384.1 | 255.5 |
| Weight-decay switch (ms) | 132.8 | 63.3 |
| Both-loss switch (ms) | 118.9 | 152.6 |
| Total UI heap (MiB) | 60.8 | 39.5 |

The gains are not uniform: the full two-loss switch was slower in this sample, while initial rendering, appended progress, configured metrics and retained heap improved. Full loss/rate curves still retain every point; no downsampling or broader linear-chart rewrite was introduced. Recent windows remain fast.

### Legacy history with only 250 reliable parameter rows

| Operation | Baseline | Final |
| --- | ---: | ---: |
| Parameter reconstruction (ms) | 118.5 | 66.3 |
| Availability (ms) | 38.3 | 2.8 |
| Initial commit (ms) | 471.5 | 278.2 |
| Append one iteration (ms) | 359.4 | 251.0 |
| Total UI heap (MiB) | 51.0 | 33.1 |

Availability adds one linear snapshot scan for normal histories (about 3 ms at 100,000 rows) to avoid scanning a long unknown prefix for each configured metric. It remains memoized on history changes. Heartbeats and range/metric changes reuse the reconstructed timeline. No incremental or mutable history cache was added.

## Reproduce

From the repository root, after installing locked dependencies and Playwright Chromium:

```sh
npm --prefix frontend run benchmark:learning -- --samples=5 --label=current
npm --prefix frontend run benchmark:learning -- --samples=3 --counts=100000 --legacy=true --label=legacy
```

For a system Chromium, prefix either command with `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium`. The runner accepts counts from 1 to 100,000, defaults to all four sizes, and writes full samples plus environment/source metadata to ignored `work/learning-benchmark-<label>.json`. It builds a separate site in `work/learning-benchmark-site`; the harness is not part of the shipped application bundle.

Type-check the harness with `npm --prefix frontend run typecheck`. The ordinary production build does not import its entry point.

Production-source SHA-256 fingerprints (the five data/view/chart modules hashed in runner order):

```text
baseline 83315f8657b722bf4846222254f67bd10d7a43ca92eec9820f997b077371d099
final    de0f96ea1efef61b86c6280320c98b57027c7ed217124a2484aaf925d0db1d6d
```

## Limits

These synthetic, component-level measurements exclude API/database work, transfer/JSON parsing, the navigator/header and open historical tables. They do not prove whole-application latency, browser RSS or performance on mobile hardware. At 100,000 rows, full-series actions can still occupy the main thread for hundreds of milliseconds. Loss/evaluation/performance scaling and actual numerical training, checkpoint and archive formats are unchanged.
