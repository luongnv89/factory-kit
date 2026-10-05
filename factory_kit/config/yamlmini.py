#!/usr/bin/env python3
"""Restricted-YAML parser for ``.factory-kit.yml`` and ``.gitissue.yml``.

The kit is standard-library only (spike Q6 decision: helpers follow the
``gi-*.py`` convention — no third-party YAML dependency), so the manifest
schema deliberately lives inside a restricted YAML subset:

- nested string-keyed mappings, indentation-based (2 spaces recommended)
- block lists of scalars (``- item``) — never lists of mappings
- inline lists (``key: [a, b]``)
- scalars: double/single-quoted strings, ``true``/``false``/``null``,
  integers (including negative Telegram chat IDs), floats, bare strings
- ``#`` full-line and trailing comments

Anything outside the subset — anchors, multiline scalars, flow mappings,
lists of mappings, tabs — raises :class:`ParseError` with a line number
rather than silently producing a wrong value. The manifest schema in
:mod:`factory_kit.config.schema` is designed so nothing it needs sits
outside this subset.
"""

from __future__ import annotations

import json
import re

__all__ = ["ParseError", "load", "load_file", "flatten"]


class ParseError(Exception):
    """The restricted-YAML parser met syntax it does not support."""


_MAPPING_RE = re.compile(r"^(?P<indent> *)(?P<key>[^\s:#][^:#]*?)(?P<ws> *)"
                         r":(?P<value>.*)$")
_LIST_RE = re.compile(r"^(?P<indent> *)-(?P<value> +.*| *)$")
_KEY_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
_INT_RE = re.compile(r"-?[0-9]+")
_FLOAT_RE = re.compile(r"-?[0-9]+\.[0-9]+")


def _strip_comment(line: str) -> str:
    """Remove a trailing `` # comment`` outside quotes."""
    quote = None
    for i, char in enumerate(line):
        if quote is None:
            if char in "\"'":
                quote = char
            elif char == "#" and (i == 0 or line[i - 1] == " "):
                return line[:i].rstrip()
        elif char == quote:
            quote = None
    return line.rstrip()


def _scalar(value: str, line_no: int):
    value = value.strip()
    if value == "":
        return None
    if value.startswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ParseError(
                f"line {line_no}: invalid quoted scalar {value!r} "
                f"({exc.msg})") from exc
    if value.startswith("'"):
        if not value.endswith("'") or len(value) < 2:
            raise ParseError(f"line {line_no}: unterminated scalar {value!r}")
        return value[1:-1].replace("''", "'")
    if value == "true":
        return True
    if value == "false":
        return False
    if value in ("null", "~"):
        return None
    if _INT_RE.fullmatch(value):
        return int(value)
    if _FLOAT_RE.fullmatch(value):
        return float(value)
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_scalar(part, line_no) for part in _split_inline(inner)]
    if value and not any(c in value for c in "{}[],\t") and ": " not in value \
            and not value.endswith(":"):
        if value[0] in "&*|>%@`!?":
            raise ParseError(
                f"line {line_no}: unsupported YAML feature {value!r} — "
                "anchors, aliases, tags and block scalars are outside "
                "the subset; use a quoted string")
        return value
    raise ParseError(f"line {line_no}: unsupported scalar {value!r}")


def _split_inline(inner: str):
    """Split ``a, "b,c", d`` honouring quotes."""
    parts, buf, quote = [], [], None
    for char in inner:
        if quote is None and char in "\"'":
            quote = char
        elif char == quote:
            quote = None
        if char == "," and quote is None:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(char)
    parts.append("".join(buf))
    return [p.strip() for p in parts]


def _logical_lines(text: str):
    """Yield ``(indent, content, line_no)`` for meaningful lines."""
    for line_no, raw in enumerate(text.splitlines(), start=1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ParseError(f"line {line_no}: tab indentation is unsupported")
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        yield indent, stripped.strip(), line_no


def load(text: str):
    """Parse restricted-YAML ``text`` into nested dicts/lists.

    Raises :class:`ParseError` on anything outside the subset. A document
    whose top level is not a mapping is rejected — manifests are always
    mappings.
    """
    lines = list(_logical_lines(text))
    if not lines:
        return {}
    node, index = _block(lines, 0, lines[0][0])
    if index != len(lines):
        _, _, line_no = lines[index]
        raise ParseError(f"line {line_no}: unexpected indentation")
    if not isinstance(node, dict):
        raise ParseError("document root must be a mapping")
    return node


def load_file(path):
    """Parse a restricted-YAML file; missing files raise ``ParseError``."""
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as exc:
        raise ParseError(f"cannot read {path}: {exc}") from exc
    try:
        return load(text)
    except ParseError as exc:
        raise ParseError(f"{path}: {exc}") from exc


def _block(lines, index, indent):
    """Parse one map or list block at ``indent`` starting at ``index``."""
    is_list = _is_list_item(lines[index])
    container = [] if is_list else {}
    while index < len(lines):
        line_indent, content, line_no = lines[index]
        if line_indent < indent:
            break
        if line_indent > indent:
            raise ParseError(
                f"line {line_no}: unexpected indentation "
                f"(expected {indent} spaces)")
        if is_list:
            match = _LIST_RE.match(" " * line_indent + content)
            if not match:
                raise ParseError(
                    f"line {line_no}: expected a '- item' list entry")
            value = match.group("value").strip()
            if _LIST_MAP_RE.match(value):
                raise ParseError(
                    f"line {line_no}: lists of mappings are unsupported — "
                    "use nested keys")
            container.append(_scalar(value, line_no))
            index += 1
            continue
        match = _MAPPING_RE.match(" " * line_indent + content)
        if not match:
            raise ParseError(f"line {line_no}: expected 'key: value'")
        key = match.group("key").strip()
        if not _KEY_RE.match(key):
            raise ParseError(f"line {line_no}: unsupported key {key!r}")
        value = match.group("value").strip()
        index += 1
        if value:
            container[key] = _scalar(value, line_no)
            continue
        # `key:` alone — a nested block or an empty value, decided by the
        # next line's indent.
        if index < len(lines) and lines[index][0] > line_indent:
            child_indent = lines[index][0]
            child, index = _block(lines, index, child_indent)
            container[key] = child
        else:
            container[key] = None
    return container, index


def _is_list_item(line_tuple) -> bool:
    indent, content, _ = line_tuple
    return _LIST_RE.match(" " * indent + content) is not None


# An *unquoted* `key:` opener inside a `- ` entry means a list of mappings,
# which the subset does not support. Quoted scalars like `"npm run x:y"`
# start with a quote and never match.
_LIST_MAP_RE = re.compile(r"^[A-Za-z0-9_.\-]+ *:")


def flatten(data: dict, prefix: str = "") -> dict:
    """Flatten ``{'a': {'b': 1}}`` into ``{'a.b': 1}`` (list leaves kept)."""
    flat = {}
    for key, value in (data or {}).items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(flatten(value, path + "."))
        else:
            flat[path] = value
    return flat
