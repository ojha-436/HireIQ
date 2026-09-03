# HireIQ

Adaptive multi-persona AI interview panel — **PS11**, Track 1 (Coordinated AI Interview Panel).

- **What it is and why:** [`plan.md`](./plan.md)
- **How it is built:** [`ARCHITECTURE.md`](./ARCHITECTURE.md)
- **How it looks and why:** [`design-system/MASTER.md`](./design-system/MASTER.md)

---

## Run it locally

**One process.** The API serves the SPA at the same origin — there is no second port to
get wrong, and no CORS to configure.

### 1. Run it

```bash
cd backend
python3.12 -m venv .venv          # 3.12+ required (SQLAlchemy 2 mapped annotations)
./.venv/bin/pip install -r requirements.txt
cp .env.example .env              # dev defaults work as-is

./.venv/bin/uvicorn app.main:app --reload --port 8000
```

Then open **`http://127.0.0.1:8000/`** — that's the whole app.

- Health: `/api/health` · Interactive API docs: `/docs`
- To point the SPA at a *different* backend (split static server, remote API), set
  `localStorage.setItem('hireiq.apiBase', '<origin>')`. Nothing is hardcoded, and
  `test_frontend_does_not_hardcode_an_api_port` keeps it that way.

> **Port note:** if a PathFinder backend is already running on 8000, use `--port 8001`
> and open `http://127.0.0.1:8001/`. Since the API serves the page, the frontend follows
> the port automatically.

### 2. Demo data (optional)

Creates a tenant, two published roles, a draft role, a candidate, and one application —
by making **real HTTP calls**, not by inserting fixtures.

```bash
cd backend
./.venv/bin/python scripts/seed_scenarios.py   # role-play bank (6 scenarios)
./.venv/bin/python scripts/demo_seed.py        # tenant, 4 roles, candidate, 1 application
```

| Who | Email | Password |
|---|---|---|
| Employer (Northwind Systems) | `rea@northwind.com` | `correct-horse-battery` |
| Candidate | `sam@example.com` | `another-good-passphrase` |

---

## Tests

```bash
cd backend && ./.venv/bin/python -m pytest tests/ -q
```

Two gates matter:

- `test_full_phase1_flow` — employer posts a role → candidate applies → applicant appears employer-side.
- `test_ps11_r2_fires_in_a_live_interview` — **the PS11 centrepiece.** A live interview runs over
  the WebSocket; a technically correct answer that states no customer impact must move the floor
  to the product interviewer with `intent=challenge`. Deterministic and offline: `live_client`
  falls back to a local connection when `GEMINI_API_KEY` is unset.

The suite also asserts tenant isolation, JWT audience separation, that the WebSocket refuses to
open without a stored AI-disclosure timestamp, and that a foreign candidate cannot join.

---

## Layout

```
backend/
  app/
    main.py            FastAPI entrypoint (also the Alembic-free dev bootstrap)
    config.py          env-driven settings
    db.py              engine + session
    models.py          ORM — employer, candidate, and the interview-engine tables
    security.py        stdlib PBKDF2 + HS256 JWT, two audiences
    deps.py            auth dependencies; audience isolation lives here
    schemas.py         pydantic request/response contracts
    routers/           employer_auth, employer_jobs, candidate_auth, candidate_jobs
    services/skills.py JD -> skills + experience range -> proposed panel (model-free)
    services/resume.py resume -> skills + years; job match % (one shared vocabulary)
    engines/           compat shims for the ported engine (datasets, jd_parser, rag, profile)
    interview/         THE ENGINE (ported): moderator R1-R6, personas, analyst,
                       live_client (Gemini Live + local fallback), session runtime,
                       assessment, agora_token, broadcast,
                       agora_convoai (Agora TTS voice, manual turn detection),
                       scenarios (role-play engine + R7)
  tests/               Phase 1 gate, Phase 2 interview gate (PS11 R2)
  scripts/demo_seed.py

frontend/
  index.html
  css/  tokens.css (two registers) · base.css (components) · app.css (shells)
  js/   api.js · store.js · router.js · ui.js · app.js
        views/     auth · employer · candidate · interview · monitor ·
                   review (employer assessment) · application (candidate detail) · profile
        interview/ room.js (HireIQ shell) · bot-audio.js + mic-worklet.js (ported)
        vendor/    AgoraRTC SDK
        interview/  room.js (HireIQ shell) · bot-audio.js + mic-worklet.js (ported audio path)
        vendor/     AgoraRTC SDK

design-system/MASTER.md
Dockerfile
```

