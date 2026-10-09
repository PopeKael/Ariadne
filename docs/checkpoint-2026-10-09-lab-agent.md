# The Lab coding-agent checkpoint — 2026-10-09

The Lab now uses one bounded model → action → environment → observation loop.
Mandatory Task Contract, acceptance-planner, contract-revision and reviewer stages
have been removed from the agent core. The existing Chat toggle, model lifecycle,
Workbench, sandbox, saved builds and metrics remain in place. The selected model
is configuration, not controller logic. No additional model was installed.

Actions are read, write/create, exact patch, run, test and finish. Edits require a
read of the current file revision. Deterministic source/browser checks return real
failures; the model chooses its next action. One browser test action uses a fresh
page with sequential assertions sharing state. Finish requires current passing
execution evidence. Step, repair, format, no-progress, context and time limits
stop unsuccessful runs explicitly while retaining candidates and trajectory.

This checkpoint also includes the accumulated Lab infrastructure: separate
Windows-host-owned full-screen Edge Runner with foreground acknowledgement,
visible progress and connection activity, and canonical Chat import, reopening
and Save to Inbox. Workbench dimensions and normal Chat behaviour are preserved.
About now describes the workflow and credits mini-SWE-agent and Aider.

## Verification

- 74 focused Python regressions passed (5.972 s at checkpoint); 19 native Windows host tests passed (41.77 s). JavaScript syntax and Git whitespace checks passed. The live HTTPS About page was inspected in Opera with the new milestone and reference credits.
- Live configured qwen2.5-coder:14b, 32768 context: write → run → test → finish,
  four model calls, zero repairs, 76.500 s wall, 73.594 s model, 2.844 s tools.
  Provider counts total 5989 input / 959 output tokens across all calls.
- Four model-selected browser assertions passed. Manual Opera Preview confirmed
  counter 0 → 1 → 2 → 0 and Save to Inbox succeeded.
- Scripted-model repair fixture exercised the real compiler/browser, caught syntax
  and runtime failures, preserved two patches and passed three sequential checks.
  This proves controller/environment repair handling, not live-model repair skill.
- A deliberately broken fixture remained NEEDS ATTENTION with retained evidence.
  Conservative context preflight stopped it explicitly at CONTEXT_BUDGET.

See [validation report](lab-agent-v4-validation.md), [architecture](lab-agent-architecture.md)
and [reference comparison](lab-agent-reference-comparison.md). Earlier failed trial
receipts remain labelled historical evidence. Browser tests do not prove arbitrary
application completeness; model test selection remains a practical limitation.

## Publication boundary

Only reviewed Lab implementation, tests, documentation and bounded verification
receipts are included. Models, generated project storage, Edge profiles, private
runtime logs and the KnowledgeVault Inbox test export are outside this checkpoint.
GitHub source publication does not deploy NAS services or alter Ollama.
The commit and remote parity are verified after publication and reported in the
checkpoint handoff; this file deliberately does not embed its own commit hash.
