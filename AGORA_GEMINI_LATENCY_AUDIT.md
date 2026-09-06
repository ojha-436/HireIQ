> **Implementation update:** §6 items 1 (publish candidate mic/camera), 4 (subscribe and
> play remote tracks) and 5 (network-quality callback → the existing voice-warning UI)
> are now implemented, and the `_pump()` double-audio guard is in too. Item 2 was
> deliberately NOT implemented as originally written — rather than finishing the dead
> `attachAgora` hook (which would have republished the AI's voice under the
> *candidate's own* Agora identity), the AI's real-participant presence now comes from
> the Agora ConvoAI agents that already exist per persona in `agora_convoai.py`, each
> with its own `agent_rtc_uid` — a genuinely separate identity instead of a second track
> on the candidate's. `attachAgora` itself is removed as dead code. Item 6 (per-hop
> timing instrumentation) is not implemented — see the note at the end of this file.
>
> None of this has run against a live Agora App ID or ConvoAI credentials —
> `AGORA_APP_ID`/`AGORA_CUSTOMER_ID`/`AGORA_CUSTOMER_SECRET` are still empty in this
> environment, so `info.enabled` stays false and the new publish/subscribe code path has
> never actually executed. It has only been verified by (a) the full backend test suite
> plus 7 new regression tests (`test_agora_media_guard.py`, `test_convoai_audio_guard.py`)
> and (b) a live browser run confirming zero behavior change to the existing
> no-Agora-credentials flow. Test this for real the first time real credentials exist.

# Agora + Gemini media-path audit

Requested: inspect exactly how candidate audio moves between Agora and Gemini today,
compare against the required target architecture (Agora carries media, Gemini carries
reasoning, Moderator carries turn-taking), find the source of the reported 5–6s delay,
and report findings with measurements — not claims.

**Bottom line, up front:**

1. **Agora currently carries none of the interview's live audio.** The candidate's mic
   and the AI's voice both travel over a plain WebSocket between the browser and the
   FastAPI backend. Agora RTC is joined by the browser but nothing is ever published or
   subscribed on it. The code contains an unfinished/dead hook (`this.bot.attachAgora?.
   (this.agora)`) suggesting an Agora audio-track publish was intended and never
   completed.
2. **The Agora Conversational AI (ConvoAI) integration exists server-side but cannot
   currently reach the candidate.** When configured, the backend starts one ConvoAI
   agent per persona and pushes each line through Agora TTS — but since the browser
   never subscribes to remote Agora audio tracks, that voice is synthesized into the
   channel and never played. Gemini's own native audio is what the candidate actually
   hears, over the WebSocket, in both configurations. Running both is also pure waste:
   the candidate is billed/exposed to two TTS generations of the same line whenever
   ConvoAI is configured, and hears only one.
3. **The reported 5–6 second delay is very likely NOT a media-hop problem — it's
   arithmetic already visible in the constants.** Browser VAD hang (700 ms) + candidate
   transcript settle (2.5 s) + persona output settle (2.5 s) = **5.7 s of deliberate,
   code-level waiting per turn**, before any model or network latency is counted. This
   matches the report almost exactly and would reproduce identically whether the media
   moved over Agora or a raw socket.
4. **Personas-on-one-channel and connection reuse are already done right.** There is one
   `agora_channel` per `InterviewSession` (not one per persona), and Gemini Live
   connections are opened once per persona and kept alive for the whole interview via a
   pre-warming floor (`_Floor.warm`/`acquire`) — no reconnect-per-turn exists in the
   current code. These two requirements are already satisfied structurally; they just
   aren't being tested end-to-end over Agora because Agora isn't in the media path yet.

