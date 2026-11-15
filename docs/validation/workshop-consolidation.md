# Workshop presentation and bounded job rendering

[Documentation index](../README.md) · [Training](../TRAINING.md)

Training and AI comparisons reuse Arena materials, typography and semantic error
surfaces. Task navigation opens existing creation/tuning, monitoring, models and
remote-worker controls. Advanced settings, resume, import/export, model protections
and backend training/tuning remain intact. The accessible page heading precedes
the visual introduction. AI comparisons remain separate from human tournaments.

Job navigation renders 20 rows per page and 20 options plus a placeholder or
selected off-page option. Every matching job remains reachable. Keyboard selection,
filtering/sorting, Show selected job and the selected dashboard are preserved.
Polling does not force the current page back to the beginning. Singular job/model
summary labels are corrected in English and German.

## Measured rendering

Identical 1,000 job summaries, no filter or selection, React/jsdom render:

| Measurement | Main baseline | Candidate |
| --- | ---: | ---: |
| Job rows | 1,000 | 20 |
| Select options | 1,001 | 21 |
| Navigator descendants | 8,027 | 191 |

[Recorded result](../benchmarks/workshop-dom.json). Reproduced on the integrated
branch: one benchmark test passed. Run `node frontend/benchmarks/workshop-dom.mjs`.
The harness reads the original component from reviewed main `2579a86`, uses the
same fixture and removes its temporary source files. The 97.62% smaller descendant
count is **not a frame-time, memory, server or network benchmark**. Filtering and
sorting still process all supplied summaries; server paging is not introduced.

## Validation and visuals

Integrated focused unit suite: **134 passed, five files**. TypeScript, formatting
and build passed. Candidate browser validation: eight new presentation cases
passed across the combined run and isolated King's Table retry; four existing
scroll/polling/keyboard regressions passed. An initial matrix timeout under CPU
contention was rerun without raising its 90-second limit. Original sticky/scroll
and keyboard assertions remain. Pagination tests visit the actual last/first page.
Full repository CI is required separately on the PR.

The **88 captures** cover all themes and EN/DE: Training at 320, 390, 768, 1024,
1366, 1920, 3840; comparisons at 320, 768, 1920, 3840. Checks cover overflow,
bounded rows/options, restoring selection, task/deep links and contrast >=4.5:1.
Desktop targets assert Default at 1920 and Large at 3840. CI retains originals
before later configurations clear output.

| Theme | Contact | 1920 | 3840 |
| --- | --- | --- | --- |
| King's Table | [Reviewed](../screenshots/workshop/contact-kings-table.webp) | [Capture](../screenshots/workshop/kings-table-1920.webp) | [Capture](../screenshots/workshop/kings-table-3840.webp) |
| Iron & Ash | [Reviewed](../screenshots/workshop/contact-iron-ash.webp) | [Capture](../screenshots/workshop/iron-ash-1920.webp) | [Capture](../screenshots/workshop/iron-ash-3840.webp) |
| Saga Hall | [Reviewed](../screenshots/workshop/contact-saga-hall.webp) | [Capture](../screenshots/workshop/saga-hall-1920.webp) | [Capture](../screenshots/workshop/saga-hall-3840.webp) |
| Classic Table | [Reviewed](../screenshots/workshop/contact-classic-table.webp) | [Capture](../screenshots/workshop/classic-table-1920.webp) | [Capture](../screenshots/workshop/classic-table-3840.webp) |

Existing chunk warning remains: entry 1,017.90 kB minified /311.40 kB Vite gzip.
Route splitting is separate. Evidence shows populated Training and empty comparison
pages, not every tuning/resume/import/worker dialog. Physical devices/Safari,
true 200% zoom and merged cross-route acceptance remain later work. No backend,
model, checkpoint, schema or dependency changes; no release or merge.

Integrated browser recheck: comparison King's Table passed; Training King's Table
failed twice at the 1024px transition while initializing the session/jobs. The
failure trace contains the heading only after its visibility wait expired; API
responses were successful. This remains under investigation, with no timeout or
assertion relaxation. The integrated branch is not yet accepted for merge.
