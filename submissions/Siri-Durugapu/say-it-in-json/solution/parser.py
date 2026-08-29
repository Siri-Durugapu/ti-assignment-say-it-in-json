"""
.pfcfg parser: turns raw text into an ordered AST.

Locked decisions this implements:
- Comment = line whose first non-whitespace char is '#' or ';'. No mid-line
  comment stripping (channel = #acme-builds must stay intact).
- Sections: [name] or [dotted.path]; keys belong to the most recently seen
  section header at the current nesting.
- Quoted values: double-quoted, with \" and \\ as the only escapes.
  Unquoted values are trimmed of leading/trailing whitespace.
- @include / @include_once: directives, path relative to the containing
  file's directory. Must appear before any section header in that file
  (we don't enforce this here; the resolver/converter can validate it).
- @ifdef VAR / @ifndef VAR ... @endif: can wrap section headers and/or
  key lines and/or includes; nesting supported.
- This stage does nothing beyond structure extraction: no include
  resolution, no condition evaluation, no interpolation parsing.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Union


@dataclass
class SectionNode:
    name: str
    line: int


@dataclass
class KeyValueNode:
    key: str
    raw_value: str
    line: int
    section: Optional[SectionNode] = None  # most recent section at this point


@dataclass
class IncludeNode:
    path: str
    once: bool
    line: int


@dataclass
class ConditionalNode:
    kind: str  # "ifdef" | "ifndef"
    var: str
    line: int
    body: List["Node"] = field(default_factory=list)


Node = Union[SectionNode, KeyValueNode, IncludeNode, ConditionalNode]


class ParseError(Exception):
    pass


def _is_comment_or_blank(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    return stripped[0] in ("#", ";")


def _split_quoted(value: str) -> str:
    """Unquote a double-quoted value, honoring \\" and \\\\ escapes."""
    out = []
    i = 1  # skip opening quote
    n = len(value)
    while i < n:
        c = value[i]
        if c == "\\" and i + 1 < n and value[i + 1] in ('"', "\\"):
            out.append(value[i + 1])
            i += 2
            continue
        if c == '"':
            # closing quote
            return "".join(out)
        out.append(c)
        i += 1
    raise ParseError(f"Unterminated quoted value: {value!r}")


def parse_value(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == '"':
        return _split_quoted(raw)
    return raw


def parse(text: str, filename: str = "<string>") -> List[Node]:
    lines = text.splitlines()
    # stack of (ConditionalNode, target_list) — target_list is where new
    # nodes at this nesting level get appended
    root: List[Node] = []
    stack: List[ConditionalNode] = []
    current_section: Optional[SectionNode] = None
    seen_section_header = False

    def current_target() -> List[Node]:
        return stack[-1].body if stack else root

    for idx, raw_line in enumerate(lines, start=1):
        if _is_comment_or_blank(raw_line):
            continue
        stripped = raw_line.strip()

        if stripped.startswith("@endif"):
            if not stack:
                raise ParseError(f"{filename}:{idx}: @endif with no matching @ifdef/@ifndef")
            stack.pop()
            continue

        if stripped.startswith("@ifdef ") or stripped.startswith("@ifndef "):
            kind = "ifdef" if stripped.startswith("@ifdef ") else "ifndef"
            var = stripped.split(None, 1)[1].strip()
            node = ConditionalNode(kind=kind, var=var, line=idx)
            current_target().append(node)
            stack.append(node)
            continue

        if stripped.startswith("@include_once "):
            path = stripped[len("@include_once "):].strip()
            if seen_section_header:
                raise ParseError(
                    f"{filename}:{idx}: @include_once appears after a section header"
                )
            current_target().append(IncludeNode(path=path, once=True, line=idx))
            continue

        if stripped.startswith("@include "):
            path = stripped[len("@include "):].strip()
            if seen_section_header:
                raise ParseError(
                    f"{filename}:{idx}: @include appears after a section header"
                )
            current_target().append(IncludeNode(path=path, once=False, line=idx))
            continue

        if stripped.startswith("[") and stripped.endswith("]"):
            name = stripped[1:-1].strip()
            current_section = SectionNode(name=name, line=idx)
            seen_section_header = True
            current_target().append(current_section)
            continue

        if "=" in stripped:
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = parse_value(value)
            if current_section is None:
                raise ParseError(f"{filename}:{idx}: key '{key}' outside any [section]")
            current_target().append(
                KeyValueNode(key=key, raw_value=value, line=idx, section=current_section)
            )
            continue

        raise ParseError(f"{filename}:{idx}: unrecognized line: {raw_line!r}")

    if stack:
        raise ParseError(f"{filename}: unterminated @ifdef/@ifndef (missing @endif)")

    return root


def parse_file(path: str) -> List[Node]:
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return parse(text, filename=path)
