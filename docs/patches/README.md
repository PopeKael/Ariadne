# Hermes pinned-source patch

`hermes-context-budget.patch` preserves Ariadne's local context-budget changes
against the isolated Hermes checkout at base revision
`fcace146e9ec62a6455b5a0894f55e5298574903`.

The upstream Hermes checkout is not an Ariadne repository and its configured
remote points to `NousResearch/hermes-agent`. The patch is therefore versioned
with Ariadne rather than pushed to that upstream. Apply it from the root of the
matching Hermes checkout with `git apply` when recreating the isolated runtime.
Keep the pinned base revision unchanged unless the patch is deliberately
rebased and reverified.

The isolated coding-runner permission hook used for the accepted pilot task is
tracked at `control-plane/plugins/qwen-code/ariadne-qwen-pretool-guard.cjs`.
The accepted local worker install used a SHA-256-identical copy.
