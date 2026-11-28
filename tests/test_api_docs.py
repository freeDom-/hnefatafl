"""The developer API tables must not invent method/path combinations."""

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def documented_routes() -> set[tuple[str, str]]:
    text = (ROOT / "docs/development/api/README.md").read_text(encoding="utf-8")
    routes = set()
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip("|").split("|")]
        if len(cells) < 2 or cells[0] not in METHODS or not cells[1].startswith("/api/"):
            continue
        routes.add((cells[0], cells[1]))
    return routes


def test_documented_api_methods_and_paths_exist():
    actual = {
        (row["method"], row["route"])
        for row in json.loads((ROOT / "docs/API_PERMISSIONS.json").read_text(encoding="utf-8"))
    }
    documented = documented_routes()
    assert documented, "expected API route tables"
    assert documented <= actual, sorted(documented - actual)
