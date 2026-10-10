# Local Lab idle-watchdog validation — 2026-10-09

Normal total/per-call elapsed cutoffs were removed. The existing model → action →
observation loop is unchanged. Local Ollama retains context/output limits, steps,
repair and repeated-action protection. The separate legacy prompt-byte ceiling
was removed after the first rerun showed it rejecting a request whose token
preflight still fit. No monetary cap applies to local inference; this adapter
has no paid external provider path.

The native connection watchdog defaults to 900 seconds of silence. Valid content,
thinking output or completion refreshes it. Empty chunks cannot keep a hung
connection alive. Productive calls have no fixed maximum elapsed duration.
Tool evidence waits use the generous emergency allowance; deterministic source
and browser assertions retain their existing test semantics.

The core was restarted and 79 focused regressions passed after the final restart.
Actual HTTP tests cover sustained output lasting beyond an idle-watchdog interval
and termination of silent and empty-chunk connections. An agent regression proves
large elapsed time does not stop a productive loop. Context tests accept measured
requests above the former byte cap while still rejecting an estimated overflow.

The exact original Dungeon Test prompt was rerun through Chat; SHA256:
`9d2f9ad4f7cf27cfb385bf983600030c359efeee44a9e3cbca2f0bc622df8ad5`.

- [First rerun](lab-local-watchdog-first-rerun.json): 6 calls, 780.938 seconds;
  completed a 455.672-second productive call, then exposed the removed byte cap.
- [Final rerun](lab-local-watchdog-final-rerun.json): 7 calls, 805.531 seconds;
  44297 input / 5755 output tokens summed across calls. No time stop.
  Ended NEEDS ATTENTION at the retained conservative context preflight after
  repeated edits and full-file reads. Estimated next input 26365 against 24576
  available; last measured actual input 12198. This is not proof the actual
  32768-token window was full. The generated source still had a syntax error.

The time-limit change is verified. The Dungeon application remains unverified;
no success or complete-game claim is made. No GUI redesign, new model, agent
semantic stages, commit or GitHub push was performed in this change.
