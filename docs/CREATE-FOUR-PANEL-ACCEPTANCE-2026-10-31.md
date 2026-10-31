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
| GPU handoff and Home recovery | Passed | Rust Host released the idle Home model for Speech, granted the Speech lease, released it after completion, and subsequently restored verified Home residency. |
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
correctly remained blocked at that point. Through the existing Rust Host path,
the idle Home model was unloaded, the Rust Host owner changed to `SPEECH`, and
the clone job completed under that lease. After completion the Speech lease was
released, GPU ownership returned to `NONE`, and free memory rose to about
14 GiB. Home was then resumed and answered a prompt with verified model
residency; after closing that session, the normal Home preloader restored the
idle model. At final inspection the model was resident, the AI lease was idle,
and there were no in-flight jobs. Speech status remained safely blocked while
Home consumed enough memory to leave about 7 GiB free.

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
