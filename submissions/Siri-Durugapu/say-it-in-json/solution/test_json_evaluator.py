import json
import sys

import json_evaluator as J
import legacy_evaluator as L
import resolver as R

FAIL = 0


def check(label, cond):
    global FAIL
    status = "PASS" if cond else "FAIL"
    if not cond:
        FAIL += 1
    print(f"[{status}] {label}")


def load(name):
    doc = json.load(open(f"{name}.regen.json"))
    prov = json.load(open(f"{name}.provenance.json"))
    return doc, prov


CI_PRODUCTION = {"CI": "true", "PRODUCTION": "true", "SLACK_WEBHOOK": "https://hooks.example.invalid/x"}
NON_CI = {}

print("=" * 70)
print("1. CIRCULAR REFERENCE -- interpolation-cascade.json, cascade.loop.a/b")
print("=" * 70)
doc, prov = load("interpolation-cascade")
r = J.evaluate(doc, prov, env={})
failure_keys = {f.key: f for f in r.failures}
check("cascade.loop.a and cascade.loop.b BOTH reported (separate entries)",
      "a" in failure_keys and "b" in failure_keys)
check("cascade.loop.a/b absent from effective",
      "cascade.loop.a" not in r.effective and "cascade.loop.b" not in r.effective)
check("reason mentions circular reference",
      all("circular reference" in f.reason for f in r.failures if f.key in ("a", "b")))
check("provenance file/section attached correctly for cascade.loop.a",
      failure_keys["a"].file is not None and failure_keys["a"].section == "cascade.loop")
for k in ("a", "b"):
    print(f"    cascade.loop.{k}: file={failure_keys[k].file} line={failure_keys[k].line} "
          f"section={failure_keys[k].section} reason={failure_keys[k].reason!r}")

print()
print("=" * 70)
print("2. NESTED REFERENCE CHAIN -- cascade.alpha -> beta -> gamma -> delta -> epsilon")
print("=" * 70)
r_nocid = J.evaluate(doc, prov, env={})
r_ci = J.evaluate(doc, prov, env={"CI": "true"})
check("cascade.alpha default resolves to 'unset'", r_nocid["effective"] if False else r_nocid.effective.get("cascade.alpha") == "unset")
check("cascade.beta = prefix-unset-suffix", r_nocid.effective.get("cascade.beta") == "prefix-unset-suffix")
check("cascade.delta = prefix-unset-suffix-final", r_nocid.effective.get("cascade.delta") == "prefix-unset-suffix-final")
check("cascade.epsilon (no CI) = local-prefix-unset-suffix-final",
      r_nocid.effective.get("cascade.epsilon") == "local-prefix-unset-suffix-final")
check("cascade.epsilon (CI set) = ci-prefix-unset-suffix-final",
      r_ci.effective.get("cascade.epsilon") == "ci-prefix-unset-suffix-final")
print(f"    epsilon (no env): {r_nocid.effective.get('cascade.epsilon')!r}")
print(f"    epsilon (CI set): {r_ci.effective.get('cascade.epsilon')!r}")

print()
print("=" * 70)
print("3. CONDITIONAL LAYERS + LAST-WRITE-WINS -- acme-corp.json notify.slack.*, container.build.push")
print("=" * 70)
doc_a, prov_a = load("acme-corp")
r_slack_on = J.evaluate(doc_a, prov_a, env={"SLACK_WEBHOOK": "x"})
r_slack_off = J.evaluate(doc_a, prov_a, env={})
check("slack.enabled true when SLACK_WEBHOOK set", r_slack_on.effective.get("notify.slack.enabled") == "true")
check("slack.enabled false when SLACK_WEBHOOK unset", r_slack_off.effective.get("notify.slack.enabled") == "false")
r_push_ci = J.evaluate(doc_a, prov_a, env={"CI": "true"})
r_push_nocid = J.evaluate(doc_a, prov_a, env={})
check("container.build.push true under CI (conditional layer wins)", r_push_ci.effective.get("container.build.push") == "true")
check("container.build.push false without CI (base layer survives alone)", r_push_nocid.effective.get("container.build.push") == "false")

