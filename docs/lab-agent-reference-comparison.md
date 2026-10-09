# Agent core comparison, 2026-10-09

Inspected before changing the core:

- [mini-SWE-agent DefaultAgent](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/agents/default.py): `run` repeatedly calls `step`; `step` calls `execute_actions(query())`. Query receives the conversation; environment results become observation messages. Format errors return to the conversation. Limits and trajectory saving surround this loop.
- [Aider Coder](https://github.com/Aider-AI/aider/blob/main/aider/coders/base_coder.py): `apply_updates` parses and applies edits; edit errors become `reflected_message`. Edited files can be linted and tested; concrete failures feed the next response.
- [Aider EditBlockCoder](https://github.com/Aider-AI/aider/blob/main/aider/coders/editblock_coder.py): reads actual file content, applies SEARCH/REPLACE, reports unmatched edits rather than silently replacing complete files.

- [mini-SWE-agent default prompt](https://github.com/SWE-agent/mini-swe-agent/blob/main/src/minisweagent/config/default.yaml): pairs a short explanation of the next action with one command. Ariadne uses a bounded action rationale plus one structured safe action, within the same model call.

Ariadne before this change forced create(contract, plan, project, checks), contract validation, execute, review, contract/test revision or repair, coverage verification. The controller chose semantic stages rather than the model choosing actions. An empty revision checklist stopped the process before an application repair. This differs materially from the reference loop.

Replacement: one model query interface and one environment execute interface. The model chooses read, write, patch, run, test, or finish. Each observation is returned in the same conversation. Remove contract extraction/revision, acceptance planning, mandatory reviewer calls, coverage state machines and their role-specific schemas/prompts. Retain provider transport, workspace safety, source compilation, isolated browser execution, limits, metrics, saved candidates and UI/Runner infrastructure.

This adopts the control-flow and editing patterns, not the projects' unrestricted shell tools, dependencies, or source code. Browser V1 remains one offline index.html in Ariadne's existing sandbox.
