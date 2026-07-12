# Ariadne Engineering Backlog

## Planned

### Secret Sanitisation

Priority: Medium

Problem:
Ariadne currently preserves source content verbatim. Documents containing temporary signed URLs or credentials may cause GitHub Secret Scanning alerts.

Planned Solution:
Introduce a sanitisation stage before Processed documents, Review files and library.json are written. Detect and replace recognised secrets with descriptive placeholders while preserving document meaning.

Status:
Deferred