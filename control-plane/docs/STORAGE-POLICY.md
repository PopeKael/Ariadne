# Storage policy

Storage is finite and is part of the architecture, not an afterthought.

| Location | Allowed role |
|---|---|
| C: | Windows, applications, system files, small caches |
| D: | Durable data, repositories, Knowledge Vault, backups |
| E: | Video editing and media processing |
| F: | AI models under `F:\AI`, one Ubuntu WSL environment, active Linux tooling, builds, databases, scratch |

## Guardrails

- Ubuntu WSL capacity ceiling: 128 GB.
- AI model repositories must use explicit paths under `F:\AI`.
- Temporary processing must use declared scratch locations.
- Large downloads require a visible target path and estimated size.
- The dashboard must display free space before approving a large job.
- Cleanup must distinguish cache, rebuildable artefact, source, model, and personal data.
- No automatic deletion of models, repositories, or user data.

