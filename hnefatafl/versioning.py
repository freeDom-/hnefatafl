"""Installed application identity, without invoking Git in production."""

from importlib.metadata import PackageNotFoundError, distribution, version
import json
import re


def known_version(value: object) -> str | None:
    # Compare exact canonical release identities, never major/minor ranges. The
    # source-tree fallback (0+local) deliberately cannot establish compatibility.
    if (
        isinstance(value, str)
        and len(value) <= 100
        and re.fullmatch(
            r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
            r"(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?"
            r"(?:\+[a-z0-9]+(?:[.-][a-z0-9]+)*)?",
            value,
        )
    ):
        return value
    return None


def known_revision(value: object) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", value):
        return value.lower()
    return None


def application_metadata() -> dict:
    try:
        package = version("hnefatafl")
    except PackageNotFoundError:
        package = "0+local"
    revision = None
    try:
        # PEP 610 records an immutable revision for a VCS installation. Editable
        # checkouts can change underneath their metadata and are not evidence.
        direct = json.loads(distribution("hnefatafl").read_text("direct_url.json") or "{}")
        vcs = direct.get("vcs_info", {})
        if not direct.get("dir_info", {}).get("editable") and vcs.get("vcs") == "git":
            revision = known_revision(vcs.get("commit_id"))
    except (PackageNotFoundError, OSError, ValueError, AttributeError, TypeError):
        pass
    return {"package_version": package, "git_revision": revision}
