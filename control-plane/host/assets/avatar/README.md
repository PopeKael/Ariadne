# Ariadne avatar assets

`avatar_states.json` is the runtime contract between semantic states and
artwork files. The sixteen artwork files are intentionally not included in
this migration; supplied transparent PNG, animated WebP, or APNG assets can
be added later without changing the IPC state names.

The host logs missing or invalid assets and remains usable with the avatar
hidden or blank while artwork is being prepared.
