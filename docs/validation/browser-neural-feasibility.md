# Browser-neural feasibility audit — Hnefatafl

2026-11-15. Read-only source/model audit plus bounded native/capability probes. **Production browser-neural execution is not implemented or validated; keep it optional/deferred.** No dependencies installed, no models downloaded/trained, no valuable data modified, no tracked edits/branch/commit changes.

> Phase 1B implements the export/parity/browser-inference tooling this audit
> calls for and validates it with a tiny trained smoke checkpoint. See the
> [browser-side analysis roadmap](../designs/browser-analysis-roadmap.md) and
> [Phase 1 validation](browser-analysis-phase1.md). Representative trained
> weights and physical WebGPU remain open; this audit's findings are preserved.

[Documentation index](../README.md) · [Classical browser prototype](browser-analysis-prototype.md)

## Evidence location and reproducibility

The portable probes are committed under `spikes/browser_offline/`. Supply an
actual validated trained checkpoint and a fresh disposable output directory:

```sh
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \
  spikes/browser_offline/neural_probe.py /path/to/last.pt /tmp/neural-native
node spikes/browser_offline/browser_capabilities.mjs /tmp/neural-browser
```

[Recorded native evidence](browser-neural-native.json) and
[recorded browser evidence](browser-neural-capabilities.json) are measurements,
not fixtures that substitute for rerunning conversion. Runtime/model packages are
not application dependencies. The exact development smoke checkpoint used below
is ephemeral and is not distributed in this repository.

- Browser checks used intercepted local responses on a secure HTTPS origin, no application server/TCP ports; permission escalation was used for Chromium startup. Coordinated with performance agent after its measured trace completed.
- Source was repository HEAD2579a86 plus root's in-progress unrelated feature changes; none of this audit changes source. Model architecture files cited below are current sources.

## Actual available trained weights

`HNEFATAFL_DATA_DIR` is unset. Repository `data/` contains the development SQLite file and deployment gate only; no `.pt/.pth/.onnx` model files found there. A bounded `/root/src` search excluding dependency/git trees found no persisted last/best/ONNX artifacts. This is not a claim about disconnected remote hosts or every possible external storage path.

Ephemeral existing browser-test data under `/tmp/hnefatafl-browser-*/jobs/` does contain genuine tiny training outputs. Three safe-loader samples from one fixture root had optimizer step1 or2. The probe selected:

- `/tmp/hnefatafl-browser-YRIfan/jobs/07591f180c174b048769851f8f82107f/last.pt`
- SHA256 `b2951ff366b36375fbdcbae38e7a1eac503ef264980e87496c58148d35917c7f`
- Checkpoint82,859bytes; `tafl-policy-value-v1`, Brandubh, channels4, residualblocks0.
- Recorded iteration2, two generated self-play games, optimizer steps2, last-iteration update1; finite recorded policy/value losses3.296009063720703 /0.00029389094561338425.

These are **actual trained smoke weights**, not a newly initialized synthetic network, but they are tiny two-game development artifacts. They establish neither model strength nor representative trained-model performance. No strength-qualified persisted public model was discovered, and no trained Tablut/Copenhagen weights were validated by this audit. Avoid treating the many browser fixture registry files (which may be initialization-only test models) as trained production opponents.

Model loading used `models.load_checkpoint`, with tensor-only `weights_only=True`, archive size/format/architecture/shape/dtype/finiteness validation (`hnefatafl/models.py:331–383`). No unrestricted pickle loading, optimizer resume, database query/output or raw private checkpoint metadata was printed.

## Native probe: what actually succeeded

Environment: PyTorch2.14.1+cpu, OpenVINO2026.4.1, one Torch CPU thread.

| Native result | Observed |
| --- | --- |
| Model parameters / FP32 parameter bytes |355 /1,420|
| Input |[1,6,7,7]|
| Policy logits |[1,2401]|
| Defender-value output |[1],0.3232969045639038|
| Logits finite |true|
| Repeated eager CPU outputs identical |true|
| Opening legal moves |40|
| Legal-only normalized policy sum |0.9999998807907104|
| Illegal-action probability sum |0|

This checks native input/output/masking plumbing on one trained Brandubh smoke artifact. It is **not** ONNX parity, browser execution, a calibrated win chance, completed-search strength, full-game accuracy or a speed benchmark.

