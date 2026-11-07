"""Bounded, stateful sanitization of archive diagnostic text, not a DLP filter."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import TextIO

BUFFER_CHARS = 65536
_POSSIBLE = re.compile(r"password|token|secret|credential|api[_-]?key|authorization|bearer", re.I)
_KEY = re.compile(r"password|token|secret|credential|api[_-]?key|authorization", re.I)
_AUTHORIZATION = re.compile(r"authorization", re.I)
_WORD = re.compile(r"[\w-]+")
_TRAILING_WORD = re.compile(r"(?<![\w-])[\w-]+\Z")
_HORIZONTAL = re.compile(r"[^\S\r\n]+")
_WHITESPACE = re.compile(r"\s+")
_HEAD = re.compile(r"""(?<![\w-])(?P<key>[\w-]+)[\\"']*\s*[:=]""")
_BEARER = re.compile(r"(?<![\w-])bearer(?=[^\S\r\n])", re.I)
_SLASHES = re.compile(r"\\+")
_LINE_END = re.compile(r"[\r\n]")
_VALUE_END = re.compile(r"""[\s,"'}\];&]""")
_QUOTES = re.compile(r"""\\+|["']""")
_CONTAINER = re.compile(r"""\\+|["'{}\[\]]""")
_PATH = re.compile(r"""(?<![\w:])(?:[A-Za-z]:[\\/]|/)[^\s"'<>]*""")
_PATH_BODY = re.compile(r"""[^\s"'<>]*""")


class RedactionLimitError(ValueError):
    """Sanitization must not expand an artifact beyond the existing byte limit."""


