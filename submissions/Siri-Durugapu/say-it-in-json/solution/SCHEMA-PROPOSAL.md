# Target JSON schema — proposal

Formal schema: `schema.json` (JSON Schema, draft 2020-12). Validated against seven worked
examples pulled directly from the starter configs (`examples.py`) — all pass, and five
deliberately-malformed variants are correctly rejected. This doc explains the shape and
ties each piece back to the decision it came from.

## Top-level shape

```json
{
  "entry": "customers/acme-corp/pipeline.pfcfg",
  "keys": {
    "deploy.strategy": { "layers": [ ... ] },
    "deploy.target": { "layers": [ ... ] }
  }
}
```

One JSON document per entry config, includes already flattened in (locked earlier:
flattening at conversion time, not mirroring the include graph as separate files — simpler
for the evaluator, in exchange for divergence from original file layout, noted as a
round-trip tradeoff for `DECISIONS.md`).

**New call being made here, not explicitly settled before:** `keys` is a **flat map**
keyed by the full dotted `section.key` path (e.g. `"toolchain.node.version"`), not a
nested tree mirroring section structure. Reasoning: a `$(section.key)` reference is
already written as that exact dotted string, so a flat map means the evaluator's lookup
is a direct `keys[path]` with zero reconstruction. A nested tree would need to decide,
for every dotted path, where the section ends and the key begins — genuinely ambiguous
in JSON, since section names are themselves dotted (`[toolchain.node]`, `[cascade.loop]`).
The flattening rule that resolves this (last segment = key, everything before = section)
is the same rule `$(a.b.c)` already uses, so the flat-map form just reuses it instead of
re-deriving a tree-building rule. Flag this if you want the nested form instead — it's a
real fork, not something forced by anything decided earlier.

## `keyDef.layers` — the last-write-wins mechanism

```json
"deploy.requires_approval": {
  "layers": [
    { "value": { "type": "env", "var": "DEPLOY_APPROVAL", "default": {"type":"literal","text":"true"} } },
    { "value": { "type": "literal", "text": "true" } },
    { "condition": [{ "type": "ifdef", "var": "ACME_DEPLOY_TARGET" }],
      "value": { "type": "literal", "text": "false" } }
  ]
}
```

This is the acme `[deploy]` collision, traced end to end earlier: `container-publish.pfcfg`
→ `staging.pfcfg` → `pipeline.pfcfg`'s own body → the `@ifdef` block, in depth-first order.
Evaluator contract: walk in array order, skip any layer whose `condition` fails for the
given environment, keep the **last** surviving layer. That's the whole mechanism —
depth-first-flatten-then-last-write-wins, replayed rather than re-derived.

**Conditional-include propagation**, checked against `globex/pipeline.pfcfg`'s
`on-prem.pfcfg` (included only under `@ifdef PRODUCTION`, but containing zero `@ifdef` of
its own):

```json
"deploy.strategy": {
  "layers": [
    { "condition": [{ "type": "ifdef", "var": "PRODUCTION" }],
      "value": { "type": "literal", "text": "manual" } }
  ]
}
```

The condition is attached to the layer even though nothing in `on-prem.pfcfg`'s own text
mentions `PRODUCTION` — this is the bug we caught earlier (naive file-by-file flattening
would emit this as unconditional) and the fix (propagate the include site's own condition
onto every layer that file contributes) is encoded here structurally, not left as
converter-logic that could be gotten wrong silently.

## `condition` — array of predicates, ANDed

```json
"condition": [{ "type": "ifdef", "var": "CI" }]
```

No wrapper object (dropped after your pushback on the earlier `{"all": [...]}` version) —
array length alone expresses nesting; a two-element array is two `@ifdef`/`@ifndef`
wrapped inside each other, ANDed by construction since the legacy grammar has no OR form.

## `node` — literal / env / ref / concat, one recursive shape

This is the piece that has to handle everything from a plain literal to the three-level
`container.tag` expression. One discriminated union, used both as a layer's `value` and
recursively inside `env.default`, `env.alt`, and `concat.parts` — same node-walking logic
everywhere, which is exactly what makes the evaluator's reference-resolution able to
recurse into a `concat` or an `env.default` without a separate code path for each.

