# Image Studio background rendering

Image Studio defaults to **1280 × 720 Landscape**, including the backend fallback.

`POST /api/image/generate` now returns HTTP 202 with a background `job` rather
than holding an HTTP connection open until the PNG is ready. The browser sends
a 32-character hexadecimal `request_id`; repeated submissions with that ID
return the same job. A different submission while rendering receives HTTP 409.

`GET /api/image/generation/<id>` returns the current stage, elapsed seconds,
sampling step/total, and terminal result or error. The browser polls this short
request once per second while working. `/api/image/status` also includes the
latest job, allowing page refreshes and navigation back to recover the result.
The last 32 jobs are retained in memory. Restarting the core clears this history;
saved PNGs and provenance JSON files remain in the configured output directory.

Prompt, negative prompt, size and seed are saved as a local browser draft on
input/change and restored during page initialization. A terminal job cannot be
rolled back by a stale runtime snapshot: its image preview and completion/error
message remain visible. Project image preview URLs use a valid query separator.

ComfyUI's local WebSocket is connected before submission, using the same
`client_id` and `prompt_id`. `executing` events identify checkpoint loading,
canvas preparation, prompt encoding, diffusion, VAE decoding and engine saving.
`progress` events provide actual sampling steps. Non-sampling stages display an
indeterminate bar; the UI does not invent overall percentages or an ETA.
History polling remains authoritative for success and engine errors. Ariadne
then copies the PNG and records provenance before marking the job complete.

Install the image progress dependency in the Python runtime used by the Rust
host with `python -m pip install -r requirements-control-plane.txt`.
ComfyUI stays on loopback; no NAS proxy changes or new public engine endpoints
are required. A dropped progress connection is reported while completion
continues to be checked through history. The render deadline is 30 minutes.

Verification: `python -m unittest test_image_generation test_image_jobs` from
`control-plane`, and `node --check control-plane/image.js` from the repository
root. On Windows, use a writable workspace TEMP/TMP directory if sandbox temp
file replacement is denied.

Run `node control-plane/test_image_ui.cjs` for the out-of-order status/preview
regression and browser-draft restoration checks.
