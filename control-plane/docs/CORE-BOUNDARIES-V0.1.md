# Ariadne Core Boundaries v0.1

This is the working ownership map for the local Ariadne control plane.

## Core

Ariadne Core owns the application and Windows host lifecycle, Home
conversation, durable conversation/turn storage, configuration, health,
model routing, Knowledge Vault access, identity/World State, plugin discovery,
permissions, resource arbitration, and shared presentation/event mechanisms.

Core presentation includes the tray, avatar, speech/read handoff, and shared
status surfaces. Optional plugins report through Core contracts; they do not
call avatar images, tray operations, speech implementation, or page-specific
notifications.

## Host capabilities

Host capabilities describe what the current machine provides. They are not
plugins and are not integrations. The System Details page reports examples
such as:

- physical memory and GPU telemetry;
- local storage volumes;
- WSL distributions and Docker state;
- Ollama, Open WebUI, and LM Studio local runtime availability;
- the managed Linux/video renderer lifecycle.

Host detection remains controller-owned because it includes Windows APIs,
process inspection, GPU arbitration, and safe lifecycle actions.

## Plugins and integrations

Plugins are optional Ariadne capabilities discovered from manifests. An
integration is a plugin which adapts an external product or provider. The
external product is not itself an Ariadne plugin. For example, a future
Synology integration would describe its configured Synology connection through
the plugin contract; Core would not contain a permanent Hera/NAS URL.

The Plugins / Capabilities page is the inventory for these optional pieces.
System Details shows only a compact registry summary and links to that page.
Unavailable or malformed plugins remain visible as attention records and do
not prevent Core or System Details from operating.

## Conversation and interaction seam

The existing `ChatStore` already provides the durable provider-independent
shape:

```text
conversation (chat_id) -> turn (turn_id) -> response (same turn reference)
```

`core_interactions.py` adds the shared event seam without changing the chat
schema. Current events are `conversation_attached`, `turn_started`,
`response_completed`, and `response_interrupted`. The event vocabulary also
reserves `selection_created` and `feedback_recorded` for future interfaces.

Events carry stable conversation/turn/response references and bounded metadata,
not model-weight instructions. They are available through
`/api/core/interactions` and are written to the ignored runtime JSONL stream.
Home is the first producer; future interfaces and plugins can consume the
same seam without importing `home.js`.

The intended future path for feedback is:

```text
user interaction -> recorded Core evidence -> interpretation/personalisation
layer -> controlled runtime steering
```

This pass deliberately does not implement feedback UI, learning, fine-tuning,
or personality mutation.

## Selected text

The current reader handoff owns its own presentation operation: it prepares the
answer node, copies the answer, and sends the fixed Alt+F1 reader shortcut.
That mechanism remains intact. A future selected-text feature should create a
Core interaction reference containing the conversation/turn/response and a
bounded quote or range, then route it through Home/Core services. It should
not make speech selection, reader selection, and conversational selection the
same DOM helper or require a plugin to know the Home page structure.

## System Details intent

System Details answers three questions:

1. Is Ariadne Core healthy?
2. What capabilities does this machine provide?
3. Which optional Ariadne plugins/integrations are installed and healthy?

It is not a permanent list of Wazza's external services. Machine-specific
connections belong in configuration or future integration manifests.
