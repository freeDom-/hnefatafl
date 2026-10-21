"""Isolate the module-level ASGI app before test modules are imported."""

import os
import tempfile


_previous_data_dir = os.environ.get("HNEFATAFL_DATA_DIR")
_test_data_dir = tempfile.TemporaryDirectory(prefix="hnefatafl-pytest-")
os.environ["HNEFATAFL_DATA_DIR"] = _test_data_dir.name


def pytest_unconfigure() -> None:
    if _previous_data_dir is None:
        os.environ.pop("HNEFATAFL_DATA_DIR", None)
    else:
        os.environ["HNEFATAFL_DATA_DIR"] = _previous_data_dir
    _test_data_dir.cleanup()
