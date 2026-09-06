# The product tour video

Rebuilds `HireIQ_product_tour.mp4` — a ~7 minute narrated walkthrough covering the whole
track: post a role, import a résumé and match, the AI panel interview, the assessment,
the feedback the candidate receives, and the admin portal.

Nothing in the film is mocked. Every frame is a screen recording of the deployed service
driven through its real UI, and the interview is a real interview against Gemini Live.
The only things synthesised for the camera are the microphone and camera devices, which
Chromium has to be told to fake, and the candidate's answers.

## Running it

```bash
cd backend && ./.venv/bin/pip install playwright     # browsers are usually already cached
brew install ffmpeg                                  # ffmpeg + ffprobe must be on PATH

./backend/.venv/bin/python scripts/demo-video/record.py       # capture the segments
./backend/.venv/bin/python scripts/demo-video/rec_admin.py     # the admin segment
./backend/.venv/bin/python scripts/demo-video/cards.py         # render the title cards
./backend/.venv/bin/python scripts/demo-video/build_video.py   # narrate and assemble
```

Output and all intermediates land in `.demo-video/` (gitignored). Set `HIREIQ_VIDEO_DIR`
to put them somewhere else.

`rec_s4.py` re-shoots only the assessment segment. That one is worth shooting alone: it
is the part of the film buyers actually interrogate, and it wants a slow, uninterrupted
walk down the review page rather than whatever pace the full run happens to produce.

| script | what it does |
|---|---|
| `record.py` | drives the whole journey and records one `.webm` per segment |
| `rec_s4.py` | re-shoots the assessment segment only |
| `rec_admin.py` | re-shoots the admin portal segment only |
| `cards.py` | renders the title cards as HTML, screenshots them at 2× |
| `script_v2.py` | the narration copy — edit this, not `build_video.py` |
| `build_video.py` | narrates via Gemini TTS, then assembles everything with ffmpeg |

## Things that will bite you

**The fake microphone must be silent.** Chromium's default fake device emits a
*continuous tone*. The runtime reads signal energy to decide when the candidate is
speaking, so that tone is heard as someone talking without pause: the panel correctly
waits for a gap that never comes and never asks its first question. `record.py`
generates a silent WAV and points Chromium at it.

**`--use-fake-ui-for-media-stream` has to stay.** Without it `getUserMedia` is refused,
the room comes up "Not supported", and you film a dead session. It also auto-accepts the
camera, whose default fake feed is a lurid green test pattern — so the camera is pointed
at a plain dark frame instead.

**The candidate's answers are generated per question.** Canned clips answer whatever they
were recorded for, and the panel is adaptive, so they end up answering questions nobody
asked — the analyst reads that as evasion and the run scores 0. `answer_for()` also
passes the previous answers back in, because repeating the same opening every turn reads
as a scripted candidate and costs about 20 points.

**Narration is cached** on the text, keyed by a hash. Re-running `build_video.py` after a
pacing change will not re-bill or re-roll the TTS — which also keeps the durations the
timeline was balanced against stable.

**Clips auto-fit their narration.** A segment stretches (down to `MIN_SPEED`) so a line
is never cut off mid-word; below that floor it holds the last frame instead. If you see
"holding last frame" in the output, that segment needs more footage or less script.

**Trim the sign-ins.** Every segment starts by logging in and only the first one narrates
it. `TRIM` in `build_video.py` cuts the preamble off the rest.
