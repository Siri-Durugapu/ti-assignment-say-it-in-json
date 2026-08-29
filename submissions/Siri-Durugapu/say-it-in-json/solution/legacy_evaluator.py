"""
Legacy .pfcfg semantic evaluator.

This is the "legacy side" of the two-evaluator verification design: it
resolves effective settings directly off the raw .pfcfg interpolation
strings, WITHOUT going through interpolation.py or the literal/env/ref/
concat node tree at all. It shares only the structural layer with the
JSON evaluator (parser.py + resolver.py's include/condition flattening);
${...}/$(...) scanning, environment lookup, recursion, and cycle tracking
are all reimplemented here from scratch. This independence is the point:
sharing interpolation.py would mean a bug in it could sit on both sides
of a "verification" and silently pass.

Locked behavior this implements:
- Condition survival: ifdef = var set and non-empty, ifndef = var unset
  or empty (see `layer_survives`).
- Effective value for a key = last surviving layer, depth-first order
  (see `_survivors`).
- Zero surviving layers on a key that's never referenced elsewhere is
  NOT an error: the key is simply absent from `effective`.
- Zero surviving layers on a key that IS referenced via $(...) is an
  evaluation-time failure, worded as "$(path) has no surviving layer
  for this environment" — not "key not found" (that phrasing is
  reserved for a path that isn't in the flattened config at all).
- Circular references are detected per top-level key, with a fresh
  `visiting` chain per key (no memoization across keys) so that e.g.
  cascade.loop.a and cascade.loop.b each get their own failure entry
  with their own version of the cycle chain, rather than one shared
  root-cause entry.
- Bare ${VAR} with VAR unset resolves to "" and is never a failure.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import resolver as R

MAX_DEPTH = 100


# --------------------------------------------------------------------------
# Failure types (raised during recursive resolution, caught at the
# top-level per-key driver in `evaluate`)
# --------------------------------------------------------------------------

class EvalFailure(Exception):
    """Base class for the hard failures the unmigratable report cares about."""


class CircularReferenceError(EvalFailure):
    def __init__(self, chain: List[str]):
        self.chain = chain  # e.g. ["cascade.loop.a", "cascade.loop.b", "cascade.loop.a"]
        super().__init__(" → ".join(chain))


class MissingReferenceError(EvalFailure):
    def __init__(self, path: str):
        self.path = path  # key does not exist anywhere in the flattened config
        super().__init__(path)


class NoSurvivingLayerError(EvalFailure):
    def __init__(self, path: str):
        self.path = path  # key exists structurally, but nothing survives this env
        super().__init__(path)


class EvaluationTooDeepError(EvalFailure):
    def __init__(self, depth: int):
        self.depth = depth
        super().__init__(f"exceeded max reference depth ({depth})")


# --------------------------------------------------------------------------
# Condition survival — deliberately re-implemented here rather than
# imported, to stay unambiguously independent of anything the JSON side
# touches. It's tiny and mechanical, so duplicating it costs nothing.
# --------------------------------------------------------------------------

def layer_survives(condition: Tuple[R.ConditionTerm, ...], env: Dict[str, str]) -> bool:
    for term in condition:
        val = env.get(term.var)
        is_set = val is not None and val != ""
        if term.kind == "ifdef" and not is_set:
            return False
        if term.kind == "ifndef" and is_set:
            return False
    return True


def _survivors(full_key: str, env: Dict[str, str], grouped: Dict[str, List[R.Assignment]]) -> List[R.Assignment]:
    assignments = grouped.get(full_key)
    if assignments is None:
        return []
    return [a for a in assignments if layer_survives(a.condition, env)]


# --------------------------------------------------------------------------
# Result shape
# --------------------------------------------------------------------------

@dataclass
class FailureEntry:
    file: str
    section: str
    key: str
    line: int
    reason: str


@dataclass
class EvaluationResult:
    effective: Dict[str, str] = field(default_factory=dict)
    failures: List[FailureEntry] = field(default_factory=list)


# --------------------------------------------------------------------------
# Core recursive resolution
# --------------------------------------------------------------------------

def _resolve_key(
    full_key: str,
    env: Dict[str, str],
    grouped: Dict[str, List[R.Assignment]],
    visiting: Tuple[str, ...],
    depth: int,
) -> str:
    """Resolve full_key to its effective string value, or raise an EvalFailure."""
    if depth > MAX_DEPTH:
        raise EvaluationTooDeepError(depth)
    if full_key in visiting:
        raise CircularReferenceError(list(visiting) + [full_key])
    if full_key not in grouped:
        raise MissingReferenceError(full_key)

    survivors = _survivors(full_key, env, grouped)
    if not survivors:
        raise NoSurvivingLayerError(full_key)

    winning = survivors[-1]
    return _resolve_raw(winning.raw_value, env, grouped, visiting + (full_key,), depth + 1)


def _resolve_raw(
    raw: str,
    env: Dict[str, str],
    grouped: Dict[str, List[R.Assignment]],
    visiting: Tuple[str, ...],
    depth: int,
) -> str:
    """Recursive-descent resolution of one raw .pfcfg value string. Mirrors
    interpolation.py's grammar (${VAR}, ${VAR:-d}, ${VAR:+a}, $(path), plain
    text) but resolves directly to a string instead of building a node tree,
    and is written independently of that module by design."""
    text, pos = _scan(raw, 0, env, grouped, visiting, depth, stop_at_brace=False)
    return text


def _scan(
    s: str,
    pos: int,
    env: Dict[str, str],
    grouped: Dict[str, List[R.Assignment]],
    visiting: Tuple[str, ...],
    depth: int,
    stop_at_brace: bool,
) -> Tuple[str, int]:
    n = len(s)
    out: List[str] = []
    while pos < n:
        c = s[pos]
        if stop_at_brace and c == "}":
            break
        if c == "$" and pos + 1 < n and s[pos + 1] == "{":
            piece, pos = _resolve_env_token(s, pos + 2, env, grouped, visiting, depth)
            out.append(piece)
            continue
        if c == "$" and pos + 1 < n and s[pos + 1] == "(":
            piece, pos = _resolve_ref_token(s, pos + 2, env, grouped, visiting, depth)
            out.append(piece)
            continue
        out.append(c)
        pos += 1
    return "".join(out), pos


def _resolve_env_token(
    s: str,
    pos: int,
    env: Dict[str, str],
    grouped: Dict[str, List[R.Assignment]],
    visiting: Tuple[str, ...],
    depth: int,
) -> Tuple[str, int]:
    """pos is positioned right after '${'."""
    n = len(s)
    start = pos
    while pos < n and s[pos] not in (":", "}"):
        pos += 1
    var = s[start:pos]

    if pos < n and s[pos] == "}":
        # bare ${VAR}: unset -> "", never a failure
        return env.get(var, ""), pos + 1

    # s[pos] == ':' -> ':-' (default) or ':+' (alt)
    op = s[pos + 1]
    pos += 2
    inner, pos = _scan(s, pos, env, grouped, visiting, depth, stop_at_brace=True)
    pos += 1  # consume closing '}'

    is_set = var in env and env[var] != ""
    if op == "-":
        return (env[var] if is_set else inner), pos
    else:  # op == "+"
        return (inner if is_set else ""), pos


def _resolve_ref_token(
    s: str,
    pos: int,
    env: Dict[str, str],
    grouped: Dict[str, List[R.Assignment]],
    visiting: Tuple[str, ...],
    depth: int,
) -> Tuple[str, int]:
    """pos is positioned right after '$('."""
    n = len(s)
    start = pos
    while pos < n and s[pos] != ")":
        pos += 1
    path = s[start:pos]
    value = _resolve_key(path, env, grouped, visiting, depth)
    return value, pos + 1


# --------------------------------------------------------------------------
# Top-level driver
# --------------------------------------------------------------------------

def _reason_for(exc: EvalFailure) -> str:
    if isinstance(exc, CircularReferenceError):
        immediate = exc.chain[1] if len(exc.chain) > 1 else exc.chain[0]
        return (
            f"Unresolved $({immediate}) — circular reference: "
            + " → ".join(exc.chain)
        )
    if isinstance(exc, MissingReferenceError):
        return f"Unresolved $({exc.path}) — key not found"
    if isinstance(exc, NoSurvivingLayerError):
        return f"$({exc.path}) has no surviving layer for this environment"
    if isinstance(exc, EvaluationTooDeepError):
        return f"Evaluation exceeded maximum reference depth ({exc.depth}) — likely runaway recursion"
    return str(exc)


def evaluate(grouped: Dict[str, List[R.Assignment]], env: Dict[str, str]) -> EvaluationResult:
    """Evaluate every key in `grouped` independently against `env`.

    grouped is environment-independent (parser.py + resolver.py's include/
    condition flattening already ran) — this function is the only part
    that's env-specific, so the same `grouped` can be reused across
    multiple environment fixtures without re-parsing or re-resolving
    includes.
    """
    result = EvaluationResult()
    for full_key in grouped:
        survivors = _survivors(full_key, env, grouped)
        if not survivors:
            # Zero surviving layers on a key nobody's forcing us to resolve
            # right now is not an error — just absent from effective settings.
            continue
        provenance = survivors[-1]  # for the report, if resolution fails below
        try:
            value = _resolve_key(full_key, env, grouped, tuple(), 0)
        except EvalFailure as exc:
            result.failures.append(
                FailureEntry(
                    file=provenance.source_file,
                    section=provenance.section,
                    key=provenance.key,
                    line=provenance.source_line,
                    reason=_reason_for(exc),
                )
            )
        else:
            result.effective[full_key] = value
    return result


def evaluate_entry(entry_path: str, env: Dict[str, str]) -> EvaluationResult:
    """Convenience one-shot: resolve includes/conditions for entry_path, then
    evaluate against env. For repeated evaluation against multiple fixtures,
    call resolver.resolve_entry + resolver.group_by_key once yourself and
    reuse that `grouped` across multiple evaluate(grouped, env) calls instead."""
    stream = R.resolve_entry(entry_path)
    grouped = R.group_by_key(stream)
    return evaluate(grouped, env)
