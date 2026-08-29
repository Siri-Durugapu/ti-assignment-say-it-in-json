"""
JSON-side semantic evaluator.

This is the "JSON side" of the two-evaluator verification design: it
resolves effective settings by walking the converted literal/env/ref/concat
node tree directly (doc["keys"][full_key]["layers"][i]["value"]),
WITHOUT going through legacy_evaluator.py's raw-string scanning at all.
Independence is the point, not a style preference: legacy_evaluator.py's
own docstring is explicit that sharing resolution code between the two
sides would let a bug sit on both of them and silently pass an
equivalence check. Nothing is imported from legacy_evaluator.py here.

What IS intentionally shared, as specification agreements rather than
algorithm:
  - MAX_DEPTH = 100 (must be kept in lockstep by hand -- if this drifts
    between the two files, a legitimately long reference chain could pass
    on one side and fail on the other).
  - The FailureEntry / EvaluationResult shapes.
  - The failure "reason" wording, so a report built from either evaluator
    reads the same way for the same failure class.

Locked behavior this implements (mirrors legacy_evaluator.py's contract,
independently against the node tree instead of raw text):
- Condition survival: ifdef = var set AND non-empty; ifndef = var unset
  OR empty. A var present but set to "" does NOT satisfy ifdef.
- Effective value for a key = last surviving layer, in the array's
  existing depth-first order (no re-sorting).
- Zero surviving layers on a key that's never referenced elsewhere is
  NOT a failure -- the key is just absent from `effective`.
- Zero surviving layers on a key that IS reached via a `ref` node during
  resolution IS a failure: "$(path) has no surviving layer for this
  environment" -- distinct wording from "key not found", which is
  reserved for a path that isn't in `doc["keys"]` at all.
- Circular references are detected per top-level key with a fresh
  visiting chain each time (no memoization across keys), so e.g.
  cascade.loop.a and cascade.loop.b each get their own failure entry.
- depth only increments once per key visited (i.e. once per `ref` hop
  crossed), never for descending into a `concat`/`env.default`/`env.alt`
  within the SAME key's own value tree. This mirrors legacy_evaluator.py's
  _resolve_key-only increment exactly, so a single key with deeply nested
  env/concat syntax (e.g. container.tag's 3-level nest) can't spuriously
  hit MAX_DEPTH on one side when it wouldn't on the other.
- `env` values are opaque: a raw, already-set env var's text is returned
  as-is and never rescanned for further ${...}/$(...) syntax. Only
  `default`/`alt` node syntax (literal .pfcfg-authored structure) is
  recursed into.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

MAX_DEPTH = 100  # must stay in lockstep with legacy_evaluator.py's MAX_DEPTH


# --------------------------------------------------------------------------
# Failure types -- duplicated (not imported) from legacy_evaluator.py's
# concept set, per the independence constraint. Same names, same wording,
# independently defined.
# --------------------------------------------------------------------------

class EvalFailure(Exception):
    """Base class for the hard failures the unmigratable report cares about."""


class CircularReferenceError(EvalFailure):
    def __init__(self, chain: List[str]):
        self.chain = chain
        super().__init__(" → ".join(chain))


class MissingReferenceError(EvalFailure):
    def __init__(self, path: str):
        self.path = path
        super().__init__(path)


class NoSurvivingLayerError(EvalFailure):
    def __init__(self, path: str):
        self.path = path
        super().__init__(path)


class EvaluationTooDeepError(EvalFailure):
    def __init__(self, depth: int):
        self.depth = depth
        super().__init__(f"exceeded max reference depth ({depth})")


@dataclass
class FailureEntry:
    file: Optional[str]
    section: Optional[str]
    key: Optional[str]
    line: Optional[int]
    reason: str


@dataclass
class EvaluationResult:
    effective: Dict[str, str] = field(default_factory=dict)
    failures: List[FailureEntry] = field(default_factory=list)


# --------------------------------------------------------------------------
# Condition survival -- independently reimplemented against the JSON
# condition array (list of {"type": "ifdef"|"ifndef", "var": ...}), same
# semantics as legacy_evaluator.py's layer_survives: "set" means present
# AND non-empty, not merely present.
# --------------------------------------------------------------------------

def layer_survives(condition: List[Dict[str, str]], env: Dict[str, str]) -> bool:
    for term in condition:
        val = env.get(term["var"])
        is_set = val is not None and val != ""
        if term["type"] == "ifdef" and not is_set:
            return False
        if term["type"] == "ifndef" and is_set:
            return False
    return True


def _surviving_layers(
    layers: List[Dict[str, Any]], env: Dict[str, str]
) -> List[Tuple[int, Dict[str, Any]]]:
    """(original_index, layer) pairs whose condition holds, original
    depth-first array order preserved. Index is kept so the caller can
    align back to the provenance sidecar, which is indexed against the
    full (unfiltered) layers list, not the filtered survivor list."""
    return [
        (i, layer)
        for i, layer in enumerate(layers)
        if layer_survives(layer.get("condition", []), env)
    ]


# --------------------------------------------------------------------------
# Core recursive resolution
# --------------------------------------------------------------------------

def _resolve_key(
    full_key: str,
    env: Dict[str, str],
    keys_map: Dict[str, Any],
    visiting: Tuple[str, ...],
    depth: int,
) -> str:
    """Resolve full_key to its effective string value, or raise an
    EvalFailure. depth/visiting semantics mirror legacy_evaluator.py's
    _resolve_key exactly: the guard checks use the depth/visiting as
    passed in; only the recursion into this key's OWN value tree adds
    +1 / adds full_key to visiting -- so depth counts key-hops, not
    node-tree descent."""
    if depth > MAX_DEPTH:
        raise EvaluationTooDeepError(depth)
    if full_key in visiting:
        raise CircularReferenceError(list(visiting) + [full_key])
    if full_key not in keys_map:
        raise MissingReferenceError(full_key)

    layers = keys_map[full_key]["layers"]
    surviving = _surviving_layers(layers, env)
    if not surviving:
        raise NoSurvivingLayerError(full_key)

    _, last_layer = surviving[-1]
    return _resolve_node(
        last_layer["value"], env, keys_map, visiting + (full_key,), depth + 1
    )


def _resolve_node(
    node: Dict[str, Any],
    env: Dict[str, str],
    keys_map: Dict[str, Any],
    visiting: Tuple[str, ...],
    depth: int,
) -> str:
    node_type = node["type"]

    if node_type == "literal":
        return node["text"]

    if node_type == "env":
        var = node["var"]
        has_default = "default" in node
        has_alt = "alt" in node
        # NOTE: this "is_set" check (var in env and non-empty) governs
        # which BRANCH of the env node fires (default/alt vs raw value) --
        # a different concern from layer_survives' ifdef/ifndef condition
        # check above, deliberately not shared code between the two.
        is_set = var in env and env[var] != ""

        if has_alt:
            # ${VAR:+alt} -- alt only when VAR is set AND non-empty.
            if is_set:
                return _resolve_node(node["alt"], env, keys_map, visiting, depth)
            return ""

        if has_default:
            # ${VAR:-default} -- default when VAR is unset OR empty.
            if is_set:
                return env[var]  # opaque: raw env text, never rescanned
            return _resolve_node(node["default"], env, keys_map, visiting, depth)

        # bare ${VAR} -- direct substitution, "" if unset. Never a failure.
        return env.get(var, "")

    if node_type == "concat":
        return "".join(
            _resolve_node(part, env, keys_map, visiting, depth)
            for part in node["parts"]
        )

    if node_type == "ref":
        target = node["path"]
        # Depth/visiting are passed through UNCHANGED here -- the +1 /
        # visiting-extension for crossing into `target` happens inside
        # _resolve_key itself (see its docstring), exactly mirroring
        # legacy_evaluator.py's _resolve_ref_token -> _resolve_key call,
        # which also passes depth/visiting through unchanged at the call
        # site and lets _resolve_key do the bookkeeping.
        return _resolve_key(target, env, keys_map, visiting, depth)

    raise ValueError(f"unknown node type: {node_type!r}")


# --------------------------------------------------------------------------
# Reason text -- deliberately matches legacy_evaluator.py's _reason_for
# wording exactly, so a report built from either evaluator reads
# identically for the same failure class (this is a specification
# agreement, independently implemented on each side, not shared code).
# --------------------------------------------------------------------------

def _reason_for(exc: EvalFailure) -> str:
    if isinstance(exc, CircularReferenceError):
        immediate = exc.chain[1] if len(exc.chain) > 1 else exc.chain[0]
        return f"Unresolved $({immediate}) — circular reference: " + " → ".join(exc.chain)
    if isinstance(exc, MissingReferenceError):
        return f"Unresolved $({exc.path}) — key not found"
    if isinstance(exc, NoSurvivingLayerError):
        return f"$({exc.path}) has no surviving layer for this environment"
    if isinstance(exc, EvaluationTooDeepError):
        return f"Evaluation exceeded maximum reference depth ({exc.depth}) — likely runaway recursion"
    return str(exc)


# --------------------------------------------------------------------------
# Top-level driver
# --------------------------------------------------------------------------

def evaluate(
    doc: Dict[str, Any],
    provenance: Dict[str, List[Dict[str, Any]]],
    env: Dict[str, str],
) -> EvaluationResult:
    """Evaluate every top-level key in doc["keys"] independently against
    `env`. Each key gets a fresh visiting=() / depth=0 -- no memoization
    across keys, matching the "separate failure entry per broken key"
    requirement.

    `doc` is the schema.json-shaped behavioral JSON; `provenance` is the
    separate sidecar built by serialize.py's build_provenance() (NOT part
    of `doc` -- provenance was locked as a sidecar, kept out of the
    schema). provenance[full_key][i] must describe
    doc["keys"][full_key]["layers"][i], index-aligned.
    """
    keys_map = doc["keys"]
    result = EvaluationResult()

    for full_key, key_obj in keys_map.items():
        layers = key_obj["layers"]
        surviving = _surviving_layers(layers, env)

        if not surviving:
            # Zero surviving layers on the TOP-LEVEL key itself is not a
            # failure -- just absent from effective settings. (Distinct
            # from NoSurvivingLayerError, which only fires when something
            # else references this key via a ref node.)
            continue

        layer_index, _ = surviving[-1]
        prov_list = provenance.get(full_key, [])
        prov = prov_list[layer_index] if layer_index < len(prov_list) else {}

        try:
            value = _resolve_key(full_key, env, keys_map, tuple(), 0)
        except EvalFailure as exc:
            result.failures.append(
                FailureEntry(
                    file=prov.get("file"),
                    section=prov.get("section"),
                    key=prov.get("key"),
                    line=prov.get("line"),
                    reason=_reason_for(exc),
                )
            )
        else:
            result.effective[full_key] = value

    return result
