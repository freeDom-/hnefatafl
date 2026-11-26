#!/usr/bin/env python3
"""Fetch pinned historical sources for cross-version deployment tests.

The test suite never commits historical release trees. CI fetches the trusted
repository's immutable tags/commits into local refs; the tests extract them into
disposable temporary directories and verify the expected commit IDs.
"""

from __future__ import annotations

import subprocess
import sys

REF_040 = "0.40.0"
REF_041 = "0.41.0"
REF_0411 = "0.41.1"
REF_042 = "0.42"
REF_SCHEMA20 = "refs/historical/schema20"
EXPECTED = {
    REF_040: "21c512f8105a803f00d9efe54c50fb1906e71b0d",
    REF_041: "ca1a640fe6b3971cf7f7764083eee19d1598a19a",
    REF_0411: "8a11ee60fe9aea5734ae261aa38dc733e83b9324",
    REF_042: "0ee2cafd4e2b48d30e9ef8426ebf9a900458f513",
    REF_SCHEMA20: "4bbae495593f3b2e6487027bb5254ddfbc28f9ab",
}


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True)


def commit(ref):
    try:
        return run(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"]).stdout.strip()
    except subprocess.CalledProcessError:
        return None


def main() -> int:
    for ref, expected in EXPECTED.items():
        current = commit(ref)
        if current is None:
            if ref.startswith("refs/tags/"):
                fetch = f"refs/tags/{ref.removeprefix('refs/tags/')}"
                target = fetch
            elif ref.startswith("refs/historical/"):
                fetch = expected
                target = ref
            else:
                fetch = f"refs/tags/{ref}"
                target = fetch
            try:
                run(["git", "fetch", "--force", "--depth=1", "origin", f"{fetch}:{target}"])
            except subprocess.CalledProcessError as error:
                print(
                    "Cannot fetch pinned historical source "
                    f"{ref} ({expected}) from origin: {error.stderr.strip()}",
                    file=sys.stderr,
                )
                return 1
            current = commit(ref)
        if current != expected:
            print(
                f"Historical ref {ref} resolved to {current}, expected {expected}. "
                "Refusing to test an untrusted source.",
                file=sys.stderr,
            )
            return 1
        print(f"{ref} = {current}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
