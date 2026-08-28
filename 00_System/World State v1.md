# Ariadne World State v1

World State is a compact, derived routing projection of Warren's known world and the current local moment.

It is not a second Vault, identity store, personality prompt, or answer source. The Markdown Vault, identity files, catalogue, and runtime clock remain authoritative.

## Shape

- `self`: owner parsed from the active Identity Kernel, known handles and aliases, compact People/Entities labels, and catalogue-derived channel/project subjects.
- `now`: local date, local time, timezone, Vault root, and catalogue/Processed counts.
- `request_context`: current request, recent user-message subject, focus terms, matched known subjects, and bounded retrieval guidance.

## Refresh

`00_System/world_state.py` fingerprints the active source files and directories. Home refreshes the derived base snapshot before semantic interpretation. A source change causes a rebuild and atomic replacement of the ignored local cache at:

`00_System/Data/WorldState/world-state-v1.json`

The request overlay is never persisted. It exists only for the current planning/retrieval path.

## Separation rule

The Identity Kernel remains a separate prompt block and is always supplied to user-facing synthesis. Planner-facing identity guidance is restrained operational guidance. World State resolves references and guides history search; it does not override personality, policy, evidence, or user instructions.
