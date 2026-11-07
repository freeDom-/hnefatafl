# Historical training-job archives

These are original ZIP bytes produced by complete historical application sources,
wrapped in gzip solely to keep the test fixtures small (about 300 KiB in total).
Tests unwrap that outer fixture packaging and give the unchanged ZIP bytes to the
application importer. No fixture was converted from a schema-3 export.

- Schema 1: commit `efbbdbb99f6ba886ddd4dd22f9ff310c4ce80c6b`, application 0.35.0.
- Schema 2: commit `0407d88c08d2f8441f5763d084f209fc9d822656`, application 0.36.0,
  before the TAR.GZ changes.

Each version generated a real one-iteration CPU smoke job with its original
trainer, an optimization study, a frozen-opponent study and a metadata-only job
with missing optional artifacts. The toy checkpoints include real optimizer,
replay and RNG state. There is no user training data. `provenance.json` records
source/writer hashes, original ZIP hashes and byte counts.

Regenerate from a checkout containing those historical commits:

```sh
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 .venv/bin/python scripts/generate_legacy_job_fixtures.py
```

The generator places source checkouts, databases and unwrapped models under
`work/` and removes them afterward. Training timings, generated IDs and checkpoint
creation timestamps can vary; regeneration is structural rather than byte-for-byte.
The stored ZIP hash is checked before using each golden fixture.

The two `schemaN_writer.py.txt` source snapshots are exact copies from the same
commits. Smaller custom metadata tests execute these old writers directly, while
the golden binaries above establish compatibility with complete old applications.
The source snapshots are deliberately excluded from format rewriting.

Schema-1 frozen studies predating verifiable content pins are rejected safely;
tests must not invent pins or weaken current frozen-opponent validation. Ordinary
legacy studies can be inspected/imported without granting them a new experiment
identity. Schema-2 pinned frozen dependencies remain importable and deduplicate
through the current model registry.
