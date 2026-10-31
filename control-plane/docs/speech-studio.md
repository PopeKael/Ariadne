# Ariadne Speech Studio

Speech Studio is the fourth panel on `/create`. It runs the existing native
Windows `audio.cpp` CLI, and uses Ariadne's Rust Host GPU lease path for model
inference. It adds no WSL, cloud speech service, or third-party desktop speech
application.

## Local components

The runtime and model identities are recorded in
[`speech-models.lock.json`](../speech-models.lock.json). The currently installed
runtime is audio.cpp 0.8.0, commit `4af1432` (2026-09-15), built for Windows
AMD64 with CPU and Vulkan backends. The installed CLI hash is recorded in the
lock file.

| Job | Model package | Stored size | License |
| --- | --- | ---: | --- |
| Transcription | Qwen3-ASR 0.6B Q8_0 | 1,151,279,968 bytes | Apache-2.0 |
| Text-to-speech, voice design, cloning | VoxCPM2 Q8_0 | 2,955,007,488 bytes | Apache-2.0 |

Weights are not checked into Git. They are installed under
`control-plane/runtime/external/audio-cpp-v0.8.0-vulkan/models`; that runtime
tree is machine-local. The application streams each model through SHA-256 on
first status check, caches the result for the file's size and modification time,
and enables inference only when the bytes match the lock. A missing, changed,
or unlocked model is reported as unavailable.

The package manager's current package specs resolve from the `main` branch of
`audio-cpp/audio.cpp-gguf`. Updating is manual: use the bundled
`app/tools/model_manager_v2.py`, review the upstream release and model license,
verify the new file hashes, then update the lock file in the same change. The
Create page does not download or silently update weights. The lock hashes pin
the exact installed artifacts even though the upstream package spec names a
branch.