Every claim below cites the exact file and line it comes from. No wall-clock numbers are
asserted anywhere in this report unless they come from a constant in the code (labeled
"deliberate, per the code") — real end-to-end timings require a live `GEMINI_API_KEY`
and a real microphone/network, neither of which exists in this environment (confirmed:
`backend/.env` has `GEMINI_API_KEY=`, `AGORA_APP_ID=`, `AGORA_CUSTOMER_ID=`,
`AGORA_CUSTOMER_SECRET=` all empty). Section 6 gives the exact instrumentation to add so
the next person who *does* have those keys can produce the hop table this report asks
for, in one interview.

---

## 1. What the code actually does today (verified by reading it, not assumed)

```
Candidate mic
  │ getUserMedia() → AudioWorklet (mic-worklet.js) resamples device rate → 16 kHz mono PCM16
  │ 640-sample (40 ms) frames
  ▼
Raw WebSocket binary frame   ── frontend/js/interview/room.js:126-128, 197-199
  │  ws.send(buf)  — captureMic()'s onFrame callback is literally `this._sendBinary`
  ▼
FastAPI WebSocket endpoint    ── backend/app/routers/interview.py:386-387
  │  frame["bytes"] → runtime.on_audio(bytes)
  ▼
InterviewRuntime.on_audio()  ── backend/app/interview/session.py:974-987
  │  gates on _capture_allowed + _carries_speech(), then conn.send_audio(pcm16)
  ▼
google-genai Live session (per persona, one long-lived connection)
  │  native-audio model: reasoning + speech in one pass
  ▼
InterviewRuntime._pump()     ── backend/app/interview/session.py:1325-1345
  │  EV_AUDIO → self.emit_audio(pcm)   (unconditional — no ConvoAI check)
  ▼
FastAPI: websocket.send_bytes(pcm)    ── backend/app/routers/interview.py:365-370
  ▼
Raw WebSocket binary frame
  ▼
frontend/js/interview/room.js:170-171
  │  typeof e.data !== 'string' → this.bot.enqueue(e.data)
  ▼
BotAudio (Web Audio API, LOCAL playback only) ── frontend/js/interview/bot-audio.js:41-64
  ▼
Candidate speaker
```

**Agora's actual role in this picture: none.** `room.js` does call `this.agora.join(...)`
(line 136) when a token is available, but:

- No `agora.publish(...)` call exists anywhere in the frontend for the candidate's mic
  or camera track. `grep -rn "\.publish(" frontend/js/` matches nothing outside the
  vendor SDK bundle.
- No `.subscribe(...)` / `user-published` handler exists anywhere in the frontend either.
  Even if something were being published into the channel by an Agora ConvoAI agent, the
  candidate's browser has no code path that would ever play it.
- The candidate's camera track is attached to a **local-only** `<video>` element
  (`room.js:119-123`) and never published — it isn't carried by Agora, WebSocket, or
  anything else. It is a self-view mirror, not a media stream anyone else receives.
- `bot-audio.js:1-13`'s own header comment states the design intent: expose
  `mediaStreamTrack` "so the browser can publish it into the Agora channel as the 'AI
  Panel' participant." `room.js:137` calls `this.bot.attachAgora?.(this.agora)` — but
  `BotAudio` (bot-audio.js) has no `attachAgora` method. The optional-chaining call
  silently no-ops. This is the unfinished half of the intended design, not a
  misunderstanding on your part: **the code already says what it was supposed to do, and
  never got wired up.**

So today, Agora RTC join is vestigial on the frontend: a channel is joined, a token is
spent, and then nothing is ever sent or received over it.

### The ConvoAI path (server-side, currently unreachable — no credentials configured)

`backend/app/interview/agora_convoai.py` is a real, carefully-scoped integration: one
Agora ConvoAI agent per persona (own `agent_rtc_uid`, own TTS voice — `VOICE_MAP`,
lines 54-61), turn detection forced to `manual`/`manual` so Agora never makes a
turn-taking decision (lines 148-150), and the agent's own LLM is explicitly muted
(`"Say nothing on your own. Wait for broadcast messages."`, lines 161-163) — every word
comes from `/speak` (`session.py:1491-1492`, gated on `getattr(self, "_convoai", False)`).
This is the right shape for "Agora carries media and speech, Gemini/Moderator decide
what's said."

