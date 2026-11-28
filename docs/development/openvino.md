# OpenVINO inference implementation

[Development index](README.md) · [OpenVINO setup](../training/openvino.md) · [Training data](training-data.md)

This page records the conversion, synchronization, parity and checkpoint
compatibility mechanisms behind the optional OpenVINO backend. Practical installation,
hardware/worker setup and operator limitations remain in
[training OpenVINO](../training/openvino.md).

## Defaults and synchronization

Absent metadata means `inference_backend: pytorch`, `inference_device: auto`.
CPU/CUDA/MPS inference follows the PyTorch optimization device as before.
Existing presets and budgets are unchanged. Auto self-play still selects
sequential for PyTorch CPU/MPS and batched for CUDA. Only explicit OpenVINO
selects batched auto on a CPU training worker. Explicit sequential/batched modes
are respected; concurrency, games and inference batch limits are never raised.

Before each self-play iteration, the backend clones the authoritative CPU model,
traces that frozen copy with TorchScript and converts/compiles one bounded dynamic
batch graph. Its batch dimension ranges from 1 to the minimum of games,
concurrency and inference batch limit. No padding, additional queue delays or
compiled batch cache is used. Conversion/compilation occur outside per-move MCTS
deadlines and are charged to iteration time. A request buffer is reused; results
are copied before reuse. Every compiled snapshot is checked against PyTorch on an encoded starting position
with the documented FP32 tolerance before replacing the previous representation.
Model refresh happens once per iteration, after any
previous optimization, never per batch. Resources are released on completion,
cancellation and Python exceptions. Native failures remain isolated in the
existing training subprocess; previous valid checkpoint files are retained.

TorchScript avoids the experimental export route implicated in the prior native
crash report. PyTorch 2.14 deprecates tracing, so future dependency upgrades need
parity tests before changing conversion routes. GroupNorm's trace warning comes
from its fixed spatial-size validation guard; supported boards always have more
than one spatial element. Variable-batch tests verify that the traced operations
do not freeze batch-dependent computation. This is not a promise that arbitrary
future architectures are safe to trace.

Shared encoding and legal-policy postprocessing preserve source/target indexing,
defender-relative values, terminal handling and legal softmax probabilities.
FP32 parity tests use absolute tolerance `3e-5`, relative tolerance `1e-4`.

Checkpoints retain the existing validated PyTorch tensor/primitives format;
OpenVINO compiled objects are never saved. Resume creates a new inference model
from restored PyTorch weights. The resume dialog allows backend/device changes
while preserving network identity, optimizer buffers, replay, RNG and history.
When switching to PyTorch select inference device `auto`. The CLI also supports
an explicit new backend in resume JSON. Old checkpoints default to PyTorch.
