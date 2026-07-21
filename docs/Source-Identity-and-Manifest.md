# Source Identity and Manifest

## Purpose

This document defines the deterministic identity rule for imported source Markdown. It applies to the source corpus in `Inbox/`, `Processed/`, and `Failed/`. Folder location is workflow state, never source identity.

## Canonical content normalisation

For a source file, the canonical content string is produced as follows:

1. Decode the file as UTF-8, accepting and removing an initial UTF-8 BOM. A decoding failure is a source-read failure.
2. Convert every CRLF sequence to LF.
3. Remove trailing whitespace from every line. Internal whitespace, line order, and Unicode characters are otherwise preserved.
4. Remove trailing whitespace and blank lines from the complete result.

The canonical content SHA-256 is the lowercase hexadecimal SHA-256 of that UTF-8 canonical string.

## Stable source ID

The source ID is selected in this order:

1. `youtube:<video-id>` when the extracted source URL contains a valid YouTube video ID.
2. `url:<canonical-url>` when front matter supplies a valid external source URL. URL canonicalisation lowercases scheme and host, removes a fragment, and preserves the path and query string.
3. `sha256:<canonical-content-hash>` when no verified external identity is available.

The manifest rejects duplicate source IDs and duplicate canonical content hashes. A conflict is reported; no file is changed or silently discarded.

## Manifest fields

Each manifest record contains the stable source ID, canonical content hash, current relative path, workflow state, source type, title, canonical URL/external identity, file size, modification timestamp, and parse/metadata warnings.

The manifest is derived state. It is deterministic for unchanged source files and is not a replacement for source Markdown.
