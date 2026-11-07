"""Execute frozen historical archive writers, without translating schema-3 exports."""

import importlib.util
import importlib.machinery
from pathlib import Path


def legacy_writer(version):
    path = Path(__file__).parent / "fixtures" / "job_archives" / f"schema{version}_writer.py.txt"
    name = f"hnefatafl._historical_archive_writer_{version}"
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    assert module.FORMAT_VERSION == version
    return module


def historical_export(job, directory, registry, output, version=2):
    legacy_writer(version).export_job(
        job, directory, registry, output, "0.35.0" if version == 1 else "0.36.0"
    )
    return output.read_bytes()
