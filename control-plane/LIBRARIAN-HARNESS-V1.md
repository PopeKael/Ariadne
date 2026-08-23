# Ariadne Librarian Harness v1

Planner v2 keeps the existing Home integration seam but separates semantic interpretation from controller policy.

## Responsibility mapping

| Existing field | Semantic Interpreter | Policy Resolver / controller |
| --- | --- | --- |
| `intent` | Interprets the user's task | Carries it into the execution plan |
| `primary_source` | No longer generated | Derived from attachment, Vault mode, current need, and runtime state |
| `tools` | No longer generated | Selected from available and selected capabilities |
| Vault decision | `needs_personal_history` | `auto`, `always`, `never`, Vault availability |
| Current decision | `needs_current_information` | Capability gaps remain explicit; the semantic fact is not falsified |
| Heavy-model decision | `reasoning_complexity` | Maps to a reasoning tier; model selection remains separate |
| `tasks` | No longer generated | Bounded deterministic execution operations |
| `confidence` | Confidence in the interpretation | Preserved as telemetry/plan metadata |

The semantic contract is intentionally compact: `intent`, three requirement flags,
`reasoning_complexity`, `ambiguity`, and numeric `confidence`.

## Events

The Home request path appends bounded JSONL records to
`control-plane/runtime/librarian-events.jsonl` by default. The path can be changed
with `ARIADNE_LIBRARIAN_EVENTS_PATH`. The stream records semantic interpretation,
policy resolution, final execution plan, and fallback errors. Logging failures do
not interrupt a request and events are not written to the Vault.

## Evaluation

`evaluation/planner_cases.json` remains unchanged. The runner now records semantic
interpretation fields and scores them separately from the controller-normalized
final route. The frozen Vault-`never` cases retain their original expected objects;
the evaluator normalizes the forbidden Vault primary source only at the policy
boundary.

The old `semantic_planner.py` contract remains available for compatibility while
Home and the bakeoff runner use `librarian_harness.py`.

## Selected local interpreter

The default Semantic Interpreter is `qwen3.5:9b-q4_K_M`, retained resident with
`keep_alive=-1`. It is the smallest tested model that met the v2 acceptance bar:
54/60 final routes, 60/60 valid semantic responses, zero fallbacks, and zero
controller constraint violations. Smaller tested candidates reached at most
53/60 final routes (`qwen3.5:4b-q4_K_M`) or lower.