class _Credentials:
    def __init__(self):
        self.mode = "scan"
        self.tail = ""
        self.prefix = ""
        self.length = 0
        self.sensitive = self.authorization = False
        self.quoted_key = self.json_value = False
        self.quote = ""
        self.level = self.slashes = 0
        self.keep_quotes = False
        self.auth_token = ""
        self.container: list[str] = []

    def word(self, value: str, *, reset: bool = False, checked: bool = False) -> None:
        if reset:
            self.tail = self.prefix = ""
            self.length = 0
            self.sensitive = self.authorization = False
            self.quoted_key = self.json_value = False
        combined = self.tail + value
        if not checked:
            self.sensitive = self.sensitive or bool(_KEY.search(combined))
            self.authorization = self.authorization or bool(_AUTHORIZATION.search(combined))
        self.tail = combined[-13:]
        self.prefix = (self.prefix + value[:6])[:6]
        self.length = min(7, self.length + len(value))

    def feed(self, text: str) -> str:
        # Ordinary logs take two linear C-regex scans, rather than one Python
        # operation per character/word. Only a bounded word suffix crosses reads.
        if (
            self.mode in {"scan", "word", "after_key"}
            and not self.sensitive
            and not (self.length == 6 and self.prefix.lower() == "bearer")
            and not _POSSIBLE.search((self.tail if self.mode == "word" else "") + text)
        ):
            # Only the longest recognized keyword can span a read boundary;
            # a long trailing word need not be scanned/copied a second time.
            trailing = _TRAILING_WORD.search(text[-14:])
            if trailing:
                self.word(
                    trailing[0],
                    reset=len(text) > 14 or trailing.start() != 0 or self.mode != "word",
                    checked=True,
                )
                self.mode = "word"
            else:
                self.mode = "scan"
                self.word("", reset=True)
            return text

        output: list[str] = []
        position = 0
        while position < len(text):
            char = text[position]
            if self.mode == "scan":
                match = _WORD.search(text, position)
                if match is None:
                    output.append(text[position:])
                    break
                output.append(text[position : match.start()])
                self.word(match[0], reset=True)
                output.append(match[0])
                position = match.end()
                self.mode = "word"
            elif self.mode == "word":
                match = _WORD.match(text, position)
                if match:
                    self.word(match[0])
                    output.append(match[0])
                    position = match.end()
                else:
                    self.mode = "after_key"
            elif self.mode == "after_key":
                space = (_WHITESPACE if self.quoted_key else _HORIZONTAL).match(text, position)
                if space:
                    output.append(space[0])
                    position = space.end()
                    if not self.quoted_key and self.length == 6 and self.prefix.lower() == "bearer":
                        self.mode = "value"
                        self.authorization = False
                elif char in "\"'\\":
                    self.quoted_key |= char in "\"'"
                    output.append(char)
                    position += 1
                elif char in ":=":
                    self.json_value = self.quoted_key and char == ":"
                    output.append(char)
                    position += 1
                    self.mode = "value" if self.sensitive else "scan"
                else:
                    self.mode = "scan"
                    self.word("", reset=True)
            elif self.mode in {"value", "auth_next"}:
                space = (_WHITESPACE if self.json_value else _HORIZONTAL).match(text, position)
                if space:
                    if self.mode == "value":
                        output.append(space[0])
                    position = space.end()
                    continue
                if char in "\r\n,};]&":
                    if self.mode == "value":
                        output.append("[redacted]")
                    self.mode = "scan"
                    self.word("", reset=True)
                    continue
                keep = self.mode == "value"
                if char in "\"'":
                    output.append((char if keep else "") + ("[redacted]" if keep else ""))
                    self.quote, self.level, self.slashes = char, 0, 0
                    self.keep_quotes = keep
                    self.mode = "quoted"
                    position += 1
                else:
                    if keep:
                        output.append("[redacted]")
                    self.slashes = 0
                    if char == "\\":
                        self.mode = "prefix"
                    elif char in "{[":
                        self.container = [char]
                        self.quote = ""
                        self.mode = "container"
                        position += 1
                    elif self.authorization and keep:
                        self.auth_token = ""
                        self.mode = "auth_scheme"
                    else:
                        self.mode = "unquoted"
            elif self.mode == "prefix":
                match = _SLASHES.match(text, position)
                if match:
                    self.slashes += len(match[0])
                    position += len(match[0])
                elif char in "\"'":
                    # A serialized diagnostic may use \" as its delimiter and
                    # \\\" for a quote inside it. Count, never retain, slashes.
                    self.quote, self.level = char, self.slashes
                    self.slashes, self.keep_quotes = 0, False
                    self.mode = "quoted"
                    position += 1
                else:
                    if _VALUE_END.match(text, position) and self.slashes % 2:
                        position += 1
                    self.slashes = 0
                    self.mode = "unquoted"
            elif self.mode in {"unquoted", "auth_scheme"}:
                match = _VALUE_END.search(text, position)
                end = match.start() if match else len(text)
                if self.mode == "auth_scheme":
                    self.auth_token += text[
                        position : min(end, position + 17 - len(self.auth_token))
                    ]
                else:
                    # Shell-style escaped whitespace/quotes still belong to the
                    # unquoted value. Count only the contiguous trailing slashes.
                    slash_start = end
                    while slash_start > position and text[slash_start - 1] == "\\":
                        slash_start -= 1
                    self.slashes = (
                        (self.slashes if slash_start == position else 0) + end - slash_start
                    )
                position = end
                if match:
                    if self.mode == "auth_scheme":
                        self.mode = (
                            "auth_next"
                            if self.auth_token.lower() in {"bearer", "basic", "token", "negotiate"}
                            else "auth_line"
                        )
                    else:
                        if self.slashes % 2:
                            position = match.end()
                            self.slashes = 0
                            continue
                        self.mode = "scan"
                        self.word("", reset=True)
            elif self.mode == "auth_line":
                match = _LINE_END.search(text, position)
                if match is None:
                    break
                position = match.start()
                self.mode = "scan"
                self.word("", reset=True)
            elif self.mode in {"quoted", "container"}:
                pattern = _QUOTES if self.mode == "quoted" or self.quote else _CONTAINER
                match = pattern.search(text, position)
                if match is None:
                    self.slashes = 0
                    break
                if match.start() > position:
                    self.slashes = 0
                token = match[0]
                position = match.end()
                if token[0] == "\\":
                    self.slashes = (
                        (self.slashes + len(token)) % (2 * (self.level + 1))
                        if self.quote
                        else self.slashes + len(token)
                    )
                elif self.quote:
                    if token == self.quote and self.slashes == self.level:
                        self.quote = ""
                        if self.mode == "quoted":
                            if self.keep_quotes:
                                output.append(token)
                            self.mode = "scan"
                            self.word("", reset=True)
                    self.slashes = 0
                elif token in "\"'":
                    self.quote, self.level = token, self.slashes
                    self.slashes = 0
                elif token in "{[":
                    if len(self.container) == 32:
                        self.mode = "discard"
                    else:
                        self.container.append(token)
                elif token in "}]":
                    if not self.container or (self.container[-1], token) not in {
                        ("{", "}"),
                        ("[", "]"),
                    }:
                        self.mode = "discard"
                    else:
                        self.container.pop()
                        if not self.container:
                            self.mode = "scan"
                            self.word("", reset=True)
            else:  # Malformed/deep credential containers fail closed through EOF.
                break
        return "".join(output)

    def finish(self) -> str:
        return "[redacted]" if self.mode == "value" else ""


