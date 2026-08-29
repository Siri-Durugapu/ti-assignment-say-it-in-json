"""
Parses a single raw_value string (already extracted by parser.py, already
unquoted) into the locked node union: literal | env | ref | concat.

Grammar, per format-reference.md:
    ${VAR}            -> env, bare
    ${VAR:-default}   -> env, default (recurses into this same grammar)
    ${VAR:+alternate} -> env, alt (recurses into this same grammar)
    $(section.key)    -> ref, path is the raw dotted string, byte-for-byte
    anything else     -> literal text

Multiple pieces in one string concatenate; concat.parts has >=2 entries;
a value that resolves to exactly one piece is that node directly, never
wrapped.

This is a genuine recursive-descent parser (not regex-with-a-closing-brace-
search) because defaults/alts can themselves contain nested ${...} and
$(...) — e.g. the real value
    ${ACME_RELEASE_TAG:-$(build.node_version)-${GIT_SHA:-dev}}
has a ${...} nested inside another ${...}'s default. A regex hunting for
the next "}" would close on the inner GIT_SHA's brace and mis-parse.
"""
from __future__ import annotations
from typing import List, Tuple


class InterpolationError(Exception):
    def __init__(self, message: str, raw_value: str, pos: int):
        self.raw_value = raw_value
        self.pos = pos
        super().__init__(f"{message} (in {raw_value!r} at offset {pos})")


def _literal(text: str) -> dict:
    return {"type": "literal", "text": text}


def _combine(parts: List[dict]) -> dict:
    if not parts:
        return _literal("")
    if len(parts) == 1:
        return parts[0]
    return {"type": "concat", "parts": parts}


def parse_value(raw: str) -> dict:
    """Entry point: parse a full raw_value string into a node."""
    parts, pos = _parse_parts(raw, 0, stop_at_brace=False)
    if pos != len(raw):
        # Only reachable if a stray unmatched '}' is left dangling, since
        # _parse_parts(stop_at_brace=False) otherwise always consumes to
        # the end of the string.
        raise InterpolationError("Unexpected '}' with no matching '${'", raw, pos)
    return _combine(parts)


def _parse_parts(s: str, pos: int, stop_at_brace: bool) -> Tuple[List[dict], int]:
    """Scan literal text / ${...} / $(...) chunks.

    If stop_at_brace, stops *before* consuming an unescaped '}' (the caller
    is inside a ${...} and that '}' closes it); otherwise runs to end of
    string.
    """
    parts: List[dict] = []
    buf: List[str] = []
    n = len(s)

    def flush():
        if buf:
            parts.append(_literal("".join(buf)))
            buf.clear()

    while pos < n:
        c = s[pos]
        if stop_at_brace and c == "}":
            break
        if c == "$" and pos + 1 < n and s[pos + 1] == "{":
            flush()
            node, pos = _parse_env(s, pos + 2)
            parts.append(node)
            continue
        if c == "$" and pos + 1 < n and s[pos + 1] == "(":
            flush()
            node, pos = _parse_ref(s, pos + 2)
            parts.append(node)
            continue
        buf.append(c)
        pos += 1

    flush()
    return parts, pos


def _parse_env(s: str, pos: int) -> Tuple[dict, int]:
    """pos is positioned right after '${'."""
    n = len(s)
    start = pos
    while pos < n and s[pos] not in (":", "}"):
        pos += 1
    if pos >= n:
        raise InterpolationError("Unterminated '${...}' (no closing '}')", s, start - 2)
    var = s[start:pos]
    if not var:
        raise InterpolationError("Empty variable name in '${}'", s, start)

    if s[pos] == "}":
        return {"type": "env", "var": var}, pos + 1

    # s[pos] == ':'
    if pos + 1 >= n or s[pos + 1] not in ("-", "+"):
        raise InterpolationError(
            "Expected ':-' or ':+' after variable name", s, pos
        )
    op = s[pos + 1]
    pos += 2

    inner_parts, pos = _parse_parts(s, pos, stop_at_brace=True)
    if pos >= n or s[pos] != "}":
        raise InterpolationError("Unterminated '${...}' (no closing '}')", s, start - 2)
    pos += 1  # consume the closing '}'

    node = {"type": "env", "var": var}
    if op == "-":
        node["default"] = _combine(inner_parts)
    else:
        node["alt"] = _combine(inner_parts)
    return node, pos


def _parse_ref(s: str, pos: int) -> Tuple[dict, int]:
    """pos is positioned right after '$('."""
    n = len(s)
    start = pos
    while pos < n and s[pos] != ")":
        pos += 1
    if pos >= n:
        raise InterpolationError("Unterminated '$(...)' (no closing ')')", s, start - 2)
    path = s[start:pos]
    if not path:
        raise InterpolationError("Empty reference path in '$()'", s, start)
    return {"type": "ref", "path": path}, pos + 1