print()
print("=" * 70)
print("4. DUAL-BRANCH MUTUALLY-EXCLUSIVE LAYERS -- conditional-includes.json toolchain.node.version")
print("=" * 70)
doc_c, prov_c = load("conditional-includes")
tv = doc_c["keys"].get("toolchain.node.version")
print(f"    toolchain.node.version layer count: {len(tv['layers'])}")
r_beta = J.evaluate(doc_c, prov_c, env={"FEATURE_BETA": "1"})
r_nobeta = J.evaluate(doc_c, prov_c, env={})
check("toolchain.node.version resolves under FEATURE_BETA", "toolchain.node.version" in r_beta.effective)
check("toolchain.node.version resolves without FEATURE_BETA", "toolchain.node.version" in r_nobeta.effective)
print(f"    with FEATURE_BETA: {r_beta.effective.get('toolchain.node.version')!r}")
print(f"    without:           {r_nobeta.effective.get('toolchain.node.version')!r}")

print()
print("=" * 70)
print("5. MISSING REFERENCE -- migration.fallback_endpoint style (edge-cases)")
print("=" * 70)
# Real fixture check: does conditional-includes.pfcfg's tree contain a bare
# required env var referenced by another key (migration.* from examples.py)?
mig_keys = [k for k in doc_c["keys"] if k.startswith("migration.")]
print(f"    migration.* keys present: {mig_keys}")
if mig_keys:
    r_mig = J.evaluate(doc_c, prov_c, env={})
    for k in mig_keys:
        status = "effective" if f"migration.{k.split('.')[-1]}" in r_mig.effective else "?"
    print(f"    migration effective: { {k: r_mig.effective.get(k) for k in mig_keys} }")
    print(f"    migration failures:  { [f for f in r_mig.failures if f.key and 'endpoint' in (f.key or '')] }")

# Synthetic but precise missing-reference case, built the same way as the
# dual-branch fixture above -- a ref to a path that is not in doc["keys"]
# at all.
doc_missing = {"keys": {"a.uses_ghost": {"layers": [{"value": {"type": "ref", "path": "does.not.exist"}}]}}}
prov_missing = {"a.uses_ghost": [{"file": "f.pfcfg", "line": 1, "section": "a", "key": "uses_ghost"}]}
r_missing = J.evaluate(doc_missing, prov_missing, env={})
check("missing reference -> failure, not in effective",
      "a.uses_ghost" not in r_missing.effective and len(r_missing.failures) == 1)
check("missing reference reason wording matches legacy convention",
      "key not found" in r_missing.failures[0].reason)
print(f"    reason: {r_missing.failures[0].reason!r}")

print()
print("=" * 70)
print("6. ZERO-SURVIVING-LAYER -- globex.json deploy.strategy under non-production")
print("=" * 70)
doc_g, prov_g = load("globex")
r_g_nonprod = J.evaluate(doc_g, prov_g, env={})
r_g_prod = J.evaluate(doc_g, prov_g, env={"PRODUCTION": "true"})
check("deploy.strategy ABSENT (not failed) under non-production -- zero survivors, top-level, no error",
      "deploy.strategy" not in r_g_nonprod.effective and
      not any(f.key == "strategy" and f.section == "deploy" for f in r_g_nonprod.failures))
check("deploy.strategy PRESENT under production", r_g_prod.effective.get("deploy.strategy") == "manual")

