# Failed ingestion audit

Generated: 2026-08-24T03:50:59.224709+00:00

## Scope

- Rebuild-v1 rejected records: 5
- Current `Failed/` Markdown files: 109

## Classification

- `manual-review`: 1
- `retry-safe`: 4

## Confirmed findings

- All 67 records received an Ollama response envelope and have no recorded transport error.
- The adapter POSTs to /api/chat and reads response.message.content; it does not use /api/generate.
- Twelve captured message.content values are malformed/truncated JSON; no Markdown fences were observed in those captures.
- The JSON schema permits any string for proposed_domains even though semantic validation requires the controlled vocabulary; this is a pipeline defect behind the 15 invalid_domain_proposal rejections.
- The active catalogue contains none of the failed stable IDs or canonical content hashes.
- One 70-byte untitled source is empty/metadata-only after front matter and is obsolete intake material.

## Not observed / not proven

- Malformed HTTP request payloads, unsupported endpoint/format errors, transport timeouts, encoding/read failures, duplicate source identities, multiple JSON objects, concurrent-run evidence, or partial source moves.
- A fixed context-limit cause cannot be proved from the captures alone; malformed JSON ends mid-object and is consistent with truncated final generation.

## Per-source findings

| File | Type | Size | Failure stage | JSON | Classification | Recommended action |
|---|---:|---:|---|---|---|---|
| `Failed/Islam Conflict Analysis2026-08-24T09_54_17+07_00.md` | chatgpt | 14068 | semantic_validation | yes | `manual-review` | Leave in Failed for human relevance review; the model returned structurally valid but semantically insufficient enrichment. |
| `Failed/My Local AI System - Feature Creep Summary2026-08-24T09_55_28+07_00.md` | chatgpt | 13749 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/Okay Ariadne give me 10 good ideas for my main video channel_80c37039.md` | markdown | 4832 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/Sing Sample Recording Tips2026-08-22T08_29_28+07_00.md` | chatgpt | 9556 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |
| `Failed/Smart-glasses Privacy Watch2026-08-24T09_54_55+07_00.md` | chatgpt | 10652 | model_final_output | no | `retry-safe` | A fresh, isolated retry is safe; the source is nonempty and the Ollama envelope was received but message.content was empty. |

The JSON companion contains hashes, timestamps, exact failure messages, capture references, and response diagnostics for every row.
