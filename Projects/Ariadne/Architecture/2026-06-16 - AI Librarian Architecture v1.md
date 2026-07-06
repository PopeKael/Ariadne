# AI Librarian Architecture v1

## Overview

A description of what this architecture represents, where it sits in the evolution of the project, and why it was created.

## Background

What discussions led to this design?

What problems were we trying to solve?

What previous assumptions proved inadequate?

Why did we decide a new architecture was necessary?

## Design Philosophy

Explain the thinking that shaped the architecture.

This section should capture the conceptual reasoning rather than describing individual boxes in the diagram.

For this architecture, the primary shift was moving away from the idea of a single AI assistant toward the concept of a trusted AI Librarian responsible for orchestrating many specialised intelligences.

## Major Architectural Concepts

Describe every major section of the diagram.

### User Interaction

Why this layer exists.

Its responsibilities.

How it separates the human from the underlying AI systems.

### Discovery & Intelligence Layer

Explain the purpose of the Scout, Examiner, Archivist, Translator and Monitor.

Describe why these responsibilities were separated.

Explain how continuous discovery differs from static configuration.

### AI Capability Atlas

Describe why knowledge about AI systems is treated as a first-class knowledge base.

Explain capability mapping, pricing, performance, best practices and historical tracking.

### Intent Analysis

Explain how user requests become execution plans.

Discuss planning versus execution.

### Execution & Routing

Describe the reasoning behind treating AI providers as interchangeable execution engines rather than primary decision makers.

### Evaluation

Explain why returned answers should themselves be analysed before presentation.

Discuss quality, cost, confidence and comparison.

### Synthesis

Explain why Ariadne owns the final response rather than forwarding raw AI output.

### Feedback Loop

Describe how user feedback continuously improves future routing decisions.

## Key Insights

List the major discoveries that emerged during development.

These are not implementation details.

These are conceptual breakthroughs.

For example:

- AI models are tools, not the librarian.
- Knowledge should remain independent of AI providers.
- Routing is a core capability.
- Evaluation should occur after execution rather than before.

## What Changed From Earlier Thinking

Describe how this architecture differed from earlier conversations.

Explain which ideas were discarded, refined or retained.

## Limitations

Looking back, what weaknesses existed in this design?

What assumptions later proved incorrect?

Which components were later replaced?

## Legacy

Describe what survived into later versions.

Explain why this architecture remains historically important even though later versions superseded it.

## Related Documents

Reference later architecture diagrams.

Reference important design decisions.

Reference relevant Chronicle entries.

## Historical Notes

This architecture represents the first complete expression of Ariadne as an AI Librarian rather than a conversational AI.

Although many implementation details evolved, this document marks the point at which the project's identity became clear.

It is retained as a historical design milestone and forms part of Ariadne's design provenance.