Two problems, both visible in the code, not hypothetical:

- **It never turns Gemini's own audio off.** `_pump()`'s `EV_AUDIO` branch
  (`session.py:1333-1345`) emits Gemini's native audio over the WebSocket unconditionally
  — there is no `if not self._convoai:` guard. If ConvoAI were reachable, the candidate
  would be sent two independently-generated recordings of the same line (Gemini's own
  voice over WS, Agora's TTS voice over the channel), synthesized from two different
  vendors, for every single turn — doubled TTS cost and cost of API calls for zero
  benefit, and if the frontend ever *did* start subscribing to Agora audio, the two would
  audibly overlap.
- **The frontend has no path to hear it even if the above were fixed** — per the "no
  subscribe" finding above. `agent_payload()` sets `"remote_rtc_uids": [str(candidate_uid)]`
  (`agora_convoai.py:142`), which tells the ConvoAI agent to *listen* for the candidate's
  published audio — but the candidate never publishes a track to Agora either (first
  bullet, previous section), so the agent's own ASR (`"asr": {"vendor": "ares", ...}`,
  configured but never read from — the transcript this app scores comes from Gemini's
  `input_audio_transcription`, not from Agora ASR) would also never receive anything.

Net effect: whether `VOICE_PROVIDER` resolves to `gemini` or `agora` today, **the
candidate's experience is identical** — Gemini native audio over a raw socket — because
neither the candidate's mic nor either AI voice ever actually crosses Agora RTC. The
ConvoAI branch currently spends an HTTP round trip per persona per turn (`CA.speak`,
`SPEAK_TIMEOUT_S = 3` at `agora_convoai.py:100`) to produce audio nobody hears.

---

## 2. Gap vs. the required architecture

Required:

```
Candidate mic → Agora RTC → AI/Interview layer → AI audio → Agora RTC → Candidate speaker
```

Actual:

```
Candidate mic → WebSocket → AI/Interview layer → AI audio → WebSocket → Candidate speaker
                                                              (Agora RTC: joined, idle)
```

| Requirement | Status | Evidence |
|---|---|---|
| Candidate mic published to Agora | ✅ Implemented (untested live — no credentials) | `room.js` `createCustomAudioTrack` + `agora.publish()` |
| Candidate video published to Agora | ✅ Implemented (untested live — no credentials) | `room.js` `createCustomVideoTrack` + `agora.publish()` |
| AI audio published to Agora as a real participant | ✅ Via the existing per-persona ConvoAI agents (untested live) | `agora_convoai.py` `agent_rtc_uid`; `room.js` now subscribes and plays them |
| Gemini Live used for reasoning/audio generation | ✅ Yes | `live_client.py:174-223` |
| Moderator owns turn-taking, not Agora or Gemini | ✅ Yes | `moderator.py` R1–R7, `automatic_activity_detection=disabled` (`live_client.py:217-219`) |
| One Agora channel per interview, not per persona | ✅ Yes | `InterviewSession.agora_channel`, one value; personas differ only by `bot_uid`/voice |
| No reconnect / re-publish per turn | ✅ Yes (for Gemini connections) | `_Floor.warm`/`acquire`, `session.py:134-193` — opened once, reused |
| Barge-in without disconnecting the media session | ✅ Yes (over WebSocket) | see §5 |
| No duplicate STT→LLM→TTS round trips | ✅ Fixed | `_pump()` no longer emits Gemini's own audio over the WebSocket while `_convoai` is active — see §1 update |

---

## 3. Where the 5–6 second delay actually comes from

The user asked to identify the hop, not just the total. Here is the deliberate,
code-visible latency budget for **one** candidate-turn → next-persona-turn cycle,
independent of any network or model inference time:

