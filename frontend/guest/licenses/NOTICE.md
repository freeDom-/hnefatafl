# Guest runtime third-party notices

This package ships the **unmodified Pyodide 0.27.7 core**, obtained from the
SHA-512-pinned npm `pyodide@0.27.7` archive. No Python packages or model weights
are installed at runtime. Its source, build instructions and corresponding source
code are available at https://github.com/pyodide/pyodide/tree/0.27.7.

- Pyodide: Mozilla Public License 2.0, included as `Pyodide-MPL-2.0.txt`.
  The executable files remain unmodified; this project uses its public API.
  Corresponding source is available from the tagged source above, including
  https://github.com/pyodide/pyodide/archive/refs/tags/0.27.7.tar.gz.
- CPython 3.12.7: PSF license and historical notices in `CPython-LICENSE.txt`.
  Additional bundled-component notices (including expat, zlib, libffi, bzip2,
  libmpdec and other standard-library components) are reproduced in
  `CPython-third-party.rst`, from the tagged CPython documentation. Source:
  https://github.com/python/cpython/tree/v3.12.7.
- Emscripten 3.1.58 (the version specified by Pyodide 0.27.7's `Makefile.envs`):
  MIT and University of Illinois/NCSA notices in `Emscripten-LICENSE.txt`.
  Source: https://github.com/emscripten-core/emscripten/tree/3.1.58.
- Its bundled musl libc attribution is included in `Emscripten-musl-COPYRIGHT.txt`,
  from `system/lib/libc/musl/COPYRIGHT` at that tag. The matching libc++, libc++abi
  and compiler-rt licenses (Apache 2.0 with LLVM exceptions and legacy terms) are
  included in `Emscripten-libcxx-LICENSE.txt`, `Emscripten-libcxxabi-LICENSE.txt`
  and `Emscripten-compiler-rt-LICENSE.txt`, from their `system/lib/` directories
  at the same tag. Retain these linked-runtime notices with the core package.

The application engine/guest bridge are separate application source files; this
notice does not assign a new license to the Hnefatafl repository. Operators must
retain these notices and source-access links when distributing the generated
runtime directory. Runtime upgrades require an explicit version/integrity update,
review of matching notices, replay/worker parity and production-CSP browser checks.
No production service worker is installed; there is no persistent service-worker
runtime cache or claim of installability or fully disconnected startup.
