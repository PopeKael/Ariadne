# Ingestion Failure Analysis

Analysis date: 2026-07-14

The 140 legacy failed files were queued for retry after the ingestion validator was made tolerant of recoverable schema drift.

| Observed failure | Count | Root cause | Remediation |
|---|---:|---|---|
| Duplicate/contradictory `secondary_domains` | 95 | Model returned the primary domain again or repeated domains | Canonicalise, deduplicate, remove primary, cap at three |
| Parsed response is null | 25 | Empty/non-object model response | Existing retry path; schema normalisation now handles valid partial objects |
| Invalid JSON | 12 | Model formatting failure | Existing repair retries; strict fallback remains |
| Too many `secondary_domains` | 10 | Model exceeded schema limit | Truncate to three after canonicalisation |
| Missing `map_entry` | 5 | Partial model response | Safe review placeholder |
| Non-string `links` | 3 | Malformed model array | Recoverable arrays default to empty |
| Missing `reason` | 2 | Partial model response | Still requires a meaningful reason; remains retry-visible |

The dominant issue was validator brittleness, not source-file corruption. Failed files are now managed by `00_System/Retry-FailedIngestion.ps1` and are pending normal ingestion retries.