## ONNX export and runtime status

Installed Python: Torch/OpenVINO present; **onnx, onnxscript and onnxruntime absent**. Node resolution: **onnxruntime-web and onnxruntime-node absent**. No ORT browser runtime is in the initial app bundle.

Bounded attempted modern export of the loaded trained model to `/tmp/arena-neural-audit/brandubh-trained.onnx` (opset18, named planes/policy_logits/defender_value, `dynamo=True`) failed in0.332seconds:

`ModuleNotFoundError: No module named 'onnxscript'`

No ONNX artifact was produced. No dependency installation or old-exporter workaround was attempted. Therefore native ONNX execution, ONNX CPU parity and browser ORT/WASM/WebGPU parity were not run with the existing installed dependencies. Missing exporter/runtime dependencies are an explicit unvalidated gate, not evidence that the model is unexportable.

## Architecture and export feasibility (source inference, not a conversion claim)

`hnefatafl/models.py:91–110`: six FP32 planes encode attackers, defenders, king, defender turn, throne and restricted corners. Tablut differs in the corner plane. Reuse this exact encoding; do not build a divergent rules implementation.

`models.py:114–188`: convolution, GroupNorm, SiLU, residual additions, flattened query/key batched matrix multiplication, bias broadcast, adaptive average pooling, linear layers and tanh. Graph is ordinary tensor operations and fixed spatial dimensions per ruleset; a compatible ONNX graph is plausible, but the exact exported normalization/reshape/attention ops and each browser execution provider must be tested.

- Separate model instances/checkpoints per ruleset. Inputs[batch,6,N,N]; policy[batch,N⁴], value[batch]. N=7/9/11.
- Action index is `source*N²+target` (`ai.py:262–267`). Compute softmax **over legal indices only**, not all squares then pretend illegal mass is harmless. Terminal/no-legal positions bypass the model (`inference.py:216–240`). Value remains defender-relative regardless of side to move.
- Legality, repetition, terminal outcomes and search must retain shared Python engine/full state. Six neural planes intentionally do not encode repetition history. A cache of complete search results must include ruleset/rules version, full repetition-relevant state, model content hash and effective advisor/search settings; a raw neural-logit cache and a rules-aware search cache are different things.
- Existing server OpenVINO conversion (`inference_backends.py:166–198`) traces an eval snapshot, converts/compiles, checks dimensions and native FP32 parity. It is a useful pattern, not ONNX or browser proof. Existing tests/test_openvino.py explicitly distinguish CPU parity from GPU evidence; no OpenVINO conversion was rerun here.

Analytic FP32 parameter storage from the actual architecture (not measured ONNX download size or browser heap):

| Channels / blocks | Parameters | FP32 weight bytes |
| --- | ---: | ---: |
|4 /0 (actual probe)|355|1,420|
|32 /2|40,619|162,476|
|48 /3|130,219|520,876|
|128 /8 (permitted maximum)|2,377,259|9,509,036|

These counts exclude graph/runtime/metadata/activations/workspace and transmission encoding. Policy output alone is9,604bytes forBrandubh,26,244 forTablut,58,564 forCopenhagen. Runtime installation/startup and search scheduling may dominate small-model execution; no comparison was measured.

## Actual browser capability and CSP findings

Headless cloud Chromium156.0.8078.4; secure intercepted HTTPS fixture:

- Worker API present and an actual same-origin worker round-trip succeeded under the repository's current CSP literal.
- WebAssembly API present; compiling a minimal valid Wasm module succeeded without CSP.
- With the exact current `auth.py:535–536` CSP applied to the fixture, compiling that same minimal module **failed with CSP CompileError**. Current policy lacks a Wasm evaluation permission. No policy was changed. A reviewed narrow Wasm allowance/served-artifact design and real application-header tests are required; do not add broad unsafe-eval simply to hide this failure.
- `navigator.gpu` present, but `requestAdapter()` returned **null**. This environment does not demonstrate usable WebGPU execution. API presence alone is insufficient for selecting a GPU path.
- `crossOriginIsolated=false`, SharedArrayBuffer undefined. No COOP/COEP isolation is configured in the source middleware. Multi-threaded Wasm/worker deployment cannot be assumed; test a bounded single-thread fallback first. Adding isolation has cross-origin asset/window behavior consequences and needs deliberate product/security testing.
- hardwareConcurrency4 and deviceMemory32GiB are browser hints, not measurements of an end user's available memory or physical-phone capability.

