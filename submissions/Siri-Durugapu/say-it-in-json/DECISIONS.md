# Decisions

## Schema: flat `keys` map, not a nested tree

`schema.json` is `{"keys": {"<section.key>": {"layers": [...]}}}` — flat,
keyed by the full dotted path, not a tree mirroring sections. A
`$(section.key)` reference is already that exact dotted string, so a flat
map makes lookup a direct `keys[path]`, no reconstruction. A nested tree
would have to decide where section ends and key begins for every path —
genuinely ambiguous in the real corpus (`[cascade]` and `[cascade.loop]`
coexist as sibling sections in `interpolation-cascade.pfcfg`). Each layer
is `{"value": <node>, "condition"?: [...]}` — every place across the
include graph that assigned the key, depth-first, oldest first.

## Includes & conditionals

Includes flatten at conversion time (one ordered layer list per key), not
preserved as a file graph — traded for a simpler evaluator, at the cost of
round-tripping back to original layout. Depth-first walk; plain
`@include` always re-walks; `@include_once` consults/populates a "seen"
set that **forks** at each `@ifdef`/`@ifndef` body (copy in, discarded on
exit), so mutually-exclusive branches can each `@include_once` the same
path without contaminating each other. An include inside a conditional
tags every layer it contributes with that condition, ANDed with whatever
is already active. Survival: `ifdef VAR` = present and non-empty;
`ifndef VAR` = absent or empty.

## Interpolation

`${VAR}` / `${VAR:-default}` / `${VAR:+alt}` / `$(section.key)` parse into
a `literal | env | ref | concat` node union via recursive descent, not
regex — required because defaults/alts can nest further `${...}`/`$(...)`
(e.g. `${TAG:-$(node_version)-${SHA:-dev}}`). A raw, already-set env value
is **opaque**: returned as-is, never rescanned.

## Effective settings, references, circularity

A key's value = its **last surviving layer** in existing depth-first
order — last-write-wins. Zero surviving layers = absent from effective
settings (not a failure), unless something reaches it via `$(...)`, which
is then a hard failure (`"no surviving layer"`, distinct from
`"key not found"` for a path missing from `keys` entirely). Circular
references are detected per top-level key with a fresh visiting-chain
each time, so `cascade.loop.a`/`.b` each get their own entry. Depth
increments once per key-hop (`MAX_DEPTH = 100`), not per node-tree
descent within one key.

## Two independent evaluators & what equivalence proves

`legacy_evaluator.py` resolves raw `.pfcfg` text directly;
`json_evaluator.py` resolves the converted node tree. Neither imports the
other — independence is the point. Shared only as a **spec agreement**:
`MAX_DEPTH`, failure wording, result shapes. `equivalence_verifier.py`
diffs both against the same real entry + environment.
**Result: 10/10 equivalent** across all 5 real starter entries × 2 locked
fixtures (`ci-production`, `non-ci`).

**Proves:** the resolver's flattening and the schema's node encoding lost
nothing for behavior actually exercised by the starter corpus, under
those two fixtures. **Does not prove:** correctness for `.pfcfg`
constructs outside the starter set, or other environments — it's evidence
from real data, not a formal proof over the whole grammar.

## Unmigratable report scope

Two phases: `from_conversion()` (env-independent — value can't be
represented as a node at all) and `from_evaluation()` (env-dependent —
circular/missing reference, zero-surviving-layer, depth overflow, under a
fixture). On the real corpus: 0 conversion-time findings, 2
evaluation-time (`cascade.loop.a`/`.b`, merged into one row each listing
both fixtures since they're env-independent). `migration.api_endpoint`
resolving to `""` when its required env var is unset is **not** flagged —
that matches legacy behavior exactly (equivalence-confirmed), so it's a
correctly migrated edge case, not an unmigratable one.

## Known limitations

- Equivalence is real-data evidence, not a formal proof over the full
  grammar or arbitrary environments.
- List/array-valued settings never appeared in the starter corpus, so the
  schema has no locked representation for them.
- `MAX_DEPTH` is duplicated by hand in both evaluators; an edit to one
  without the other would silently break the equivalence guarantee.

## Next steps

- Stress-test the converter against `.pfcfg` trees outside the starter
  corpus.
- If array values are needed, extend schema + both evaluators together,
  then re-run equivalence verification before trusting it again.
