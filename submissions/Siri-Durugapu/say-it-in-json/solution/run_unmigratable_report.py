"""
Runnable driver: builds the unmigratable report for all 5 real starter
entries under the two locked environment fixtures (ci-production, non-ci),
and writes it as both JSON and NDJSON.

Usage: python run_unmigratable_report.py <path-to-starter/configs>
"""
import json
import os
import sys

import serialize as S
import unmigratable_report as U

CI_PRODUCTION = {
    "CI": "true",
    "PRODUCTION": "true",
    "SLACK_WEBHOOK": "https://hooks.slack.example.invalid/services/T000/B000/aaaaaaaa",
    "ACME_DEPLOY_TARGET": "production",
    "VAULT_ADDR": "https://vault.initech.example.invalid",
    "FEATURE_BETA": "true",
    "MIGRATION_AUDIT": "true",
    "MIGRATION_AUDIT_USER": "jordan.okonkwo",
    "MIGRATION_AUDIT_TICKET": "PF-4821",
    "REQUIRED_API_ENDPOINT": "https://api.pipelineforge.example.invalid/v1",
    "REQUIRED_SIGNING_SECRET": "prod-signing-material-000",
    "GLOBEX_ENV": "production",
}
NON_CI: dict = {}

FIXTURES = {"ci-production": CI_PRODUCTION, "non-ci": NON_CI}

RELATIVE_ENTRIES = {
    "acme-corp": "customers/acme-corp/pipeline.pfcfg",
    "globex": "customers/globex/pipeline.pfcfg",
    "initech": "customers/initech/pipeline.pfcfg",
    "interpolation-cascade": "edge-cases/interpolation-cascade.pfcfg",
    "conditional-includes": "edge-cases/conditional-includes.pfcfg",
}


def main():
    configs_root = sys.argv[1] if len(sys.argv) > 1 else "starter/configs"
    entries = {name: os.path.join(configs_root, rel) for name, rel in RELATIVE_ENTRIES.items()}

    docs = {}
    provs = {}
    for name, path in entries.items():
        doc, prov = S.convert_entry_with_provenance(path, configs_root)
        docs[name] = doc
        provs[name] = prov

    report = U.build_report(entries, FIXTURES, docs, provs)

    U.write_json(report, "unmigratable-report.json")
    U.write_ndjson(report, "unmigratable-report.ndjson")

    print(f"{len(report)} unmigratable finding(s) across {len(entries)} entries "
          f"x {len(FIXTURES)} fixtures (plus conversion-time scan).")
    for e in report:
        loc = f"{e.file}:{e.line}" if e.line is not None else e.file
        envs = f" [{', '.join(sorted(e.environments))}]" if e.environments else ""
        print(f"  [{e.phase}] {loc} {e.section}.{e.key}{envs} — {e.reason}")

    print()
    print("Wrote unmigratable-report.json and unmigratable-report.ndjson")


if __name__ == "__main__":
    main()
