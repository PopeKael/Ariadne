# Engineering Documentation Strategy

**Decision Date:** 2026-07-06

## Purpose

This document defines how Ariadne engineering documentation is organised.

The objective is to preserve not only the final implementation, but also the reasoning that led to each decision. Future development should be understandable without relying on memory or external conversations.

---

# Decision

Ariadne will document both the software and the engineering process used to build it.

Documentation is treated as a first-class engineering artefact rather than an afterthought.

---

# Documentation Structure

## Architecture

The Architecture folder contains visual designs together with a matching markdown document explaining:

- Why the design was created.
- What problem it attempted to solve.
- What changed from previous versions.
- Lessons learned.
- Historical context.

Images should never exist without documentation.

---

## Chronicle

The Chronicle records each engineering checkpoint.

Each chronicle summarises:

- work completed
- important discoveries
- problems encountered
- decisions made
- next objectives

The Chronicle explains what happened during development.

---

## Decisions

The Decisions folder records long-term engineering decisions.

These are decisions expected to remain valid across multiple checkpoints.

Examples include:

- documentation standards
- repository organisation
- naming conventions
- engineering workflow
- architectural principles
- development philosophy

Decision documents explain why something is done a particular way.

---

## Releases

The Releases folder records significant public milestones.

Release notes describe:

- completed features
- major improvements
- compatibility notes
- migration notes
- known limitations

Releases represent deliverables rather than work sessions.

---

# Naming Convention

Where practical, engineering documents use:

YYYY-MM-DD - Title.md

This provides natural chronological ordering while remaining readable.

---

# Public vs Private

The public GitHub repository contains:

- engineering documentation
- architecture
- project history
- repository structure

Private knowledge remains local inside the KnowledgeVault.

Examples include:

- Inbox
- Journal
- Archive
- Processed material
- Personal knowledge

The public repository explains how Ariadne is built.

The private vault contains the knowledge Ariadne works with.

---

# Guiding Principle

Every significant engineering decision should leave behind enough documentation that another engineer, or a future version of ourselves, can understand:

- what changed,
- why it changed,
- and what problem it solved,

without needing to reconstruct the original conversation.

Engineering documentation is part of the product.