Probe used the current policy literal on a test response; it did not validate a deployed HTTPS/reverse-proxy site. No ORT session, model operator support, Safari/Firefox/WebGPU device, physical mobile, cold/cache initialization, cancellation/memory-pressure behavior or actual neural inference was exercised in the browser.

## Upstream documentation and licensing

> Current repository status (2026-11-22): the project now uses the PolyForm
> Noncommercial License 1.0.0 and maintains third-party notices. The observations
> below record the earlier browser-neural assessment; they do not describe the
> current project license. See [project licensing](../LICENSING.md).

Primary sources reviewed2026-11-15:
- [PyTorch2.14 ONNX documentation](https://docs.pytorch.org/docs/2.14/onnx.html): use an explicitly supported conversion path and required tooling for the installed framework version.
- [ORT Web overview](https://onnxruntime.ai/docs/tutorials/web/): browser use requires a compatible ONNX model and preprocessing/postprocessing; GPU execution-provider operator coverage is a subset.
- [ORT Web deployment](https://onnxruntime.ai/docs/tutorials/web/deploy.html): separately deploy matching JS, Wasm and model assets; same-origin worker loading supports restricted CSP deployments, and WebGPU needs a secure context. Conditional Wasm-only imports/custom builds can reduce runtime cost but were not downloaded/measured here.
- [ORT upstream license](https://raw.githubusercontent.com/microsoft/onnxruntime/main/LICENSE) identifies MIT; [ONNX upstream license](https://raw.githubusercontent.com/onnx/onnx/main/LICENSE) identifies Apache2.0. These are upstream-main license identifications, not a complete notice inventory for any pinned distribution.

Before shipping: inventory exact pinned ORT/Pyodide/CPython/Emscripten/third-party artifact licenses/notices; ship required notices and hashes; define update/security policy; establish owner-approved app distribution and model-rights policy. No top-level repository LICENSE was found during this audit; no license was selected on the owner's behalf. ONNX serialization does not license arbitrary user's weights for publication. Keep private model artifacts opt-in and never expose resumable checkpoints/RNG/optimizer state or filesystem paths in a browser download endpoint.

## Gates before production browser neural execution

1. Obtain representative actual trained weights for all3rulesets, identify their architecture/content hash and training/evaluation limitations. Do not replace missing trained evidence with random-model throughput.
2. In a separately authorized isolated environment, pin exporter/ONNX/ORT versions; export validated inference-only weights into /tmp, inspect operator set, run ONNX checking and CPU numerical parity. Never mutate the training originals or make model packages an eager app dependency.
3. Verify FP32 policy logits/value, legal-only probabilities and supported terminal/repetition positions across PyTorch→ORTCPU→browserWASM; then separately WebGPU if an adapter/provider is genuinely operational. Existing OpenVINO tolerances can inform an initial test policy, not substitute for measurement. Near-tied move ranking needs explicit evaluation rather than blind exact-topmove equality.
4. Preserve normalized shared analysis result types and explicit `auto/local/server/disabled` behavior. Use bounded worker scheduling, generation/cursor/advisor cancellation, termination/recreation and bounded state/model/settings caches. Expensive fallback must be explicit/bounded and must not bypass backend403 or leak active-rated-online assistance.
5. Test real CSP headers and same-origin packaging, interrupted/failed model loads, offline/cache upgrades, unsupported devices, cancellation, UI responsiveness, CPU/memory/download budgets and worker exhaustion. Report fresh/cached startup percentiles and actual transfer/heap measurements.
6. Benchmark completed searches and playing strength (with unfinished searches distinguished) on Brandubh/Tablut/Copenhagen and physical low-end devices. Native tiny smoke outputs or API availability do not establish strong play or production device compatibility.
7. Retain server authority for official games/clocks/results/ratings/tournaments/achievements/leagues. Public spectator pages never initialize an advisor or trigger model downloads/analysis.

Recommendation: continue the existing Python/Pyodide classical-analysis prototype as the immediate G path; keep neural browser integration gated. Current audit establishes valid tiny trained-native plumbing plus concrete missing runtime/CSP/GPU-adapter evidence, not a production neural backend.
