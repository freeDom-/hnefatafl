# Schedule-Free AdamW: RTX 3060 validation procedure

[Training](TRAINING.md#schedule-free-adamw) · [CUDA setup](PERFORMANCE.md#separate-cuda-environment)

This is a reproducible hardware acceptance procedure, not measured RTX 3060
results. The PR implementation environment has CPU PyTorch and no operational
CUDA device. Its conditional CUDA tests skip; neither RTX 3060 nor MPS validation
is claimed. Historical [CPU measurements](benchmarks/schedulefree-adamw.md) remain
CPU evidence and predate these robustness follow-ups.

## Prepare the GPU environment

Use an RTX 3060 with a working NVIDIA driver and sufficient free device memory.
Record the actual device; results from another GPU must retain that GPU's name.
Keep the CPU lock/environment intact and use the existing separate-CUDA recipe.
Select the wheel index supported by your driver using the
[official PyTorch installer](https://pytorch.org/get-started/locally/). The example
below uses CUDA 12.8; it does not install an NVIDIA driver or establish compatibility
with a particular installed driver.

```sh
python3 -m venv .venv-cuda
.venv-cuda/bin/python -m pip install --upgrade pip
.venv-cuda/bin/python -m pip install 'torch>=2.6,<3' --index-url https://download.pytorch.org/whl/cu128
.venv-cuda/bin/python -m pip install -e '.[train,dev]'
mkdir -p work/schedulefree-cuda
export CUDA_VISIBLE_DEVICES=0
export CUBLAS_WORKSPACE_CONFIG=:4096:8
export OMP_NUM_THREADS=2
export MKL_NUM_THREADS=2
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv > work/schedulefree-cuda/hardware.csv
.venv-cuda/bin/python - <<'PY' > work/schedulefree-cuda/environment.json
import importlib.metadata
import json
import platform
import torch
assert torch.cuda.is_available(), 'CUDA must be operational; do not substitute a CPU run'
print(json.dumps({
    'python': platform.python_version(),
    'torch': torch.__version__,
    'cuda_runtime': torch.version.cuda,
    'schedulefree': importlib.metadata.version('schedulefree'),
    'gpu': torch.cuda.get_device_name(0),
    'capability': torch.cuda.get_device_capability(0),
}, indent=2))
PY
git rev-parse HEAD > work/schedulefree-cuda/revision.txt
```

Do not run CPU `uv sync` into this CUDA environment. The API server or remote
worker must use the CUDA environment's Python when actually launching GPU jobs.
The local API must also pass its Schedule-Free dependency execution probe.

## Run correctness and continuation checks

```sh
.venv-cuda/bin/python -m pytest tests/test_schedulefree_cuda.py tests/test_schedulefree.py tests/test_optimizer_lifecycle.py -q -ra > work/schedulefree-cuda/tests.log 2>&1
```

Check the command's exit status and retained summary. All six tests in
`test_schedulefree_cuda.py` must execute, with FP32 and AMP cases. A CPU host will
skip them, which is not a successful CUDA validation. The workspace configuration
above must be set before Python initializes CUDA. Tests disable TF32, cuDNN
benchmarking and nondeterministic algorithms and restore process settings afterward.
The AMP jobs use CUDA autocast and the production GradScaler path.

| Check | Evidence |
| --- | --- |
| CUDA FP32 and mixed precision | Each acceptance test runs both precision modes with real Brandubh networks |
| Batched self-play with averaged parameters | The real generation/evaluation entry points verify upstream evaluation mode and compare weights with the preceding optimization's averaged snapshot |
| Successful/skipped updates and warm-up | Nonfinite gradients are injected before unscale; AMP skips the update, then successfully updates without advancing warm-up for the skipped batch; the existing CUDA scaler test also checks two successful steps around a skip |
| Nonfinite FP32 gradients | The real optimization loop rejects them, keeps counters/weights unchanged and exits with evaluation parameters |
| Checkpoint and resume | Three-iteration uninterrupted training is compared with cancellation after one completed iteration and resume, including model, complete optimizer, replay, Python/PyTorch/CUDA RNG, scaler and learning history |
| Exports and registration | Best, inference and registered models match the weights that produced their recorded evaluation; reloaded exports produce legal inference |
| Champion evaluation | A real CUDA candidate/Champion evaluation leaves candidate and independently frozen incumbent weights unchanged |
| Champion promotion | A deterministic paired-result fixture exercises the unchanged conservative promotion rule; snapshots remain frozen and optimization state survives promotion |
| Mode errors | CPU-focused fault injection verifies original-exception preservation, blocked unsafe checkpoints, and preservation of the previous valid checkpoint |

Continuation tolerances on the same GPU/software configuration are tensor
`rtol=1e-6, atol=1e-7` for FP32 and `rtol=1e-5, atol=1e-6` for AMP. Optimizer
scalar counters/averaging settings and RNG/scaler state must match exactly;
non-timing history floats use the corresponding precision tolerance. Timings are
excluded. These are acceptance bounds to verify on hardware, not previously
observed CUDA error measurements. Record failures rather than widening tolerances
without investigation. Results do not promise reproducibility across GPU,
PyTorch, CUDA or driver versions.

The promotion fixture supplies paired winning outcomes to make promotion
deterministic. It does not claim the short CUDA-trained model earned that score
in real games, and it does not change production pairing or thresholds.

## Measure throughput and memory

The existing Schedule-Free benchmark now accepts an explicit CUDA device and AMP
flag. Its CPU defaults remain available. CUDA requests fail when CUDA is unavailable;
there is no CPU fallback or fabricated GPU memory result.

```sh
.venv-cuda/bin/python scripts/benchmark_schedulefree.py --device cuda --output work/schedulefree-cuda/fp32-benchmark.json > work/schedulefree-cuda/fp32-benchmark.log 2>&1
.venv-cuda/bin/python scripts/benchmark_schedulefree.py --device cuda --mixed-precision --output work/schedulefree-cuda/amp-benchmark.json > work/schedulefree-cuda/amp-benchmark.log 2>&1
```

For a single small diagnostic case:

```sh
.venv-cuda/bin/python scripts/benchmark_schedulefree.py --device cuda --mixed-precision --case adamw_schedulefree 4 0 > work/schedulefree-cuda/small-case.json
```

The full comparison uses identical seeded architectures (4/0, 32/2, 64/4,
128/8 channels/blocks), device, precision, 256-position replay, batch 32,
two epochs and three repetitions. Each repetition attempts 16 updates.
The optimizer step post-hook counts actual executed updates for both optimizers;
AMP may skip attempts. Schedule-Free's internal counter is checked against it.
Warm-up is 32 successful updates. Compare FP32 with FP32 and AMP with AMP,
checking counts and game work before interpreting timing differences.

CUDA synchronization brackets optimization, evaluation, mode-switch timing,
checkpoint writes and resume. Reports include:

- Optimization timings, successful updates per repetition and actual updates/second.
- A bounded self-play/optimization/evaluation iteration and train/eval-pair timing.
- `peak_gpu_bytes` (PyTorch maximum allocated) and `peak_gpu_reserved_bytes`.
- CPU process RSS, optimizer tensor size, checkpoint size/write time and resume time.
- Device, precision, deterministic setting, GPU name and CUDA/PyTorch versions.

GPU peaks cover the entire case, including a second loaded model/optimizer during
the resume measurement; they are not isolated steady-state training memory or
`nvidia-smi` process memory. The checkpoint includes 8,000 independently stored
Brandubh replay positions. Check its unchanged 128 MiB limit rather than changing
global validation. Positive actual updates and finite losses are required before
interpreting a run as optimization evidence. Short timings are not strength
evidence; use sustained repeated hardware runs to assess practical overhead.

Retain the environment, revision, test log and unedited JSON/log measurements
together. Keep weights, environments and runtime evidence under ignored `work/`
or isolated data storage, and add published evidence only after reviewing actual
hardware results. No GPU numbers have been added to the CPU results in this PR.