---

## Phase status

| Phase | Scope | State |
|---|---|---|
| 1 | Dual-audience auth, job posting, pipeline, candidate apply, both portal shells | **Done** — gate green |
| 2 | InterviewRuntime, Gemini Live per-persona connections, WS protocol, interview room | **Done** — R2 gate green |
| 3 | Shared context store, Analyst, moderator R1–R6, two-source detection | **Done** (ported) |
| 4 | Difficulty ladder, **scenario engine + R7**, SSE monitor with Panel Memory, whisper (W0) | **Done** |
| 5 | Job search filters + resume matching, employer pipeline builder | **Done** |
| 6 | Assessment review UI, advance/reject with reasons, gated feedback release, candidate profile + resume import | **Done** |
| 7 | Definition-of-Done suite (plan.md §8), mobile pass, Cloud Run deploy scripts | **Done** |

**Every phase in `plan.md` is complete.** What remains is the v2 list that was
explicitly cut from the sprint (see `ARCHITECTURE.md` §10): disputes, calibration
baselines, Agora Cloud Recording, D&I analytics, SMTP notifications, blind screening,
the human-only video room, and the job-ingestion crawler.

**All eleven PS11 capabilities are implemented and covered by tests.** The scenario
engine (#6) was the last gap; `test_phase6_scenarios.py` and
`test_r7_opens_a_roleplay_in_a_live_interview` cover it.

Full day-by-day plan with owners: [`plan.md` §6](./plan.md).

---

## The question bank (O*NET 31.0)

The interviewer's **fallback** question bank is derived from the **O*NET 31.0** database
(U.S. Department of Labor, **CC BY 4.0**) — 18,838 authoritative task statements across
923 occupations. Attribution and rationale: [`backend/data/onet/ATTRIBUTION.md`](backend/data/onet/ATTRIBUTION.md).

```bash
cd backend
python data/onet/fetch.py                                    # source CSVs (not committed)
python scripts/seed_questions.py --per-skill 6               # instant, offline
python scripts/seed_questions.py --per-skill 6 --rewrite     # + Gemini wording pass
```

It is a **fallback**. A question generated from what the candidate just said always
wins; the bank exists so a lull never becomes a silence. Every row stores
`source = "onet:<Task ID>"`, so any question a candidate is asked traces to a public
occupational record rather than to our guesswork.

**Why not Kaggle/HuggingFace.** The interview-question dumps there are anonymous scrapes
under unstated or "other" licences. For something that scores real people that provenance
is indefensible — and O*NET answers the harder question anyway: which areas is it
*legitimate* to probe for this role, rather than what someone once asked.

Three things `test_question_bank.py` now pins, each of which was broken:

| Guard | Why |
|---|---|
| `skill_id` is a **slug** (`system_design`) | `retrieval` filters on ids; seeding display names made all 640 rows **unreachable** |
| ≥50 questions **per persona** | "longest statement first" made the bank 70% technical; the hiring manager had **five** |
| Persona routing on the **leading verb** | `"manage"` matched inside `"management system"`, sending database work to the hiring manager |

## Gemini models

| Setting | Default | Used by |
|---|---|---|
| `GEMINI_MODEL` | `gemini-3.8-flash` | Analyst scoring, report prose, moderator tiebreak, segment summaries |
| `GEMINI_LIVE_MODEL` | `gemini-2.5-flash-native-audio-preview-12-2025` | The live voice connection, one per persona |

**Do not "upgrade" the Live model to a half-cascade id.** `gemini-3.1-flash-live-preview`
is newer but runs STT → LLM → TTS internally — the exact loop this architecture bypasses
to keep barge-in under 200 ms. Only swap it for another **native-audio** model.
`test_phase5_gemini.py` pins that.

All Gemini text calls go through `app/interview/gemini.py` on the current `google-genai`
SDK. Every call has a deterministic fallback, so a timeout degrades scoring rather than
ending an interview — and `available()` reports honestly whether a real model is reachable.

## Agora

Two separate integrations, both credential-gated:

| | What it does | Needs |
|---|---|---|
| **RTC** | Carries candidate camera/mic and the panel's audio as channel participants | `AGORA_APP_ID`, `AGORA_APP_CERTIFICATE` |
| **Conversational AI (voice)** | Speaks each persona's line through Agora TTS, one agent per persona with its own voice | also `AGORA_CUSTOMER_ID`, `AGORA_CUSTOMER_SECRET` |

### TTS vendor — the one setting that will waste your afternoon

`AGORA_TTS_VENDOR` defaults to **`openai`**. Verified against a live project:

| Vendor | Result |
|---|---|
| `openai`, `elevenlabs`, `google` | Agent joins in ~1.5s on Agora-managed credentials |
| `microsoft` (Azure), `cartesia`, `deepgram` | **400 `ErrInternal` after a 30-second stall** — they need BYOK keys |

Without BYOK credentials Agora dials the vendor, gets nothing, and stalls. That stall is
what reads as "Agora is unreachable" — the network is fine and the credentials are fine.
`test_phase4_agora.py` pins the vendor to one that needs no external keys.

Two related gotchas, both fixed and tested: agent `name` must be unique per attempt
(reuse returns `409 TaskConflict`, which on a reconnect looks like refusal), and the
boot must run **off** the critical path — awaiting it blocked the first interviewer for
the full timeout per persona.

`VOICE_PROVIDER=auto` (default) uses Agora ConvoAI when those credentials exist and falls
back to Gemini Live native audio otherwise. `gemini` forces the fallback.

### Where the credentials live in the Agora Console

They are in two different places, which is the usual source of confusion:

| Credential | Where | Notes |
|---|---|---|
| **App ID**, **Primary/Secondary Certificate** | **Projects → your project → Config** | Project-level |
| **Customer ID**, **Customer Secret** | **Developer Toolkit → RESTful API → Add a secret** | **Account-level, not inside a project** |

For the key pair: click **Add a secret** → **OK**, then **Download** in the Customer Secret
column and keep `key_and_secret.txt` somewhere safe. **The secret is downloadable only
once** — lose it and you must delete the pair and generate a new one.

Conversational AI also has to be enabled for the project (the Console shows a
"Conversational AI Agents are here → Build Agents" entry when it is available).

**The load-bearing detail:** ConvoAI is started with `turn_detection.sos_mode` and
`eos_mode` both set to `manual`, and every persona line is pushed through
`POST /agents/{id}/speak` verbatim. Agora provides the voice; the Moderator keeps the
floor. If Agora's own turn detection were enabled, rules R1-R6 would be bypassed and
"controlled interviewer turn-taking" would stop being true. `test_phase4_agora.py`
pins that setting.

## Deploy

```bash
export GCP_PROJECT=your-project AGORA_APP_ID=your_app_id
# one-time: create the secrets — see SECRETS.md
./deploy.sh
```

Secrets come from Secret Manager; nothing sensitive is baked into the image
(`test_the_image_does_not_bake_in_secrets` enforces that). The scenario bank is seeded
at build time, because an empty `scenarios` table means R7 silently never fires.

`--max-instances 1` is a **demo constraint**, not the scaling story: live sessions hold
in-memory runtime state and an in-process SSE broadcast. Multi-instance is a Redis pub/sub
swap behind the same two interfaces, with no change to the API contract.