The source project lists Qwen3-ASR and VoxCPM2 as Apache-2.0 model weights with
commercial use allowed; audio.cpp itself is also Apache-2.0. Keep the upstream
notices when redistributing. See the [audio.cpp model license table](https://github.com/0xShug0/audio.cpp/blob/main/docs/model_licenses.md),
[Qwen3-ASR project](https://github.com/QwenLM/Qwen3-ASR), and
[VoxCPM project](https://github.com/OpenBMB/VoxCPM). Model licenses are
separate from audio.cpp's code license.

Audio conversion uses the same FFmpeg executable already resolved by Ariadne's
Music Engine. Its installed version and hash are noted in the lock file; this
is an existing local codec dependency, not a hosted speech service.

## Use and storage

- Transcribe MP3, WAV, M4A, MP4, WebM, OGG, MOV, or MKV. FFmpeg extracts mono
  16 kHz PCM locally before Qwen3-ASR runs on Vulkan.
- Record through the browser microphone and submit the resulting local recording
  through the same transcription path. Browser microphone access is requested
  only after the user clicks **Record microphone**.
- Edit the transcript in the page, copy it, or download its current edited text
  as TXT. Downloaded SRT preserves the original ASR segments and timing, when
  the runtime returned segments.
- Generate audio with a described synthetic voice, or save a reference clip and
  optionally its transcript for reuse with VoxCPM2. WAV and MP3 are written
  locally and can be previewed in the page.
- VoxCPM2 uses audio.cpp's `tts` task for plain speech, voice design, and
  reference cloning. Voice design prefixes the text with `(description)`;
  cloning passes `--voice-ref` and an optional `--reference-text`. See the
  [audio.cpp TTS guide](https://github.com/0xShug0/audio.cpp/blob/main/docs/tts.md).
- Saved reference profiles report clip duration, normalized sample rate,
  channel count, and optional user-written voice notes. Ariadne does not infer
  speaker identity, gender, or store a derived speaker embedding.
- The voice library retains the supplied original clip plus a normalized mono
  16 kHz WAV. Saving requires an explicit ownership/permission checkbox. Delete
  removes the source audio, normalized reference, and profile metadata.
- Transcription inputs, job history, intermediate files, and logs live under
  `%LOCALAPPDATA%\Ariadne\speech-studio`. Transcript and generated audio outputs
  use Configuration → Storage → Speech (default `D:\Downloads\Speech`). A
  confirmed **Delete** on a completed job removes its source, intermediates,
  logs, history, and matching outputs.

## GPU and lifecycle

Each inference subprocess requests the exclusive Rust Host `speech` GPU workload
with a 9 GiB minimum-free-WDDM reservation and verifies that lease before
launch. Rust Host recognizes `speech` as an exclusive workload and releases idle
Ollama residency before admitting it. The lease is released as soon as
audio.cpp exits; FFmpeg MP3 encoding then runs on the CPU. A single queued,
running, or stopping speech job is allowed at once. Cancellation uses a
per-job event and terminates only that job's subprocess.

Speech status is `ready` only when the CLI is present, both locked model files
verify, WDDM telemetry is available with at least 9 GiB free, no conflicting
workload owns the GPU, and Rust Host reports an idle transition. The Create
panel displays runtime, model, owner, GPU headroom, output path, job state, and
stop control. After a job, Ariadne may request its normal model through Rust
Host; the host remains the authority for that admission.

## Live acceptance, 2026-10-31

These results came from the local Ariadne control plane on the RX 7800 XT. The
audio.cpp ASR and VoxCPM2 model files both passed their locked SHA-256 checks.

| Workflow | Result |
| --- | --- |
| Music | MiniMax Music 3 Q4 rendered a 16-step, 41.935-second WAV through Vulkan. The file remains an unaccepted candidate under `D:\Downloads\Music\Candidates`. |
| Image | ComfyUI SDXL Base 1.0 completed a 768 × 768 test render (seed 123457) in about 99 seconds, including first model load. The PNG and provenance JSON remain an unaccepted candidate under `D:\Downloads\Images`. The lease returned to idle afterward. |
| Video | The existing Ubuntu 24.04 WSL renderer started under the exclusive renderer lease and reported GPU readiness. A one-second, 360p, six-step test request timed out after about 79 seconds when its ComfyUI backend stopped. Ariadne stopped the renderer and returned GPU ownership to idle; no successful clip is claimed. |
| Speech synthesis | VoxCPM2 produced 4.8 seconds of 48 kHz mono audio in 2.546 seconds (0.5304 real-time factor). WAV and MP3 outputs were written under `D:\Downloads\Speech`. |
| Speech transcription | Qwen3-ASR transcribed the generated test WAV in 646 ms (0.1346 real-time factor): “Ariadne local speech test: The four media engines are switching safely.” The TXT export endpoint returned the same text. |
| Home recovery | A fresh, isolated Home chat answered the exact recovery prompt. The two-message test chat was purged after the response. Final checks showed the Qwen Home model resident, no in-flight request, and an idle GPU transition. |

An actual browser microphone recording was not used; microphone access remains
gated behind the page's explicit **Record microphone** action. Rendered browser
inspection was not completed: the browser origin gate treated the requested
local Create page as the configured public origin `https://ariadne.dia.net.au`
and denied observation because that origin was not approved. Source and API
checks do not substitute for that visual check.

After the Home recovery check, measured free VRAM was 6.9 GiB with the Home
Qwen model resident. Speech status therefore correctly reported `blocked`
against its 9 GiB reservation, while the model files remained verified. Speech
jobs had already completed successfully earlier in this acceptance run, when
Rust Host could admit the exclusive speech lease.

## Install and verify

From the repository root, use the bundled manager for either package:

```powershell
python .\control-plane\runtime\external\audio-cpp-v0.8.0-vulkan\app\tools\model_manager_v2.py install qwen3_asr_0_6b_q8_0 --models-root .\control-plane\runtime\external\audio-cpp-v0.8.0-vulkan\models
python .\control-plane\runtime\external\audio-cpp-v0.8.0-vulkan\app\tools\model_manager_v2.py install voxcpm2_q8_0 --models-root .\control-plane\runtime\external\audio-cpp-v0.8.0-vulkan\models
```

The installed runtime and both installed model files were checked against the
versions and SHA-256 values in the lock file during implementation. Unit and
live verification outcomes are recorded with the corresponding Ariadne change;
do not treat status as proof of audio quality until a real sample has completed
successfully on this machine.
