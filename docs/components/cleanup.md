# Component: Cleanup / filing assistant

Status: source reviewed, **runtime not executed in this documentation review**  
Last source review: 2026-11-17 (GitHub `main`)  
Primary parcel: Toolshed (execution); Workspace/interface (Configuration, job/status)  
Capability ID: `filesystem.organise`; plugin ID: `cleanup`

## Purpose and user entry

Configurable organisation of files from enabled source folders (typically Downloads) into filing classes. Available through Ariadne Configuration's Cleanup workflow. The plugin exposes **Preview** and **Apply**. It is a file-routing capability, not a Knowledge Vault ingestion service.

## Active code map

| Role | Path | What it does |
| --- | --- | --- |
| Plugin registration | [`control-plane/plugins/cleanup/plugin.json`](../../control-plane/plugins/cleanup/plugin.json) | Declares `cleanup`, `filesystem.organise`, permissions, UI routes, actions |
| Adapter and configuration | [`control-plane/plugins/cleanup/cleanup.py`](../../control-plane/plugins/cleanup/cleanup.py) | Default rules, v1 setting migration, validation, command construction |
| Orchestrator / HTTP boundary | [`control-plane/server.py`](../../control-plane/server.py) | Cleanup route and job execution; verify against current runtime before changing |
| Deterministic organiser | [`00_System/Organize-Downloads.ps1`](../../00_System/Organize-Downloads.ps1) | Classifies and moves files; optional JSON result and PowerShell WhatIf |
| Focused tests | [`control-plane/test_cleanup_plugin.py`](../../control-plane/test_cleanup_plugin.py) | Configuration migration, source/class handling, preview/apply, collision, API boundary |
| Plugin contract | [`control-plane/docs/PLUGIN-FRAMEWORK-V0.1.md`](../../control-plane/docs/PLUGIN-FRAMEWORK-V0.1.md) | Generic plugin discovery and execution architecture |

**Source-level call chain:** Ariadne's Cleanup API/job surface → trusted `cleanup.build_command` adapter → PowerShell `Organize-Downloads.ps1`, with `-ConfigPath`, optional `-ResultPath`, and `-WhatIf` for Preview. The API route `/api/plugins/cleanup/run` is exercised by the focused test suite.

**Important obsolete path:** [`00_System/Start-AriadneControl.ps1`](../../00_System/Start-AriadneControl.ps1) delegates immediately to an external Vault v2 workflow and exits. Its subsequent `$DownloadsOrganizerPath` and `downloads_preview` / `downloads_apply` entries are **unreachable historical code**, not evidence of a current caller. Check the externally owned Vault v2 implementation if diagnosing that older menu.

## Contracts, defaults and safety

- Manifest actions: `preview` is non-mutating; `apply` is mutating. Both are declared schedulable metadata, not evidence of a running schedule.
- Configurable enabled source folders, filing classes, extensions or filename patterns, recursion, exclusions, and destinations. Current default classes: Markdown, Email, Screenshot, Image, Music, Video.
- Screenshot is matched by filename pattern rather than routing every image to the screenshot destination. Other classes use extensions unless patterns are provided.
- Duplicate target filenames use the **skip** collision policy: existing destination content is not overwritten. Files that do not match any enabled class are left in place.
- Preview uses PowerShell `-WhatIf`; Apply may move files. Respect configuration confirmation policy and check the real API/session boundary before adding another invocation.
- The adapter writes the effective configuration to a runtime JSON file and may request structured results; it does not read file contents or call Vault ingestion.
- Permissions declared by the manifest: `filesystem.read`, `filesystem.move`. Actual user filesystem permissions still govern moves.
- Do not replace the configurable adapter with the script's legacy, hard-coded `-Root` defaults when servicing the plugin.

## Verification and recovery

From the repository's `control-plane` directory:

```powershell
py -3 -m unittest test_cleanup_plugin
```

The tests contain PowerShell-dependent integration cases and skip some cases if no usable PowerShell executable exists. Do **not** interpret a skip as a successful execution test. Source inspection only was performed for this documentation entry.

For an operator check, configure a temporary source and destination folder, create disposable sample files, use Preview first, confirm the reported planned changes, then Apply only within that disposable fixture. Confirm collisions preserve the existing destination and unmatched files remain. Avoid testing Apply on the real Downloads or Vault during documentation work.

Code changes can be reverted through Git; moves of user files must be handled through an explicit, verified recovery plan, not a blind reverse command.

## Decision and change-history pointers

- [Plugin contract](../../control-plane/docs/PLUGIN-FRAMEWORK-V0.1.md): trusted bundled adapter and metadata-first discovery.
- [Engineering Standards](../EngineeringStandards.md): tests, user data preservation, configuration over hard-coded behaviour.
- Use Git history and relevant PRs for the actual evolution of the organiser and Cleanup plugin. This page does **not** assert dates or motivations that have not been verified.

## Maintenance checklist

If Cleanup changes, update this map and the [Engineering Catalogue](../COMPONENT-CATALOGUE.md); check the manifest, adapter, command options, `server.py` job boundary, config migration, preview/apply tests, collision behaviour, and any external Vault v2 integration.
