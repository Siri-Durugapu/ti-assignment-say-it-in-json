"""
Turns the resolver's flattened Assignment stream into the schema's
"keys" flat map, running each raw_value through the interpolation
parser to get its node tree.

Also builds the provenance sidecar (see SCHEMA-PROPOSAL.md / DECISIONS —
provenance is deliberately NOT part of the behavioral schema, so it's a
separate map, not a field on each layer).
"""
from __future__ import annotations
import os
from typing import Dict, List, Optional

import resolver as R
import interpolation as I


def build_keys(stream: List[R.Assignment]) -> Dict[str, dict]:
    grouped = R.group_by_key(stream)
    keys: Dict[str, dict] = {}
    for full_key, assignments in grouped.items():
        layers = []
        for a in assignments:
            layer = {"value": I.parse_value(a.raw_value)}
            if a.condition:
                layer["condition"] = [c.to_dict() for c in a.condition]
            layers.append(layer)
        keys[full_key] = {"layers": layers}
    return keys


def build_provenance(
    stream: List[R.Assignment], root: Optional[str] = None
) -> Dict[str, List[dict]]:
    """Same grouping, same per-key order as build_keys, so provenance[full_key][i]
    describes keys[full_key]["layers"][i] for every full_key/i.

    Each entry carries file, line, section, and key. section/key are taken
    directly from the Assignment record (which already has them as separate
    fields from the parser/resolver stage) rather than reconstructed by
    splitting full_key on its dots later — that split is genuinely ambiguous
    in general (see the [cascade]/[cascade.loop] sibling-section case that
    ruled out a nested `keys` tree), so the only safe time to capture
    section/key is now, while the resolver still has them apart, not after
    they've been joined into a single dotted string.

    root: if given, source_file (which resolver.py stores as an absolute
    path) is made relative to it, to match the repo-relative paths shown
    in the handoff's sidecar example (e.g. "customers/acme-corp/pipeline.pfcfg").
    If omitted, the absolute path is kept as-is.
    """
    grouped = R.group_by_key(stream)
    provenance: Dict[str, List[dict]] = {}
    for full_key, assignments in grouped.items():
        entries = []
        for a in assignments:
            file_path = a.source_file
            if root is not None:
                file_path = os.path.relpath(file_path, root)
            entries.append({
                "file": file_path,
                "line": a.source_line,
                "section": a.section,
                "key": a.key,
            })
        provenance[full_key] = entries
    return provenance


def convert_entry(entry_path: str) -> dict:
    stream = R.resolve_entry(entry_path)
    return {"keys": build_keys(stream)}


def convert_entry_with_provenance(
    entry_path: str, root: Optional[str] = None
) -> tuple[dict, Dict[str, List[dict]]]:
    """Resolve entry_path once and build both the behavioral JSON and its
    provenance sidecar off the same Assignment stream, so they can never
    drift out of sync with each other."""
    stream = R.resolve_entry(entry_path)
    return {"keys": build_keys(stream)}, build_provenance(stream, root=root)


def provenance_sidecar_path(entry_json_path: str) -> str:
    """<entry>.json -> <entry>.provenance.json, matching the naming
    convention from the handoff (`<entry>.provenance.json`)."""
    base, ext = os.path.splitext(entry_json_path)
    return f"{base}.provenance.json"