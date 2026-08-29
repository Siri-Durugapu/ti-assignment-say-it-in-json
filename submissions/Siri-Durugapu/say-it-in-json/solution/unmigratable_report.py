"""
unmigratable_report.py

Produces the machine-readable "unmigratable" report (assignment item 5):
a JSON/NDJSON list of items that cannot be converted or verified
automatically, each with at least `file`, `section`, `key`, `reason`
(`line` optional).

Locked design (session 4): this is genuinely TWO entry points, not one
function branching internally, because "unmigratable" means two
different things depending on which phase finds it:

  - from_conversion(): a CONVERSION-TIME, environment-INDEPENDENT
    finding -- the converter itself cannot safely turn a raw .pfcfg
    value into a literal/env/ref/concat node at all (e.g. malformed
    interpolation syntax: an unterminated "${" or "$(", an empty
    variable name). This re-walks resolver.py's flattened stream and
    calls interpolation.parse_value() on every raw_value exactly the
    way serialize.py does -- it does not reimplement or duplicate
    interpolation.py's grammar, it just catches what interpolation.py
    itself already raises. Nothing in resolver.py, interpolation.py, or
    serialize.py is modified to support this; it is purely additive.

  - from_evaluation(): an EVALUATION-TIME, environment-DEPENDENT
    finding -- resolution failed for a *specific* environment fixture
    (circular reference, missing reference, a reference into a key with
    no surviving layer for this environment, or a pathological
    reference chain past MAX_DEPTH). This is a thin adapter over
    json_evaluator.EvaluationResult.failures (or legacy_evaluator's,
    which the two-evaluator equivalence check already established
    produce the identical failing-key set) -- it does not recompute
    anything, it only reshapes FailureEntry into the report's shape.

Both phases feed into one aggregated report. Findings that are
env-independent (like a genuine structural cycle, which fails under
every environment because it's not gated by any condition) are
deduplicated across fixtures into a single entry that lists every
environment it was observed under, rather than being repeated
once per fixture -- Jordan's brief asks for "which file, which key,
why," not a wall of near-identical rows for the same root cause.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import json

import resolver as R
import interpolation as I
import json_evaluator as J


@dataclass
class ReportEntry:
    file: Optional[str]
    section: Optional[str]
    key: Optional[str]
    reason: str
    line: Optional[int] = None
    phase: str = "evaluation"  # "conversion" | "evaluation"
    environments: List[str] = field(default_factory=list)  # only meaningful for phase="evaluation"

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "file": self.file,
            "section": self.section,
            "key": self.key,
            "reason": self.reason,
        }
        if self.line is not None:
            d["line"] = self.line
        d["phase"] = self.phase
        if self.environments:
            d["environments"] = sorted(self.environments)
        return d


# --------------------------------------------------------------------------
# Phase 1: conversion-time, environment-independent
# --------------------------------------------------------------------------

def from_conversion(entry_path: str) -> List[ReportEntry]:
    """Re-walk entry_path's flattened stream (same resolver.py call
    serialize.py itself makes) and attempt interpolation.parse_value on
    every raw_value. A parse failure here means the converter cannot
    safely represent this value as a node AT ALL, regardless of
    environment -- this is a structural/syntax problem in the .pfcfg
    source, not a resolution problem.

    On the real starter corpus (all 5 entries, 280 assignments, 102
    unique raw values) this returns an empty list -- every observed
    interpolation form in the starter set is representable in the
    locked node union. This function is included, and genuinely wired
    to interpolation.py's real exception type, so the report mechanism
    is not vaporware for a customer tree that hits a form the starter
    set never exercises (an unterminated "${", an empty "$()").
    """
    findings: List[ReportEntry] = []
    stream = R.resolve_entry(entry_path)
    for a in stream:
        try:
            I.parse_value(a.raw_value)
        except I.InterpolationError as exc:
            findings.append(
                ReportEntry(
                    file=a.source_file,
                    section=a.section,
                    key=a.key,
                    line=a.source_line,
                    reason=f"Unmigratable value {a.raw_value!r} — {exc}",
                    phase="conversion",
                )
            )
    return findings


# --------------------------------------------------------------------------
# Phase 2: evaluation-time, per-environment
# --------------------------------------------------------------------------

def from_evaluation(env_name: str, result: "J.EvaluationResult") -> List[ReportEntry]:
    """Reshape an EvaluationResult's failures (already file/section/key/
    line/reason, produced by json_evaluator.evaluate or
    legacy_evaluator.evaluate -- the equivalence verifier already
    established these two agree on the failing-key set for every real
    entry/fixture combination) into ReportEntry, tagged with the
    fixture it was observed under.
    """
    return [
        ReportEntry(
            file=f.file,
            section=f.section,
            key=f.key,
            line=f.line,
            reason=f.reason,
            phase="evaluation",
            environments=[env_name],
        )
        for f in result.failures
    ]


# --------------------------------------------------------------------------
# Aggregation: merge across phases and environments
# --------------------------------------------------------------------------

def _identity(entry: ReportEntry):
    """Two findings are "the same underlying issue" if they name the
    same file/section/key/reason/phase -- an env-independent structural
    cycle produces byte-identical reason text under every fixture it's
    tested against, so this collapses those into one row with a merged
    `environments` list rather than one row per fixture."""
    return (entry.file, entry.section, entry.key, entry.reason, entry.phase)


def aggregate(entries: List[ReportEntry]) -> List[ReportEntry]:
    merged: Dict[tuple, ReportEntry] = {}
    order: List[tuple] = []
    for e in entries:
        k = _identity(e)
        if k not in merged:
            merged[k] = ReportEntry(
                file=e.file, section=e.section, key=e.key,
                reason=e.reason, line=e.line, phase=e.phase,
                environments=list(e.environments),
            )
            order.append(k)
        else:
            for env in e.environments:
                if env not in merged[k].environments:
                    merged[k].environments.append(env)
    return [merged[k] for k in order]


# --------------------------------------------------------------------------
# Top-level driver: build the report for a set of entries x fixtures
# --------------------------------------------------------------------------

def build_report(
    entries: Dict[str, str],
    fixtures: Dict[str, Dict[str, str]],
    docs: Dict[str, Any],
    provenances: Dict[str, Any],
) -> List[ReportEntry]:
    """entries: {entry_name: entry_path (.pfcfg)}
    fixtures: {env_name: env dict}
    docs / provenances: {entry_name: already-converted doc / sidecar}
    (the JSON-evaluator side is used as the source of evaluation
    failures here since the equivalence verifier already confirmed it
    agrees with legacy_evaluator on every real case; either would do).
    """
    all_entries: List[ReportEntry] = []
    for entry_name, entry_path in entries.items():
        all_entries.extend(from_conversion(entry_path))
        doc = docs[entry_name]
        prov = provenances[entry_name]
        for env_name, env in fixtures.items():
            result = J.evaluate(doc, prov, env)
            all_entries.extend(from_evaluation(env_name, result))
    return aggregate(all_entries)


def write_json(entries: List[ReportEntry], path: str) -> None:
    with open(path, "w") as f:
        json.dump([e.to_dict() for e in entries], f, indent=2)


def write_ndjson(entries: List[ReportEntry], path: str) -> None:
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e.to_dict()))
            f.write("\n")