| Stage | Constant | Value | Source |
|---|---|---|---|
| Browser decides the candidate stopped talking | `HANG_MS` | 700 ms | `frontend/js/interview/mic-worklet.js:29` |
| Backend waits for the candidate's transcription to stop growing | `CAND_SETTLE_S` | 2.5 s | `backend/app/interview/session.py:56` |
| *(ceiling on the above if transcription stalls)* | `CAND_SETTLE_MAX_S` | 8 s | `session.py:63` |
| Backend waits for the incoming persona's audio generation to stop producing before flushing its turn | `TURN_SETTLE_S` | 2.5 s | `session.py:42` |
| *(hard ceiling on one persona turn)* | `TURN_MAX_S` | 12 s | `session.py:47` |

**700 ms + 2.5 s + 2.5 s = 5.7 s** of intentional, code-level quiet-window waiting between
"candidate stops talking" and "the next persona's line is considered final and the floor
visibly changes" — before counting a single millisecond of actual network transit or
Gemini inference. This lines up almost exactly with the reported 5–6 second delay.

This is not incidental — it is the direct, documented cost of two real bugs this same
codebase already fixed earlier the same day (see `git log`, commit
`baf84d8 "Fix the live interview: the panel spoke once, then went deaf"` and the
follow-up `a638d83 "Score the interview again..."`): scoring a candidate's answer before
transcription had finished arriving, and fragmenting one spoken sentence into several
turns because native-audio emits multiple `turn_complete` signals mid-sentence. Both
debounces are real fixes for real correctness bugs, not accidental slowness — but they
were tuned for *correctness*, not for *latency*, and nobody has since gone back to trim
them now that the correctness bug is fixed. Concretely:

- `CAND_SETTLE_S` resets its quiet-timer every time ANY new transcription text arrives
  (`_await_input_settled`, `session.py:1106-1141`) — so it *always* pays close to the
  full 2.5 s on a normal answer, not just on the pathological case it was built for.
- `TURN_SETTLE_S` likewise always waits the full 2.5 s after the model's last audio
  chunk, because native-audio's spurious mid-sentence `turn_complete` events are common
  enough that a shorter debounce would resurrect the exact bug `baf84d8` fixed.

**This means media transport (Agora vs. WebSocket) is very unlikely to be where the 5–6s
lives.** Moving audio onto Agora RTC would not remove either debounce; both are
turn-decision logic, entirely independent of which pipe the bytes travel over. If the
goal is to cut this delay, the two candidate levers are:

1. **Adaptive settle windows** — start the quiet-timer at a shorter value (e.g. 800 ms)
   and only extend it toward the current 2.5 s ceiling if evidence suggests a sentence
   is still incomplete (trailing conjunction, unclosed clause, or if the previous chunk
   arrived very recently) rather than always paying the worst case.
2. **A hard end-of-turn signal that skips the debounce entirely** — e.g. Gemini's
   explicit `turn_complete` *combined with* silence on the input transcription stream
   already having stopped for a much shorter window (transcription for native-audio
   models is normally a fixed ~200-400ms behind the audio, not seconds, except in the
   pathological case `CAND_SETTLE_S` was built for).

Neither of these is safe to do blind — both debounces were added to fix specific,
reproduced regressions this session, and a naive shortening reintroduces them. This
audit does not change either constant; it identifies them as the lever, with the
regression they guard against named, so whoever tunes them next tests against
`backend/tests/test_latency_and_noise.py` and the phase5/vad_turn_guard suites and
doesn't have to rediscover why they're 2.5 s in the first place.

---

## 4. Agora-specific checklist (as requested)

