---
kind: project-checkpoint
project: Ariadne
date: 2026-08-22
stage: 1-personality
status: accepted-baseline
---

# Identity Kernel v1.1.0 checkpoint

Identity Kernel v1.1.0 is the accepted Ariadne personality baseline. The previous
`Ariadne Identity Kernel v1.0.0.md` remains available as the explicit rollback
target.

## Evidence recorded

- The identity/personality regression suite was rerun: 6 tests passed.
- Planner/user-facing personality separation was verified by the test suite.
- The personality was manually exercised through conversational prompts covering
  casual conversation, hidden assumptions, contradictions, technical diagnosis,
  simple tasks, Vault synthesis, planner isolation, and serious topics.
- No Stage 2 world-model work has begun.
- Chat-memory persistence is the next implementation stage; it is not part of
  this personality checkpoint.

## Checkpoint scope

This checkpoint contains only the accepted v1.1 personality implementation,
its regression fixture and tests, the active v1.1 kernel, and this record. It
does not include unrelated catalogue, Home UI, retrieval, or other work.

