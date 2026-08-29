"""
equivalence_verifier.py

Compares the two independently-implemented evaluators -- legacy_evaluator.py
(resolves raw .pfcfg text directly) and json_evaluator.py (walks the
converted literal/env/ref/concat node tree) -- against the same real config
and environment, and reports exactly where they diverge.

This module does NOT modify, wrap, or reimplement anything from either
evaluator or from resolver.py. It only calls their existing public
entry points (legacy_evaluator.evaluate / json_evaluator.evaluate) and
diffs the two EvaluationResult-shaped outputs.

Two independently-implemented evaluators agreeing on every key, for every
environment, across every real config, is the actual evidence that the
resolver's semantics and the JSON schema's node encoding did not lose or
distort anything in the .pfcfg -> JSON conversion. A boolean pass/fail
would throw away exactly the information needed to debug the one case
where they don't.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import legacy_evaluator as L
import json_evaluator as J
import resolver as R


def _full_key(section: Optional[str], key: Optional[str]) -> Optional[str]:
    """legacy_evaluator.py's FailureEntry and json_evaluator.py's
    FailureEntry both carry section/key already split apart (from
    Assignment / the provenance sidecar respectively) -- reconstructing
    "section.key" here is safe and unambiguous, unlike the reverse
    operation (splitting a dotted full_key back into section/key), which
    is the exact ambiguity that ruled out a nested `keys` tree in the
    schema to begin with."""
    if section is None or key is None:
        return None
    return f"{section}.{key}"


@dataclass
class ValueMismatch:
    full_key: str
    legacy_value: str
    json_value: str


@dataclass
class FailureMismatch:
    full_key: str
    legacy_reason: Optional[str]
    json_reason: Optional[str]
    only_on: str  # "legacy" | "json" | "both-different-reason"


@dataclass
class EquivalenceReport:
    entry: str
    env_name: str
    legacy_effective_count: int = 0
    json_effective_count: int = 0
    legacy_failure_count: int = 0
    json_failure_count: int = 0
    effective_only_in_legacy: Dict[str, str] = field(default_factory=dict)
    effective_only_in_json: Dict[str, str] = field(default_factory=dict)
    value_mismatches: List[ValueMismatch] = field(default_factory=list)
    failure_mismatches: List[FailureMismatch] = field(default_factory=list)
    reason_text_differences: List[FailureMismatch] = field(default_factory=list)

    @property
    def is_equivalent(self) -> bool:
        """Reason-text wording differences are reported but do NOT count
        against equivalence -- they're a shared spec-agreement string
        (see json_evaluator.py's module docstring), not semantics. What
        matters for equivalence is: same effective settings, same set of
        keys that failed."""
        return not (
            self.effective_only_in_legacy
            or self.effective_only_in_json
            or self.value_mismatches
            or self.failure_mismatches
        )

    def summary_line(self) -> str:
        status = "EQUIVALENT" if self.is_equivalent else "DIVERGENT"
        return (
            f"[{status}] {self.entry} / {self.env_name} -- "
            f"legacy: {self.legacy_effective_count} effective / {self.legacy_failure_count} failed; "
            f"json: {self.json_effective_count} effective / {self.json_failure_count} failed"
        )

    def detail_lines(self) -> List[str]:
        lines: List[str] = []
        if self.effective_only_in_legacy:
            lines.append(
                f"  effective ONLY in legacy ({len(self.effective_only_in_legacy)}): "
                f"{self.effective_only_in_legacy}"
            )
        if self.effective_only_in_json:
            lines.append(
                f"  effective ONLY in json ({len(self.effective_only_in_json)}): "
                f"{self.effective_only_in_json}"
            )
        for vm in self.value_mismatches:
            lines.append(
                f"  VALUE MISMATCH {vm.full_key}: legacy={vm.legacy_value!r} json={vm.json_value!r}"
            )
        for fm in self.failure_mismatches:
            lines.append(
                f"  FAILURE MISMATCH {fm.full_key} (only_on={fm.only_on}): "
                f"legacy={fm.legacy_reason!r} json={fm.json_reason!r}"
            )
        for fm in self.reason_text_differences:
            lines.append(
                f"  reason text differs (both sides failed, not counted against equivalence) "
                f"{fm.full_key}: legacy={fm.legacy_reason!r} json={fm.json_reason!r}"
            )
        return lines


def compare(
    entry: str,
    env_name: str,
    legacy_result: "L.EvaluationResult",
    json_result: "J.EvaluationResult",
) -> EquivalenceReport:
    report = EquivalenceReport(entry=entry, env_name=env_name)
    report.legacy_effective_count = len(legacy_result.effective)
    report.json_effective_count = len(json_result.effective)
    report.legacy_failure_count = len(legacy_result.failures)
    report.json_failure_count = len(json_result.failures)

    legacy_eff = legacy_result.effective
    json_eff = json_result.effective
    legacy_keys = set(legacy_eff)
    json_keys = set(json_eff)

    for k in sorted(legacy_keys - json_keys):
        report.effective_only_in_legacy[k] = legacy_eff[k]
    for k in sorted(json_keys - legacy_keys):
        report.effective_only_in_json[k] = json_eff[k]
    for k in sorted(legacy_keys & json_keys):
        if legacy_eff[k] != json_eff[k]:
            report.value_mismatches.append(ValueMismatch(k, legacy_eff[k], json_eff[k]))

    legacy_fail_map: Dict[str, Any] = {}
    for f in legacy_result.failures:
        fk = _full_key(f.section, f.key)
        if fk is not None:
            legacy_fail_map[fk] = f

    json_fail_map: Dict[str, Any] = {}
    for f in json_result.failures:
        fk = _full_key(f.section, f.key)
        if fk is not None:
            json_fail_map[fk] = f

    legacy_fail_keys = set(legacy_fail_map)
    json_fail_keys = set(json_fail_map)

    for k in sorted(legacy_fail_keys - json_fail_keys):
        report.failure_mismatches.append(
            FailureMismatch(k, legacy_fail_map[k].reason, None, only_on="legacy")
        )
    for k in sorted(json_fail_keys - legacy_fail_keys):
        report.failure_mismatches.append(
            FailureMismatch(k, None, json_fail_map[k].reason, only_on="json")
        )
    for k in sorted(legacy_fail_keys & json_fail_keys):
        lr, jr = legacy_fail_map[k].reason, json_fail_map[k].reason
        if lr != jr:
            report.reason_text_differences.append(
                FailureMismatch(k, lr, jr, only_on="both-different-reason")
            )

    return report


def verify_entry(
    entry_path: str,
    doc: Dict[str, Any],
    provenance: Dict[str, List[Dict[str, Any]]],
    env: Dict[str, str],
    entry_name: str,
    env_name: str,
) -> EquivalenceReport:
    """Resolve entry_path independently for the legacy side (a fresh
    parser.py/resolver.py re-walk of the raw .pfcfg tree), and use the
    already-converted doc/provenance for the JSON side -- run both
    evaluators and diff the results.

    Both sides ultimately depend on resolver.py's include/condition
    flattening (that's the intentionally-shared structural layer, per the
    locked design); everything downstream of that -- condition survival,
    value/reference resolution, cycle and depth tracking -- runs through
    each evaluator's own independent implementation.
    """
    stream = R.resolve_entry(entry_path)
    grouped = R.group_by_key(stream)
    legacy_result = L.evaluate(grouped, env)
    json_result = J.evaluate(doc, provenance, env)
    return compare(entry_name, env_name, legacy_result, json_result)
