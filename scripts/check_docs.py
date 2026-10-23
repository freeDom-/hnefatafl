#!/usr/bin/env python3
"""Check repository Markdown destinations, GitHub-style anchors and navigation.

No network access or third-party packages. Fenced examples are deliberately
excluded. Remote URLs and application routes in code spans are not validated.
"""

from __future__ import annotations

import html
from pathlib import Path
import re
import subprocess
import sys
import unicodedata
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
INLINE = re.compile(
    r"!?\[[^\]]*\]\(\s*(<[^>]+>|(?:[^()\s\\]|\\.|\([^()]*\))+)"
    r'(?:\s+["\'][^\n]*["\'])?\s*\)',
    re.MULTILINE,
)
REFERENCE = re.compile(r"^ {0,3}\[([^\]]+)\]:\s*<?([^\s>]+)>?", re.MULTILINE)
USE_REFERENCE = re.compile(r"!?\[([^\]]+)\]\[([^\]]*)\]")


def prose(text: str) -> str:
    """Blank fenced blocks while preserving offsets for error line numbers."""
    output = []
    fence = None
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\n"))
        if fence:
            if (
                marker
                and marker[1][0] == fence[0]
                and len(marker[1]) >= len(fence)
                and not marker[2].strip()
            ):
                fence = None
            output.append("\n" if line.endswith("\n") else "")
        elif marker:
            fence = marker[1]
            output.append("\n" if line.endswith("\n") else "")
        else:
            output.append(line)
    if fence:
        raise ValueError("unclosed fenced code block")
    return "".join(output)


def anchors(text: str) -> set[str]:
    result = set()
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        heading = match[1] if match else None
        if (
            heading is None
            and index + 1 < len(lines)
            and line.strip()
            and re.fullmatch(r" {0,3}(?:=+|-+)\s*", lines[index + 1])
        ):
            heading = line.strip()
        if heading is None:
            continue
        heading = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", heading)
        heading = html.unescape(re.sub(r"<[^>]+>", "", heading)).lower()
        heading = "".join(c for c in heading if c in " _-" or unicodedata.category(c)[0] in "LNM")
        base = heading.replace(" ", "-")
        slug, suffix = base, 0
        while slug in result:
            suffix += 1
            slug = f"{base}-{suffix}"
        result.add(slug)
    result.update(re.findall(r'\b(?:id|name)=["\']([^"\']+)["\']', text))
    return result


def links(text: str):
    for match in INLINE.finditer(text):
        yield text.count("\n", 0, match.start()) + 1, match[1].strip("<>")
    references = {m[1].casefold(): m[2] for m in REFERENCE.finditer(text)}
    for match in USE_REFERENCE.finditer(text):
        label = (match[2] or match[1]).casefold()
        if label not in references:
            raise ValueError(f"undefined reference link: {label}")
    for match in REFERENCE.finditer(text):
        yield text.count("\n", 0, match.start()) + 1, match[2]
    for match in re.finditer(r'\b(?:href|src)=["\']([^"\']+)["\']', text):
        yield text.count("\n", 0, match.start()) + 1, match[1]


def main() -> int:
    names = (
        subprocess.check_output(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.md"],
            cwd=ROOT,
        )
        .decode()
        .split("\0")
    )
    documents = sorted({ROOT / name for name in names if name and (ROOT / name).is_file()})
    contents, errors = {}, []
    for path in documents:
        try:
            contents[path] = prose(path.read_text(encoding="utf-8"))
        except ValueError as error:
            errors.append(f"{path.relative_to(ROOT)}: {error}")
    headings = {path: anchors(text) for path, text in contents.items()}
    graph = {path: set() for path in contents}
    checked = 0
    for path, text in contents.items():
        try:
            for line, target in links(text):
                url = urlsplit(html.unescape(target))
                if url.scheme or url.netloc:
                    continue
                destination = (
                    (
                        ROOT / unquote(url.path).lstrip("/")
                        if url.path.startswith("/")
                        else path.parent / unquote(url.path)
                    ).resolve()
                    if url.path
                    else path
                )
                checked += 1
                prefix = f"{path.relative_to(ROOT)}:{line}: {target}"
                if not destination.exists():
                    errors.append(f"{prefix}: missing destination")
                    continue
                if destination.is_dir() and (destination / "README.md").is_file():
                    destination /= "README.md"
                if destination in graph:
                    graph[path].add(destination)
                if (
                    url.fragment
                    and destination.suffix == ".md"
                    and unquote(url.fragment) not in headings.get(destination, set())
                ):
                    errors.append(f"{prefix}: missing heading anchor")
        except ValueError as error:
            errors.append(f"{path.relative_to(ROOT)}: {error}")
    reached, pending = set(), [ROOT / "docs/README.md"]
    while pending:
        path = pending.pop()
        if path not in reached:
            reached.add(path)
            pending.extend(graph.get(path, set()) - reached)
    for path in documents:
        if path not in reached:
            errors.append(f"{path.relative_to(ROOT)}: unreachable from docs/README.md")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(
        f"Documentation OK: {len(documents)} Markdown files, {checked} local links/images/anchors; all reachable from docs/README.md."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
