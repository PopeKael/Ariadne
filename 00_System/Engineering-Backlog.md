# Ariadne Engineering Backlog

This document records engineering improvements that have been identified but are not yet scheduled for implementation.

---

## Planned

### Secret Sanitisation

**Priority:** Medium

**Status:** Planned

**Problem**

Ariadne currently preserves source content verbatim. Documents containing temporary signed URLs, API keys, authentication tokens, or other credentials may be written into Processed documents, Review files, and `library.json`. This can trigger GitHub Secret Scanning and unnecessarily retain transient transport metadata.

**Proposed Solution**

Introduce a sanitisation stage before documents are written to persistent storage.

The sanitiser should:

- Detect recognised secret formats and signed URLs.
- Replace detected secrets with descriptive placeholders.
- Preserve the semantic meaning of the original document.
- Prevent credentials from being committed to the repository or knowledge graph.

**Notes**

- Triggered by GitHub Secret Scanning alert on a Perplexity-generated AWS S3 presigned URL.
- This is considered an engineering improvement rather than an emergency security incident because the exposed credential belongs to a third-party service and is temporary.
- Repository history should eventually be scrubbed once the sanitisation feature has been implemented.

---

## Completed

*(None)*

---

## Deferred

*(None)*

---

## Future Ideas

*(None)*