| Check | Finding |
|---|---|
| Agora SDK initialization | Loaded lazily, only once the interview actually starts (`interview.js:168-176`) — good, avoids a ~1.3 MB bundle cost on every page. |
| Channel join time | One `agora.join()` per interview (`room.js:136`), not per turn or per persona — correct cardinality, but currently joins a channel that nothing uses. |
| Microphone publication | **Missing.** No `createMicrophoneAudioTrack` / `publish()` call exists. |
| Audio track configuration | N/A — no track is ever created for Agora. The WebSocket path uses 16 kHz mono PCM16 (`mic-worklet.js:14-16`), which would need an equivalent Agora track config (`encoderConfig`, sample rate) once ported. |
| Audio encoding / sample rate / channel count | WebSocket path: 16 kHz mono 16-bit PCM in (mic), 24 kHz mono 16-bit PCM out (`bot-audio.js:16`, `_pump` PCM straight from Gemini). Neither has been mapped onto an Agora track profile yet. |
| Audio frame handling | 40 ms frames (640 samples @16 kHz), chosen explicitly to balance barge-in responsiveness against WebSocket framing overhead (`mic-worklet.js:15-17`) — a reasonable frame size to reuse for an Agora custom track. |
| AI bot audio publication | **Missing** — the `attachAgora` no-op described in §1. |
| Local playback | Implemented via Web Audio API (`bot-audio.js:41-64`), gapless-scheduled, with a documented flush-on-barge-in path. This part is solid engineering; it just isn't Agora. |
| Remote playback (subscribe) | **Missing** — no `.subscribe()` anywhere in the frontend. |
| Unnecessary subscribe/unsubscribe churn | None observed, because there is no subscribe activity at all yet. |
| Audio buffering | `BotAudio.enqueue()` schedules each PCM chunk on a running clock (`nextAt`) with a small (`40ms`) lead — no unbounded buffering; `pending` set is cleaned up via `onended`. |
| Reconnection behavior | Gemini Live connections: opened once per persona, reused via `_Floor`, never rebuilt mid-interview (`session.py:134-193`). Agora RTC: joined once per session, `leave()` only on teardown (`room.js:75-84, 337-343`) — correct cardinality, nothing to fix here structurally. |
| Network quality callbacks | **Not wired.** No `client.on('network-quality', ...)` or equivalent handler exists; there is currently no signal from Agora's connection health back into the UI or logs. |

---

## 5. Barge-in (already correct, already fast, already not disconnecting anything)

```
Candidate starts speaking
  │
  ▼
mic-worklet.js VAD (START_RMS=0.018, hysteresis) → postMessage('speech_start')
  │
  ▼
room.js:144-157  _onVoice(true)
  │  1. this.bot.flush()          — LOCAL playback stops within one JS tick (bot-audio.js:67-74)
  │  2. this._setSpeaking(null)   — UI reflects it immediately
  │  3. ws.send({type:'speech_start'})
  ▼
session.py on_speech_start (line 1015-1032)
  │  guarded by _awaiting_candidate / _persona_turn_open (room-noise filter)
  │  → _cancel_pending_flush(); if persona_turn_open: _flush_persona_turn() now, not later
  │  → if ConvoAI active: asyncio.create_task(_convoai_interrupt())  (fire-and-forget)
  ▼
Candidate is heard immediately; nothing about the WebSocket or Agora session is closed.
```

This already satisfies "stop/flush the AI audio stream while keeping the session alive" —
`bot.flush()` runs synchronously in the browser the instant VAD fires, which is the
dominant term in perceived barge-in latency (it does not wait on a round trip to the
server at all). The `_convoai_interrupt()` call is genuinely unnecessary right now (there
is no audio to interrupt on a channel nobody is listening to), but it is also harmless —
it's a fire-and-forget task with its own try/except, and it costs nothing on the
WebSocket path.

**Nothing here needs to change for barge-in specifically.** It should be re-verified once
media actually moves onto Agora (an Agora-published AI audio track needs a client-side
`unpublish`/mute-on-interrupt equivalent to what `bot.flush()` does today), but the
underlying principle — flush client-side immediately, tell the server, never
close/rejoin anything — is already the design, and it's a good one.

---

## 6. What would need to change to actually reach the target architecture