# Now force a REFERENCE to a zero-surviving-layer key -- this must be a
# hard failure with the exact "no surviving layer" wording, not a skip.
doc_zref = {
    "keys": {
        "a.refs_strategy": {"layers": [{"value": {"type": "ref", "path": "deploy.strategy"}}]},
        "deploy.strategy": doc_g["keys"]["deploy.strategy"],
    }
}
prov_zref = {
    "a.refs_strategy": [{"file": "f.pfcfg", "line": 9, "section": "a", "key": "refs_strategy"}],
    "deploy.strategy": prov_g["deploy.strategy"],
}
r_zref = J.evaluate(doc_zref, prov_zref, env={})
check("referencing a zero-surviving-layer key IS a failure",
      "a.refs_strategy" not in r_zref.effective and len(r_zref.failures) == 1)
check("zero-surviving-layer reason wording matches legacy convention",
      "no surviving layer" in r_zref.failures[0].reason)
print(f"    reason: {r_zref.failures[0].reason!r}")

print()
print("=" * 70)
print("7. ENV OPACITY -- a raw env value containing $(...) syntax must not be rescanned")
print("=" * 70)
doc_op = {"keys": {"a.opaque": {"layers": [{"value": {"type": "env", "var": "WEIRD"}}]}}}
prov_op = {"a.opaque": [{"file": "f.pfcfg", "line": 1, "section": "a", "key": "opaque"}]}
weird_val = "literally $(build.node_version) and ${GIT_SHA}"
r_op = J.evaluate(doc_op, prov_op, env={"WEIRD": weird_val})
check("env value with $(...) text returned verbatim, not rescanned",
      r_op.effective.get("a.opaque") == weird_val)

print()
print("=" * 70)
print("8. CROSS-CHECK AGAINST legacy_evaluator.py ON THE REAL .pfcfg TREES")
print("=" * 70)
# This is the actual equivalence spot-check: resolve the SAME real entry
# through both evaluators (JSON side reads the regenerated JSON, legacy
# side re-parses the raw .pfcfg text) and confirm the effective settings
# and failing-key sets agree, for both fixtures already locked in earlier
# sessions.
CI_PRODUCTION_FULL = {"CI": "true", "PRODUCTION": "true"}
for name, entry_path in [
    ("acme-corp", "../../../../starter/configs/customers/acme-corp/pipeline.pfcfg"),
    ("globex", "../../../../starter/configs/customers/globex/pipeline.pfcfg"),
    ("initech", "../../../../starter/configs/customers/initech/pipeline.pfcfg"),
    ("interpolation-cascade", "../../../../starter/configs/edge-cases/interpolation-cascade.pfcfg"),
]:
    doc_x, prov_x = load(name)
    stream = R.resolve_entry(entry_path)
    grouped = R.group_by_key(stream)
    for env_name, env in [("ci-production", CI_PRODUCTION_FULL), ("non-ci", NON_CI)]:
        json_result = J.evaluate(doc_x, prov_x, env)
        legacy_result = L.evaluate(grouped, env)
        same_effective = json_result.effective == legacy_result.effective
        json_fail_keys = {(f.section, f.key) for f in json_result.failures}
        legacy_fail_keys = {(f.section, f.key) for f in legacy_result.failures}
        same_failures = json_fail_keys == legacy_fail_keys
        check(f"{name} / {env_name}: effective settings match legacy_evaluator.py", same_effective)
        check(f"{name} / {env_name}: failing-key sets match legacy_evaluator.py", same_failures)
        if not same_effective:
            only_json = {k: v for k, v in json_result.effective.items() if legacy_result.effective.get(k) != v}
            only_legacy = {k: v for k, v in legacy_result.effective.items() if json_result.effective.get(k) != v}
            print(f"    DIFF ({name}/{env_name}) json-only/differing: {only_json}")
            print(f"    DIFF ({name}/{env_name}) legacy differing:    {only_legacy}")
        if not same_failures:
            print(f"    DIFF ({name}/{env_name}) json failures:   {json_fail_keys}")
            print(f"    DIFF ({name}/{env_name}) legacy failures: {legacy_fail_keys}")

print()
print("=" * 70)
if FAIL == 0:
    print("ALL CHECKS PASSED")
else:
    print(f"{FAIL} CHECK(S) FAILED")
    sys.exit(1)