class _Paths:
    def __init__(self):
        self.tail = self.before = ""
        self.dropping = False

    def feed(self, text: str, *, final: bool = False) -> str:
        if self.dropping:
            match = _PATH_BODY.match(text)
            assert match is not None
            length = match.end()
            if length:
                self.before = text[length - 1]
            text = text[length:]
            if not text:
                return ""
            self.dropping = False
        text = self.tail + text
        self.tail = ""
        view = self.before + text
        offset = len(self.before)
        limit = len(text) if final else max(0, len(text) - 2)
        output: list[str] = []
        position = 0
        for match in _PATH.finditer(view, offset):
            start, end = match.start() - offset, match.end() - offset
            if start >= limit:
                break
            output.extend((text[position:start], "[local path]"))
            position = end
            if end == len(text) and not final:
                self.dropping = True
                break
        end = max(position, limit)
        output.append(text[position:end])
        self.tail = text[end:]
        if end:
            self.before = text[end - 1]
        return "".join(output)


def redacted_chunks(chunks: Iterable[str]) -> Iterator[str]:
    credentials, paths = _Credentials(), _Paths()
    for chunk in chunks:
        yield paths.feed(credentials.feed(chunk))
    yield paths.feed(credentials.finish(), final=True)


def clean_text(value: str) -> str:
    """Sanitize recognized credentials and local paths in one diagnostic string."""
    if not _needs_redaction(value):
        return _PATH.sub("[local path]", value)
    return "".join(
        redacted_chunks(value[i : i + BUFFER_CHARS] for i in range(0, len(value), BUFFER_CHARS))
    )


def clean_reference(value: str) -> str:
    """Keep already validated job-relative references, without a credential bypass."""
    if not _needs_redaction(value):
        return value
    credentials = _Credentials()
    result = [
        credentials.feed(value[i : i + BUFFER_CHARS]) for i in range(0, len(value), BUFFER_CHARS)
    ]
    result.append(credentials.finish())
    return "".join(result)


def _needs_redaction(value: str) -> bool:
    if not _POSSIBLE.search(value):
        return False
    # Heads have disjoint word/quote/whitespace spans and a word-boundary guard.
    # No complete diagnostic-sized string copy is needed for this preflight.
    return bool(_BEARER.search(value)) or any(
        _KEY.search(value, match.start("key"), match.end("key")) for match in _HEAD.finditer(value)
    )


def redact_log(source: TextIO, destination: TextIO, *, maximum: int) -> None:
    """Stream bounded reads, retaining no complete line, key, path or secret value."""
    written = 0
    for chunk in redacted_chunks(iter(lambda: source.read(BUFFER_CHARS), "")):
        written += len(chunk.encode("utf-8"))
        if written > maximum:
            raise RedactionLimitError("Sanitized job log exceeds the file size limit.")
        destination.write(chunk)