**Plain literal** — no ceremony:
```json
{ "type": "literal", "text": "acme-corp" }
```

**Env interpolation**, three legacy forms mapped onto one node type:
```json
{ "type": "env", "var": "NODE_VERSION", "default": { "type": "literal", "text": "20" } }   // ${VAR:-default}
{ "type": "env", "var": "CI", "alt": { "type": "literal", "text": "ci-" } }                  // ${VAR:+alt}
{ "type": "env", "var": "REQUIRED_API_ENDPOINT" }                                            // ${VAR} bare
```
`default` and `alt` are mutually exclusive (enforced in the schema — a single `${...}`
token is one legacy form or the other, never both) and each recurses into `node`, which is
what makes `${RELEASE_VERSION:-0.0.0-$(build.node_version)}` representable: the default
isn't a string, it's a `concat` of a literal and a `ref`.

**Cross-key reference**:
```json
{ "type": "ref", "path": "build.node_version" }
```
`path` matches the `keys` map's key format exactly (see the flat-map decision above) —
same string, both places.

**Concatenation** — only when a value is genuinely more than one piece (confirmed
empirically: ~23% of the 52 interpolated lines in the starter set, the rest are one clean
node):
```json
{ "type": "concat", "parts": [
  { "type": "ref", "path": "build.node_version" },
  { "type": "literal", "text": "-" },
  { "type": "env", "var": "GIT_SHA", "default": { "type": "literal", "text": "dev" } }
]}
```
This is `acme-corp/pipeline.pfcfg`'s `container.tag` (`${ACME_RELEASE_TAG:-$(build.node_version)-${GIT_SHA:-dev}}`)
in full — three-level nesting, exact encoding, no information lost. `parts` requires
`minItems: 2` deliberately: a single-piece value is never wrapped, it just *is* that node.

## What's deliberately **not** in this schema

**Legacy file/line provenance.** Locked in last turn: kept as separate converter-side
metadata (a sidecar map, same `section.key` keying as `keys`, index-aligned to each key's
`layers` array — e.g. `{"deploy.strategy": [{"file": "templates/container-publish.pfcfg", "line": 24}, {"file": "customers/acme-corp/staging.pfcfg", "line": 3}]}`), not a field on
`layer`. Rationale checked against the assignment: `file`/`line` are required only in the
unmigratable report (item 5), where `line` is explicitly optional — not in the schema
requirement (item 1), which is scoped to representing behavior. Keeping it separate means
the schema stays exactly "what does this config compute," and the evaluator never has a
reason to touch a field it doesn't need.

## Sanity-checked against real cases

| Example | File | What it exercises |
|---|---|---|
| `acme_deploy` | `customers/acme-corp/pipeline.pfcfg` | 3-file layer collision, conditional layer |
| `acme_tag` | same | 3-level nested concat/env/ref |
| `globex_deploy` | `customers/globex/pipeline.pfcfg` | condition propagated from include site, not file's own text |
| `cascade` | `edge-cases/interpolation-cascade.pfcfg` | 4-hop chain, conditional layer mid-chain, the a↔b cycle |
| `initech` | `customers/initech/pipeline.pfcfg` | 3-hop cross-file chain (`bundle_name → version → node_version → toolchain.node.version`) |
| `migration` | `edge-cases/conditional-includes.pfcfg` | bare required env var referenced by another key (locked: resolves normally, no propagated failure) |
| `key_prefix` | `_base/defaults.pfcfg` | two interpolations concatenated in one value (`${CI:+ci-}${CACHE_NAMESPACE:-default}`) |

All seven validate against `schema.json`. Five intentionally-broken variants (env with
both `default` and `alt`, single-item `concat`, value wrapped in a forced array, zero-layer
`keyDef`, bare-object `condition` instead of an array) are all correctly rejected — the
schema enforces the decisions, it doesn't just document them.

## Open before implementation

- Confirm the flat-map `keys` decision (new call above) — or switch to nested-by-section.
- Provenance sidecar shape sketched above but not formalized as its own schema yet — worth
  a pass once the converter's actual output order is known.