Not implemented in this pass — this is a report, and the instruction was explicit that
improvement must not be claimed without measurement. Recorded here as the concrete
next-step list so a follow-up task can be scoped without re-deriving it:

1. **Publish the candidate's mic to Agora.** `room.js`: after `agora.join()`, create an
   Agora audio track from the same `mediaStreamTrack` already captured for the worklet
   (or a low-level custom track sourced from the same `MediaStream`) and `publish()` it.
2. **Give the AI a real Agora presence.** Implement `BotAudio.attachAgora()` for real:
   create an Agora custom audio track from `this.streamDest.stream.getAudioTracks()[0]`
   (already exposed, already documented, never finished) and publish it — this is what
   makes the interviewer "a real participant" per the requirement, and it's most of the
   way built already.
3. **Stop double-generating audio when ConvoAI is active.** Gate `_pump()`'s `EV_AUDIO`
   emission on `not self._convoai` (`session.py:1343`), or drop the ConvoAI TTS path
   entirely in favor of publishing Gemini's own native audio as the Agora track from (2)
   — the latter is simpler and avoids paying for two TTS generations of the same line.
4. **Subscribe and play remote Agora tracks** if the candidate is meant to receive the AI
   voice as an Agora participant rather than (or in addition to) the WebSocket path — a
   `client.on('user-published', ...)` handler calling `subscribe()` and routing the
   remote track to an `<audio>` element.
5. **Wire `network-quality` and connection-state callbacks** into the existing `notice`/
   `status` UI channel, so a degraded Agora link surfaces to the candidate the same way
   a Gemini/voice failure already does (`_voiceWarning`, this session's fix).
6. **Instrumentation for a real latency table**, once (1)-(5) exist and real credentials
   are configured — add `time.monotonic()` timestamps at:
   - mic frame captured (worklet) → Agora publish ack
   - Agora → `on_audio()` receipt (if audio still round-trips through the backend at all
     once Agora carries it, or the equivalent point where the AI layer's audio pipeline
     receives it)
   - Gemini connection open → first `EV_AUDIO` chunk (already loggable today at
     `session.py:174` `connect()` return → `_pump`'s first `EV_AUDIO` branch)
   - AI audio ready → Agora publish
   - Agora → candidate `onended`/playback start
   
   Log each as a structured event (`{"hop": "...", "session_id": ..., "ms": ...}`) so a
   single interview produces the exact hop table this report was asked for, with real
   numbers instead of code-derived estimates.

None of the above touches Moderator, Analyst, or Scoring — per the critical rule, media
transport changes stay entirely inside the Agora/BotAudio/room.js layer and the one
`_pump()` emission guard in (3).

---

## 7. Success-criteria scorecard

| Criterion | Status |
|---|---|
| Agora-based candidate audio/video | ✅ Implemented, untested live (no credentials in this environment) |
| Agora-based AI audio delivery | ✅ Implemented via ConvoAI agents + subscribe, untested live |
| Gemini Live streaming | ✅ Already the reasoning/audio engine |
| Reliable candidate speech detection | ✅ Hysteresis VAD, tuned, already shipping |
| Reliable AI listening | ✅ Manual activity-window gating (this session's earlier fix) |
| Fast first audio response | ⚠️ Not measured — no live key in this environment; instrumentation plan above |
| Immediate barge-in | ✅ Already sub-tick client-side flush, independent of any hop |
| Deterministic R2 Product handoff | ✅ `moderator.py` R2, unchanged and unaffected by any of this |
| No unnecessary media reconnections | ✅ For Gemini connections and the Agora channel itself; N/A for Agora tracks since none are published yet |

**No latency numbers are claimed as improved in this report, because nothing was changed
that would move them — this was an inspection.** The one number stated with confidence
(5.7 s of deliberate settle-window waiting) is arithmetic on constants already in the
repository, not a measurement from a live run, and is called out as such.
