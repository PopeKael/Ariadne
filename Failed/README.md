# Failed

## Purpose

The **Failed** folder contains files that could not be successfully processed by Ariadne.

Rather than discarding these files, Ariadne moves them here for investigation, correction, and possible reprocessing.

This ensures that no captured knowledge is lost due to processing errors.

## Typical reasons for failure

Files may appear here for a variety of reasons, including:

- Unsupported file formats
- Corrupted or incomplete files
- OCR failures
- Metadata extraction errors
- Parsing or conversion problems
- Missing dependencies
- Unexpected processing exceptions

## Workflow

Files placed in this folder should be reviewed to determine the cause of the failure.

Once the issue has been resolved, the file may be:

- Reprocessed through the ingestion pipeline.
- Returned to the Inbox for another processing attempt.
- Archived if no further action is required.
- Removed if the file is invalid or no longer needed.

## Design Principle

Ariadne is designed to preserve information whenever possible.

Processing failures should never result in silent data loss.

Every failed file represents an opportunity for the system to improve and for the knowledge to be recovered.

---

**In short:**

The **Failed** folder is Ariadne's recovery queue, ensuring that no captured knowledge is forgotten simply because something went wrong during processing.