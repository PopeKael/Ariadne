# Create four-panel acceptance — 2026-10-31

This records the live checks completed while continuing from Ariadne commit
`1040bfc` (`feat(create): add local Speech Studio`). Existing Music and Image
implementations were preserved. Their earlier successful generation evidence
was part of the starting checkpoint; this pass focused on Video, Speech, GPU
handoff, and the served Create/Speech pages.

## Results

| Area | Result | Evidence |
| --- | --- | --- |
| Music | Existing live generation evidence retained | No implementation changes in this pass. |
| Image | Existing live generation evidence retained | No implementation changes in this pass. |
| Video | Passed | Managed Ariadne start/stop, successful ComfyUI history, MP4 range response, and full local decode. |
| Speech voice library and cloning | Passed | Saved voice `47de8170fb1b4027b262e50d983f432c` was listed from the persistent library and selected for a real VoxCPM2 reference-clone job. |
| Speech output and exports | Passed | Generated WAV and MP3 both decoded; ASR text export was retrieved and persisted. |
| GPU handoff and Home recovery | Passed | With Home resident, both Speech controls submitted through the real local UI code. Rust Host released the idle model, admitted Speech, and released its lease. A later Home session reacquired Qwen through Rust Host and answered a real prompt. |
| Served Create and Speech markup | Passed | Local RUN routes returned HTTP 200; all four panel names and the Speech Studio workspace controls were present. |
| Microphone capture | Not verified | Windows exposed no audio capture endpoint in DirectShow or the enumerated audio endpoints during this check. No recording was simulated. |
| Browser visual inspection | Manual check remains | The browser inspection request redirected toward the public origin and was denied by automatic review. The authorized local HTTP routes and page markup were inspected instead. |

## Video repair and generation

The earlier 79-second render was not a failed generation. ComfyUI's history
showed that it completed successfully after 107.53 seconds and wrote an MP4.
During an active render, Ariadne's status path synchronously requested
`/system_stats` with a 15-second timeout. A telemetry timeout was treated as a
stopped backend, even while the ComfyUI process and prompt were still alive.
This made a slow but successful render appear failed.

The renderer now reuses the most recent healthy GPU telemetry while a clip is
active, bounds history polling, checks the local ComfyUI process after transient
endpoint errors, and only marks the job failed when the backend is actually
dead or ComfyUI reports an execution error. Explicit ComfyUI failures remain
failures. Four focused regression tests cover those cases.

A new managed render completed with prompt ID
`56e5eb84-ce00-4cdd-a148-83cc6da7ef87`: 360p, 640×384, 25 frames, 24 fps,
6 sampling steps. The output was
`D:\Downloads\Videos\Ariadne Video E2E 2026-10-31 - 56e5eb84.mp4`
(375,158 bytes). The renderer served a byte-range response (`206`,
`Content-Range: bytes 0-1023/375158`); FFmpeg decoded all 25 H.264 frames from
the configured output copy. Ariadne's managed stop released the renderer GPU
lease afterward.

## Speech and resource handoff

The local reference WAV was saved as voice `Ariadne E2E Reference`
(`47de8170fb1b4027b262e50d983f432c`). The library reloaded it with its
reference transcript and consent record. A reference-based clone job completed
as job `ff830d478aa2446eabfa8fb7969395fa`:

- [WAV output](</D:/Downloads/Speech/ff830d478aa2446eabfa8fb7969395fa.wav>) — PCM, 48 kHz mono, 7.68 seconds; decoded successfully.
- [MP3 output](</D:/Downloads/Speech/ff830d478aa2446eabfa8fb7969395fa.mp3>) — 48 kHz mono, 7.704 seconds; decoded successfully.

Before the Speech run, Home's `qwen3.5:9b-q4_K_M` model was resident and
available WDDM memory was 6.4 GiB, below Speech's 9 GiB minimum. Speech
initially showed Blocked before the final button-state correction. Through the existing Rust Host path,
the idle Home model was unloaded, the Rust Host owner changed to `SPEECH`, and
the clone job completed under that lease. After completion the Speech lease was
released, GPU ownership returned to `NONE`, and free memory rose to about
14 GiB. Home was then resumed and answered a prompt with verified model
residency; after closing that session, the idle model could be preloaded again
through the Rust Host.

