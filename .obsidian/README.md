# .obsidian

## Purpose

This folder contains Obsidian's local configuration and application state.

Examples include:

- Workspace layout
- Open tabs
- Graph view settings
- Installed plugins
- Theme preferences
- Cache and temporary files

These files are generated automatically by Obsidian and are specific to each user's environment.

## Why isn't this folder included?

Project Ariadne provides the structure for a knowledge vault, not the personal configuration of the person using it.

Each installation will naturally create its own `.obsidian` directory the first time the vault is opened.

Keeping these files out of version control prevents:

- Personal preferences being shared accidentally
- Machine-specific settings causing merge conflicts
- Temporary cache files being committed to the repository

## Expected behaviour

After cloning the repository:

1. Open the vault in Obsidian.
2. Obsidian will automatically create the required configuration files.
3. Configure plugins, themes, and workspace layout to suit your own workflow.

No manual setup of this folder should normally be required.

---

Project Ariadne intentionally versions knowledge, documentation, and automation, while leaving personal application state under the control of each individual user.