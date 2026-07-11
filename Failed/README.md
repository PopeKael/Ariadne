# Processed

## Purpose

The **Processed** folder contains files that have successfully completed Ariadne's ingestion and initial processing pipeline.

These files have been captured, analysed, indexed, and enriched with metadata, making them ready for review, verification, and knowledge extraction.

This folder represents Ariadne's working library rather than its permanent knowledge base.

## Typical contents

Examples include:

- Processed Markdown documents
- OCR output
- Extracted metadata
- AI-generated summaries
- Converted source material
- Normalised documents
- Processing manifests

## Workflow

Documents typically move through the following lifecycle:

1. Captured in the **Inbox**.
2. Processed and converted into a standard format.
3. Stored temporarily in **Processed**.
4. Reviewed and verified for quality.
5. Extracted into the Wiki and other KnowledgeVault structures.
6. Archived or retained as required.

The Processed folder acts as the staging area between raw information and curated knowledge.

## What does *not* belong here?

This folder should not contain permanent Wiki articles or original source documents.

It exists to support the processing pipeline and should contain only intermediate working material.

## Repository

The contents of this folder are intentionally excluded from the public repository.

Only this README file is included so that the directory structure is preserved when the project is cloned.

Each user's KnowledgeVault will populate this folder automatically as documents are processed.

## Design Principle

Ariadne separates the processing of knowledge from the knowledge itself.

Keeping processed material isolated allows the system to validate, refine, and enrich information before it becomes part of the permanent KnowledgeVault.

---

**In short:**

The **Processed** folder is Ariadne's workbench, where captured information is prepared before becoming trusted knowledge.