ASR then transcribed the generated clone output. The returned transcript matched
the generated speech and was saved as
[TXT export](</D:/Downloads/Speech/92427ae63ea14c2b9dc01e1243c4aa8c.txt>).
The ASR result contained no timestamp segments, so this run did not produce an
SRT export. The UI's timestamped SRT path remains conditional on the engine
returning segments.

## Browser and regression checks

The running Ariadne control plane served `/create` and `/speech` on
`http://127.0.0.1:8765` with HTTP 200. The Create response contained Music
Engine, Image, Video, and Speech Studio. The Speech page contained the record,
transcribe, save/select voice, generate, WAV, MP3, and TXT export controls.
The project exposes its existing RUN process here; the source explicitly says
manual local DEV is unavailable and uses Docker for build/deploy only. No
separate deployment mode or networking change was introduced.

Checks run:

- `node control-plane/test_create_ui.cjs` — passed.
- `node --check control-plane/create.js` and `node --check control-plane/speech.js` — passed.
- `python -m unittest test_speech_studio test_image_jobs` — 14 passed.
- Renderer `./bin/python -m unittest -v test_comfy_status` — 4 passed.
- `git diff --check` for scoped changes — passed.

The Create and Speech pages were verified through the local HTTP routes, but a
manual browser visual check is still required. A real microphone recording also
remains unverified until Windows exposes an audio capture device.

## Final Speech handoff correction

The UI had treated every `blocked` status as a disabled Generate/Transcribe
state. That also blocked the user when the only issue was VRAM held by an idle
Home model, so Rust Host never got the request that would initiate its existing
handoff. Speech status now distinguishes safe submission from immediate free
capacity: it reports `ready_after_handoff` only when WDDM telemetry and Ariadne
GPU ownership are healthy, no AI activity or Home/Vault session model lease is
active, and measured free memory plus quantifiable idle Ariadne model
allocations meet the 9 GiB preflight. The local Speech API also refuses to
queue a job unless status is `ready` or `ready_after_handoff`. The Rust Host
still owns exclusive admission and verifies actual WDDM capacity after the
handoff. Unknown ownership, active leases, unavailable telemetry or residency,
and insufficient projected capacity stay blocked.

The change was loaded through the canonical Rust Host in `RUN · HERA`. The
`start-ariadne.ps1` reporting preflight itself could not deserialize the
417 MB Vault embedding index in this shell (PowerShell raised
`OutOfMemoryException`); the same configured resident-host executable was
started directly and `/api/core/ready` returned ready. The Home model was then
preloaded through Ariadne's existing Rust Host path. With
`qwen3.5:9b-q4_K_M` resident and about 7 GiB free, `/api/speech/status`
reported `ready_after_handoff`, identifying one idle model allocation and a
projected 13.2 GiB free.

The live Node harness ran the actual `speech.js` against the local RUN APIs and
clicked each control:

- Generate used saved reference voice `47de8170fb1b4027b262e50d983f432c` and
  completed job `6a6d4282c7e74f448aa07c61da9b9e52`. It wrote
  [WAV](</D:/Downloads/Speech/6a6d4282c7e74f448aa07c61da9b9e52.wav>) and
  [MP3](</D:/Downloads/Speech/6a6d4282c7e74f448aa07c61da9b9e52.mp3>).
- Transcribe used that new WAV and completed job
  `204ed614f98544e789485bfd8906ca16` with transcript “A short handoff test.”
  It wrote
  [TXT](</D:/Downloads/Speech/204ed614f98544e789485bfd8906ca16.txt>).
- Both jobs began with Home resident; during handoff the harness observed
  Rust Host owner `SPEECH`, Home unloaded, then owner `NONE`. The Host lease
  was released when each job completed.
- A subsequent Home session reacquired Qwen through Rust Host and answered
  `What is 2 + 2?` with “The sum of 2 and 2 is 4.” Speech status was `blocked`
  while that Home session lease was active, then returned to `ready` after the
  session closed. There were no in-flight AI jobs afterward.

Regression coverage now includes the UI's safe-handoff enabled state and its
busy/blocked disabled states; safe versus insufficient idle-model capacity;
active session leases even when free VRAM is already sufficient; and rejection
of direct Speech submissions while admission is unsafe. The RUN page remains
verified through local routes and markup. The manual browser visual check and
real microphone capture remain outstanding as recorded above.
