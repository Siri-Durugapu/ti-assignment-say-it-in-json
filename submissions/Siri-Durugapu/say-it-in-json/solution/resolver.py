"""
Recursive include/condition resolver.

Locked decisions this implements:
- Depth-first flatten of the include tree.
- Plain @include is NEVER deduplicated — every occurrence re-walks its
  target. Only @include_once consults/populates the "seen once" state.
- The "seen once" state is threaded sequentially: includes that occur
  one after another (whether in the same file, or because an @include
  pulled in a file that itself does further @include_once calls) share
  state, so a later @include_once of an already-once-included path is
  correctly skipped.
- The "seen once" state FORKS at each @ifdef/@ifndef body: a
  conditional body is resolved against a copy of the state as it stood
  at entry, and whatever that body adds to the copy is discarded once
  the body finishes — it never leaks to sibling nodes after @endif.
  This is what keeps mutually exclusive branches (@ifdef VAR / @ifndef
  VAR) from contaminating one another: each gets its own view, so an
  @include_once'd file that appears in both branches is included once
  per branch, not once total.
- Condition propagation into recursion: if an @include sits inside an
  @ifdef/@ifndef, every layer contributed by the included file is tagged
  with that condition, ANDed with whatever condition is already active
  at the include site (nested conditionals stack).
- Output: a flat, ordered stream of Assignment records — this is the
  "flattened (section.key, value, condition) stream" from the trace,
  one record per KeyValueNode encountered, in depth-first order.
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import List, Dict, Tuple

import parser as P


@dataclass
class ConditionTerm:
    kind: str  # "ifdef" | "ifndef"
    var: str

    def to_dict(self):
        return {"type": self.kind, "var": self.var}


@dataclass
class Assignment:
    section: str
    key: str
    raw_value: str
    condition: Tuple[ConditionTerm, ...]
    source_file: str
    source_line: int

    @property
    def full_key(self) -> str:
        return f"{self.section}.{self.key}"


class ResolveError(Exception):
    pass


def resolve_entry(entry_path: str) -> List[Assignment]:
    entry_path = os.path.abspath(entry_path)
    stream: List[Assignment] = []

    # seen_once is threaded functionally (passed in, returned out) rather
    # than mutated on a single shared object, so that forking it at a
    # conditional boundary is just "pass a copy in, don't use what comes
    # back out" — no separate fork/merge bookkeeping needed.

    def walk(
        path: str,
        inherited_condition: Tuple[ConditionTerm, ...],
        seen_once: frozenset,
    ) -> frozenset:
        ast = P.parse_file(path)
        base_dir = os.path.dirname(path)
        return _walk_nodes(ast, path, base_dir, inherited_condition, seen_once)

    def _walk_nodes(
        nodes,
        path: str,
        base_dir: str,
        cond: Tuple[ConditionTerm, ...],
        seen_once: frozenset,
    ) -> frozenset:
        for node in nodes:
            if isinstance(node, P.IncludeNode):
                target = os.path.normpath(os.path.join(base_dir, node.path))
                # Only @include_once ever SKIPS based on prior inclusion —
                # a plain @include always re-walks its target regardless of
                # whether that path has been seen before (this is what lets
                # two mutually-exclusive branches each independently
                # @include_once the same target without contaminating one
                # another, since each branch's fork starts clean).
                if node.once and target in seen_once:
                    continue
                # But EVERY include — plain or once — POPULATES seen_once.
                # This is standard include-guard semantics: an @include_once
                # elsewhere still needs to recognize "this path was already
                # spliced in," even if that first inclusion happened via a
                # plain @include. Without this, an unconditional plain
                # @include of a path followed later (in the same reachable
                # scope) by an @include_once of the identical path would
                # incorrectly re-splice that file's content a second time —
                # exactly what happens with _base/defaults.pfcfg in
                # customers/globex/pipeline.pfcfg (plain @include at the top)
                # vs. customers/globex/overrides.pfcfg (@include_once of the
                # same path, reached only when @ifndef PRODUCTION is active).
                seen_once = seen_once | {target}
                seen_once = walk(target, cond, seen_once)
            elif isinstance(node, P.KeyValueNode):
                stream.append(
                    Assignment(
                        section=node.section.name,
                        key=node.key,
                        raw_value=node.raw_value,
                        condition=cond,
                        source_file=path,
                        source_line=node.line,
                    )
                )
            elif isinstance(node, P.ConditionalNode):
                term = ConditionTerm(kind=node.kind, var=node.var)
                # Fork: the body resolves against a copy of seen_once as it
                # stands right now. We deliberately ignore what the body's
                # walk returns — it must not affect anything after @endif,
                # and in particular must not affect a mutually exclusive
                # sibling branch (@ifndef of the same var) processed next.
                _walk_nodes(node.body, path, base_dir, cond + (term,), seen_once)
            elif isinstance(node, P.SectionNode):
                pass  # section headers themselves don't produce stream entries
            else:
                raise ResolveError(f"Unknown node type: {node!r}")
        return seen_once

    walk(entry_path, tuple(), frozenset({entry_path}))
    return stream


def group_by_key(stream: List[Assignment]) -> Dict[str, List[Assignment]]:
    """Group the flattened stream into layers-per-key, preserving order."""
    grouped: Dict[str, List[Assignment]] = {}
    for a in stream:
        grouped.setdefault(a.full_key, []).append(a)
    return grouped