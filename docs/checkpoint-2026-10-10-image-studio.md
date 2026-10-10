# Image Studio correction — 10 October 2026

## Problem and correction

A 205-second SDXL render succeeded and was saved, but the public Image Studio
page showed no result. Generation previously held a single HTTP request open;
periodic status refreshes replaced rendering/error feedback with “ready”.

Generation now returns a background job immediately and uses short polling
requests. ComfyUI execution events supply real stages and sampling counts, with
elapsed time and an indeterminate bar during loading/decoding/saving. Busy
controls prevent duplicate generation and stopping an active render. Results
and errors remain visible; refreshing reconnects to the current job.

The default is 1280 × 720 Landscape in the UI and backend. Prompt, negative
prompt, size and seed persist in browser storage across refresh. An additional
race found during live acceptance allowed an older running snapshot to hide a
finished image; terminal jobs now reject that stale update. Project image URLs
also use the correct query separator.

## Verification

- 13 focused Python tests passed using the actual bundled core Python runtime.
- JavaScript syntax check and `git diff --check` passed.
- Node regression checks cover terminal result versus stale running status,
  project preview URLs, and prompt/settings restoration after initialization.
- Submitted a real 1280 × 720 SDXL image from Opera at
  `https://ariadne.dia.net.au/image`. Observed checkpoint loading, sampling
  counts, decoding and completion. Refreshed during rendering and recovered the
  same job. Total job time: 204.4 seconds; ComfyUI execution: 200.87 seconds.
- Visually verified the completed image on the public page after correcting the
  stale-status race. Public image delivery returned HTTP 200, 1,463,141 bytes.
- Saved output: `D:\Downloads\Images\ariadne-c96b458f-9785-43a7-b9ac-294db4dab586.png`
  and matching JSON provenance. Generated media is not part of this commit.
- Qwen3.5 9B remained resident during the render. No NAS proxy configuration was
  changed. A proxy timeout remains an inference, not a log-confirmed diagnosis.
- Final prompt-retention UI interaction was stopped by the user's Escape key;
  draft restoration is covered by the Node regression instead.

## Runtime and scope

The host's Python runtime now has `websockets==17.0.1`, declared in
`requirements-control-plane.txt`. The core was restarted through the existing
host command, and the image engine started through Ariadne's existing API.
See [the job API notes](image-studio-jobs.md) for setup and lifecycle limits.
Job history is in memory (last 32 jobs); restarting the core clears it, while
saved image/provenance files remain. The generation deadline is 30 minutes.

This checkpoint includes only Image Studio changes and their documentation.
Existing Lab source, fixtures, screenshots, and its separate changelog entry
remain outside this commit.
