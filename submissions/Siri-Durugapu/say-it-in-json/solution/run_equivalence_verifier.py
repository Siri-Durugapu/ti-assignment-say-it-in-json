import json

import equivalence_verifier as V

# The two fixtures locked in an earlier session, built by scanning every
# ifdef/ifndef var actually used across all 5 entry configs -- not
# reconstructed or approximated here.

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

ENTRIES = {
    "acme-corp": "../../../../starter/configs/customers/acme-corp/pipeline.pfcfg",
    "globex": "../../../../starter/configs/customers/globex/pipeline.pfcfg",
    "initech": "../../../../starter/configs/customers/initech/pipeline.pfcfg",
    "conditional-includes": "../../../../starter/configs/edge-cases/conditional-includes.pfcfg",
    "interpolation-cascade": "../../../../starter/configs/edge-cases/interpolation-cascade.pfcfg",
}

all_reports = []
any_divergent = False

for name, entry_path in ENTRIES.items():
    doc = json.load(open(f"{name}.regen.json"))
    prov = json.load(open(f"{name}.provenance.json"))
    for env_name, env in [("ci-production", CI_PRODUCTION), ("non-ci", NON_CI)]:
        report = V.verify_entry(entry_path, doc, prov, env, entry_name=name, env_name=env_name)
        all_reports.append(report)
        print(report.summary_line())
        for line in report.detail_lines():
            print(line)
            any_divergent = any_divergent or not report.is_equivalent

print()
print("=" * 78)
n_equiv = sum(1 for r in all_reports if r.is_equivalent)
n_total = len(all_reports)
n_reason_diffs = sum(len(r.reason_text_differences) for r in all_reports)
print(f"{n_equiv}/{n_total} entry x environment combinations are EQUIVALENT")
if n_reason_diffs:
    print(f"({n_reason_diffs} failure-reason WORDING differences noted above -- "
          f"do not count against equivalence, see EquivalenceReport.is_equivalent docstring)")
if n_equiv == n_total:
    print("RESULT: the two independently-implemented evaluators are fully equivalent "
          "across all 5 real starter configs, under both locked environment fixtures.")
else:
    print("RESULT: DIVERGENCE FOUND -- see details above.")