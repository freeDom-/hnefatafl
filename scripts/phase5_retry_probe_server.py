"""Serve a disposable production build with one injected advisor interruption."""

import argparse
import os
from pathlib import Path
import threading

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path, help="Disposable probe data")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    os.environ["HNEFATAFL_DATA_DIR"] = str(args.data_dir.resolve())
    from hnefatafl.server import app

    evaluate = app.state.analyses.evaluate
    lock = threading.Lock()
    interrupted = False

    def interrupt_once(game, role, agent, settings, progress, *, state):
        nonlocal interrupted
        result = evaluate(game, role, agent, settings, progress, state=state)
        if role == "advisor" and agent["name"] == "Phase 5 retry probe":
            with lock:
                if interrupted:
                    return result
                interrupted = True
            progress(result)
            raise RuntimeError("Injected interruption after publishing advisor progress")
        return result

    app.state.analyses.evaluate = interrupt_once
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
