# Wazza Home Lab Network Topology

**Created:** 26 June 2026

## Overview

This document records the first complete network topology of the home laboratory that serves as the development environment for Project Ariadne.

Previous architectural documents focused on long-term concepts and future capabilities. This diagram shifts the focus toward implementation by documenting the physical infrastructure available for experimentation and development.

Rather than designing systems in the abstract, Project Ariadne now had a clearly defined hardware platform on which those ideas could be developed, tested and refined.

---

## Background

Following several architecture iterations, it became increasingly apparent that the project could easily become an expensive engineering exercise if every concept depended upon enterprise-class AI hardware.

Rather than chasing the largest available hardware, the decision was made to begin with existing equipment already available within the home laboratory.

This represented an important change in thinking.

Instead of asking:

"How would a large AI company build this?"

The question became:

"How would an experienced systems engineer build this using practical equipment that already exists?"

That philosophy remains central to the project.

---

## Design Philosophy

Throughout my career I have rarely begun projects by purchasing new technology.

Instead, I first develop a clear understanding of the infrastructure already available.

Only after understanding the available resources do I determine where investment will produce the greatest benefit.

The same principle applies to Ariadne.

The hardware exists to serve the architecture.

The architecture does not exist to justify the hardware.

Documenting the network therefore became an essential engineering task rather than simple documentation.

---

## Existing Infrastructure

The home laboratory currently consists of several distinct computing roles.

The Windows workstation provides primary administration, software development, media production and local AI experimentation.

The Linux services host provides a flexible platform for Docker containers, Open WebUI, Ollama, game servers and experimental AI services.

The Synology NAS provides reliable storage, Docker services, Plex, backups and long-term knowledge preservation.

These systems communicate across a simple Gigabit Ethernet network connected through a 3BB fibre internet service with Cloudflare providing public DNS services.

Although modest compared with enterprise environments, the laboratory provides an excellent platform for investigating practical AI deployment strategies.

---

## Engineering Significance

Producing this network diagram achieved several objectives.

It documented the current physical environment.

It established an authoritative reference for future infrastructure decisions.

It identified where new services should be deployed.

It provided Ariadne with knowledge of the hardware environment in which it operates.

Rather than treating the development environment as external information, the infrastructure itself became part of the Knowledge Vault.

---

## Influence on Project Ariadne

One important realisation emerged during this phase.

If Ariadne is expected to provide intelligent engineering guidance, it must understand the environment in which it is operating.

Knowledge of network topology, available hardware, operating systems, storage resources and service locations forms part of Ariadne's operational context.

This principle extends beyond the home laboratory.

Future deployments inside businesses will similarly require Ariadne to understand the customer's infrastructure before meaningful recommendations can be made.

The network itself therefore becomes another knowledge source.

---

## Key Insight

The value of Ariadne does not come from owning the most powerful hardware.

Its value comes from understanding the hardware that already exists and using it intelligently.

Good engineering begins with accurate knowledge.

The network diagram represents the first step toward giving Ariadne that knowledge.

---

## Historical Significance

This document marks the point where Project Ariadne transitioned from conceptual architecture into practical implementation.

The project now possessed both an architectural vision and a documented physical environment capable of supporting incremental development.

Rather than waiting for ideal hardware, development would proceed using existing infrastructure while remaining flexible enough to incorporate future technologies as they became practical and affordable.

---

## Legacy

This network topology became the foundation upon which the first operational versions of Ariadne would be constructed.

As the laboratory evolves, this document will continue to record significant infrastructure changes while preserving the historical record of how the project began.

The home laboratory is not intended to be the final destination.

It is the proving ground where concepts become working systems.