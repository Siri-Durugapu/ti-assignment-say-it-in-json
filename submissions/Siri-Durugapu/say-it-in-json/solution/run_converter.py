"""
Converter CLI (assignment item 2: ".pfcfg -> JSON").

Thin wrapper around the existing library functions in serialize.py
(convert_entry / convert_entry_with_provenance / provenance_sidecar_path) --
no new conversion logic here, this just makes them runnable and writes
their output to disk.

Two modes:

  1. Single entry, general-purpose:
       python run_converter.py <entry.pfcfg> [configs_root] [out.json]

     Converts one entry. out.json defaults to "<basename>.json" in the
     current directory; the provenance sidecar is written alongside it
     via serialize.provenance_sidecar_path() ("<out>.json" ->
     "<out>.provenance.json"). configs_root (used to make provenance
     "file" paths repo-relative instead of absolute) defaults to
     "starter/configs".

  2. All 5 locked starter entries:
       python run_converter.py --all [configs_root]

     Converts the same 5 real entries used throughout the rest of the
     solution (run_equivalence_verifier.py, test_json_evaluator.py,
     run_unmigratable_report.py) and writes "<name>.regen.json" +
     "<name>.provenance.json" for each -- that's the exact naming
     run_equivalence_verifier.py and test_json_evaluator.py already
     expect (json.load(open(f"{name}.regen.json")) /
     f"{name}.provenance.json"). "regen" distinguishes these as freshly
     regenerated-by-the-real-converter output, as opposed to any
     earlier hand/ad-hoc JSON sitting in this directory from schema
     design work.
"""
from __future__ import annotations
import json
import os
import sys

import serialize as S

RELATIVE_ENTRIES = {
    "acme-corp": "customers/acme-corp/pipeline.pfcfg",
    "globex": "customers/globex/pipeline.pfcfg",
    "initech": "customers/initech/pipeline.pfcfg",
    "interpolation-cascade": "edge-cases/interpolation-cascade.pfcfg",
    "conditional-includes": "edge-cases/conditional-includes.pfcfg",
}


def _write(doc: dict, prov: dict, json_path: str, prov_path: str) -> None:
    with open(json_path, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    with open(prov_path, "w") as f:
        json.dump(prov, f, indent=2)
        f.write("\n")
    print(f"wrote {json_path} and {prov_path}")


def convert_all(configs_root: str) -> None:
    # NOTE: intentionally does NOT derive the sidecar path via
    # serialize.provenance_sidecar_path() here. That helper does a plain
    # splitext substitution ("<x>.json" -> "<x>.provenance.json"), which
    # on a "<name>.regen.json" input would yield
    # "<name>.regen.provenance.json" -- not the "<name>.provenance.json"
    # that run_equivalence_verifier.py / test_json_evaluator.py actually
    # load. Named explicitly instead so the two locked naming schemes
    # (".regen.json" for the JSON, plain ".provenance.json" for the
    # sidecar) don't collide.
    for name, rel in RELATIVE_ENTRIES.items():
        entry_path = os.path.join(configs_root, rel)
        doc, prov = S.convert_entry_with_provenance(entry_path, configs_root)
        _write(doc, prov, f"{name}.regen.json", f"{name}.provenance.json")


def convert_one(entry_path: str, configs_root: str, out_path: str) -> None:
    doc, prov = S.convert_entry_with_provenance(entry_path, configs_root)
    _write(doc, prov, out_path, S.provenance_sidecar_path(out_path))


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--all":
        configs_root = args[1] if len(args) > 1 else "starter/configs"
        convert_all(configs_root)
        return

    if not args:
        print(__doc__)
        sys.exit(1)

    entry_path = args[0]
    configs_root = args[1] if len(args) > 1 else "starter/configs"
    if len(args) > 2:
        out_path = args[2]
    else:
        base = os.path.splitext(os.path.basename(entry_path))[0]
        out_path = f"{base}.json"
    convert_one(entry_path, configs_root, out_path)


if __name__ == "__main__":
    main()
