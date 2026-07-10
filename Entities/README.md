# Entities

## Purpose

The **Entities** folder contains records representing identifiable people, places, organisations, technologies, products, events, and other named objects discovered within the KnowledgeVault.

Entities form the foundation of Ariadne's knowledge graph by connecting information across multiple sources and Wiki articles.

Think of this folder as Ariadne's card catalogue of "things."

## Typical entities

Examples include:

- People
- Organisations
- Companies
- Products
- Technologies
- Programming languages
- Countries
- Cities
- Events
- Books
- Software
- Hardware

## Examples

Examples of entity records include:

- OpenAI
- Ollama
- Docker
- Obsidian
- Synology
- NVIDIA
- Windows 11
- Thailand

Each entity may appear in dozens of different documents throughout the KnowledgeVault while maintaining a single canonical record.

## Purpose within Ariadne

Rather than storing knowledge itself, entities provide a consistent identity that can be referenced from multiple sources.

This enables Ariadne to:

- recognise when different documents refer to the same thing
- build relationships between knowledge
- reduce duplication
- support graph-based navigation
- improve semantic search and retrieval

## What does *not* belong here?

This folder should not contain general notes or articles.

Descriptions, tutorials, research, and documentation belong in the Wiki.

Entities exist to identify and connect knowledge, not replace it.

## Design Principle

Knowledge describes things.

Entities identify things.

Keeping these responsibilities separate allows Ariadne to build an increasingly rich knowledge graph while maintaining a single source of truth for each identifiable object.

---

**In short:**

If the Wiki contains the stories, **Entities** contains the cast of characters.