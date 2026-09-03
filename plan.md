# HireIQ — Adaptive Multi-Persona AI Interview Panel

> **Problem statement:** PS11 — Adaptive Voice Interview Platform (Track 1, Coordinated AI Interview Panel)
> **Team:** Prince Kumar Ojha (Team Lead / AI Architect) · Abhishek Kumar Gupta (WebRTC & Media Engineer)
> **Status:** Build — 6-day sprint to demo
> **Prior submission:** `TeamName_EchoSphere2026_IdeaSubmission.pdf` (PathFinder)

---

## 0. The Pivot (say this out loud to judges, don't hide it)

Round II was submitted as **PathFinder** — a candidate-side career platform whose Stage 3 was the AI
panel. Building it revealed that the panel *is* the product and the job-ingestion crawler was
scaffolding around it.

**HireIQ keeps the same engine and puts it where it creates value: inside a real hiring decision.**

The one-line story:

> "We shipped the interview panel from our submission. What changed is who it serves — we moved it
> from mock-interview practice to the live hiring screen, because the same engine that gives a
> candidate evidence-linked feedback is what gives an employer a defensible, auditable hiring
> decision. Everything PS11 asks for got stronger; the crawler got cut."

Three other reconciliations against the PDF:
- **Python 3.9 → 3.12.** SQLAlchemy 2's mapped annotations cannot resolve `X | None` on 3.9.
- **Panel size 3 → 5.** Customer and Behavioural personas were added because PS11 names them, and the
  Customer persona is what makes role-play scenarios work.
- **Transcript retention 180 → 60 days.** Tightened after privacy review. Cited quotes are snapshotted
  into the assessment before purge, so evidence links stay clickable forever.

---

## 1. PS11 Capability Coverage — the scoring map

This table is the plan. Every row must be a **runtime behaviour visible in the demo**, not a database
column. Three rows were previously columns-only; they are now first-class engines (§3).

| # | PS11 requirement | Mechanism | Where it's visible in the demo |
|---|---|---|---|
| 1 | Real-time, interruptible voice | Agora RTC media + Gemini Live native audio; client-side hysteresis VAD in AudioWorklet; symmetric barge-in flushes playback buffer | Candidate talks over an interviewer mid-sentence; audio cuts instantly |
| 2 | Multiple interviewer roles / personalities | 5 personas, 5 distinct Gemini voices, 5 distinct system prompts + probe charters | Five avatar tiles; the speaking one pulses |
| 3 | **Shared candidate context** | `CandidateContext` blackboard — server-owned, append-only, rebuilt into every persona prompt before every turn (§3.1) | **"Panel Memory" panel on the recruiter monitor** shows facts/threads carried between personas live |
| 4 | Dynamic follow-up questions | Follow-ups are generated per turn from open threads + coverage gaps; the question bank is a *fallback*, not the script (§3.4) | Trace rail labels each question `generated` vs `bank` |
| 5 | Controlled turn-taking | Deterministic server-side Moderator, single floor token, rules R1–R7 + W0 in priority order (§4) | Trace rail prints the rule ID that fired for every handoff |
| 6 | Role-play / scenario questions | Scenario engine with escalation ladder bound to difficulty; owned by Customer / Hiring Manager personas (§3.3) | Customer persona role-plays an angry enterprise customer mid-interview |
| 7 | Difficulty adjustment | Deterministic ladder on rolling Analyst score; 1–5; filters bank + injected into persona prompt (§3.2) | Monitor shows `Difficulty 3 → 4 ▲` badge flipping live |
| 8 | Vague / contradictory answers | Two-source detection: deterministic pre-filter + Analyst confirmation (§3.5) | R1 fires and highlights **both** conflicting turns in the transcript |
| 9 | Evidence-based feedback | Every report line carries `turn_ids`; uncited lines dropped server-side before persist | Report lines are clickable → scroll + highlight the transcript turn |
| 10 | Structured final assessment | Per-dimension scores, arithmetic `overall` computed in Python, recommendation, difficulty trajectory | Final report page |
| 11 | Clear AI disclosure | Four layers: consent modal (timestamped), persistent badge, spoken by first persona, printed in report | Visible in all four places during the run |

**Nothing ships that doesn't serve a row in this table, or the employer spine in §5.**

---

## 2. The Panel

| Persona | Voice | Probe charter | Owns |
|---|---|---|---|
| **Technical Interviewer** | Charon | Correctness, depth, trade-offs, scalability | Opening technical question, depth probes |
| **Product Manager** | Kore | Customer impact, metrics, business value, prioritisation | **R2 challenges** |
| **Hiring Manager** | Orus | Ownership, seniority, delivery, personal contribution ("I" vs "we") | R4 resume challenges, scenario option |
| **Customer** | Aoede | Plain-language clarity, empathy, expectation-setting | R6 jargon, **primary scenario owner** |
| **Behavioural** | Leda | STAR structure, failure, conflict, self-awareness | Behavioural depth |

Employer picks the panel per stage. Default panel is proposed from the job's `required_skills_json`.
Demo preset = `panel` (Technical + Product + Customer + Hiring Manager).

---

## 3. The Three Engines (this is the actual product work)

### 3.1 Shared Context Blackboard — `CandidateContext`

The PS11 requirement is *within one session, across personas*: the Product Manager must know what the
candidate told the Technical Interviewer. This is not conversation history replay — it is a
**structured, server-owned working memory** rebuilt into each persona's prompt before that persona
takes the floor.

```python
CandidateContext:
  facts[]         # {id, turn_id, claim, kind: experience|metric|tool|decision|scope, value, confidence}
  claim_index{}   # normalized_key -> [fact_ids]        (drives contradiction detection)
  coverage{}      # dimension -> {asked_by[], samples[], last_turn_seq}
  open_threads[]  # {id, opened_by, topic, why_unresolved, priority, target_persona}
  difficulty      # {level: 1-5, history: [(turn_seq, level, reason)]}
  flags[]         # {kind: vague|contradiction|no_impact|jargon|unsubstantiated, turn_ids[]}
  scenario        # active scenario state or null
  resume_claims[] # parsed from application at session start — seeds claim_index
```

**Handoff briefing.** When the floor moves to persona P, the server injects a compact block:

```
PANEL MEMORY (what the candidate has already told the panel):
- To Technical (turn 4): "we added a Redis write-through cache, cut p99 from 800ms to 90ms"
- Coverage: correctness=82 (technical), impact=UNPROBED, ownership=UNPROBED
- OPEN THREAD #3 (opened by technical, for you): candidate never stated who this helped
  or what it was worth. Challenge the business implications.
- Difficulty: 4/5. Do not accept a surface answer.
```

**Why this wins the demo:** it is rendered verbatim on the recruiter monitor as the *Panel Memory*
panel. Judges do not have to trust that context is shared — they watch a fact move from the
Technical Interviewer's turn into the Product Manager's mouth.

Persisted to `interview_sessions.state_json` after every turn so a reconnect resumes intact.

### 3.2 Difficulty Ladder — deterministic, never model-decided

```
after each scored turn:
  rolling = mean(overall of last 2 scored turns)
  if rolling >= 75 for 2 consecutive turns and level < 5:  level += 1  reason="sustained_strength"
  elif rolling <= 40 and level > 1:                        level -= 1  reason="struggling"
  else:                                                     hold
```

Level starts at 3 (or from `pipeline_stages.interview_config_json.start_difficulty`).

The level does two things:
1. **Filters the question bank** — `interview_questions.difficulty <= level`
2. **Enters the persona prompt** — level 1–2: scaffold, offer hints, accept partial answers. Level 4–5:
   assume seniority, demand trade-offs and numbers, escalate scenarios, do not accept the first answer.

Every change emits `{type:"difficulty", from, to, reason, turn_seq}` on the trace stream and is written
to `context.difficulty.history`, which becomes the **difficulty trajectory chart** in the final report.

### 3.3 Scenario / Role-Play Engine

```
scenarios
  id, role_family, title, persona_owner, difficulty_floor,
  setup_text, injects_json[], escalations_json{level -> escalation_text},
  success_signals_json[], skills_json[]
```

**Rule R7** fires when: ≥2 Q&A exchanges have covered a target skill, no scenario has run this session,
and ≥4 minutes remain. Floor goes to `persona_owner`, who reads `setup_text` and **stays in character
until the moderator releases the floor** — the persona does not narrate that it is role-playing.

Escalation is bound to difficulty. Demo scenario (Customer persona), extending the PS11 example:

> **L3:** "I'm the enterprise customer on your caching release. Since Tuesday my nightly report shows
> yesterday's numbers. Walk me through what happened."
> **L4 escalation:** "That's the second incident this quarter. My CFO signs your renewal in March."
> **L5 escalation:** "I want a written commitment on staleness bounds before I approve anything else."

Scenario turns are scored on `user_insight`, `clarity`, `ownership`, `prioritisation` — not correctness.

### 3.4 Dynamic Follow-Up Generation

The question bank is a **fallback**, never the script. Each turn:

1. Analyst returns flags + dimension scores + `unresolved` list for the answer just given.
2. Moderator picks the rule (§4), which names the next persona and an *intent* (`clarify`, `challenge_impact`, `press_specifics`, `verify_claim`, `plain_language`, `scenario`, `rotate`).
3. The persona generates the question **from the intent + Panel Memory + the candidate's literal words**, quoting them back.
4. Bank question is pulled only when intent is `rotate` and there is no open thread — i.e. when opening a genuinely new area.

Trace rail labels every question `generated` or `bank`. Target: ≥70% generated in the demo run.

### 3.5 Vague & Contradictory Answer Detection

Two-source, so it is not "we asked the LLM and hoped."

| Flag | Deterministic pre-filter | Analyst confirmation |
|---|---|---|
| `vague` | 0 numerals AND 0 known tool/proper nouns AND word_count > 25 | `is_vague: true` + `missing: []` |
| `contradiction` | new fact's `normalized_key` already in `claim_index` with an incompatible value | `contradicts: fact_id` + `why` |
| `no_impact` | — | `correctness >= 70` AND `impact_stated == false` → fires **R2** |
| `unsubstantiated` | claim's `normalized_key` absent from `resume_claims` and stated as personal ownership | `attributable: false` |
| `jargon` | term density above threshold against a common-tech lexicon | `needs_plain_language: true` |

A flag needs **both** sources to fire a moderator rule (except `no_impact`, which is Analyst-only by
nature). Both `turn_ids` are stored on a contradiction so the report can highlight the pair.

---

## 4. Moderator Rules (priority order, first match wins)

| Rule | Condition | Action | Target |
|---|---|---|---|
| **W0** | Employer whispered a question | Surface it verbatim at the next handoff | Current persona |
| **R1** | Contradiction confirmed | Clarify immediately, quote both statements | Same persona |
| **R2** | `correctness >= 70` AND `impact_stated == false` | **Challenge business implications** | Product Manager |
| **R3** | `vague` confirmed | Press for specifics — numbers, named tools, dates | Same persona |
| **R4** | Personal claim not substantiated by resume | Challenge the claim | Hiring Manager |
| **R5** | One persona has held ≥3 consecutive turns | Rotate to the least-served persona | Least coverage |
| **R6** | Dense unexplained jargon | Ask for a plain-language explanation | Customer |
| **R7** | ≥2 exchanges on target skill, no scenario yet, ≥4 min left | Open a role-play scenario | Scenario owner |
| — | default | Next question in coverage-gap order | Least coverage |

**R2 is the PS11 centrepiece and is a hard rule, not a prompt suggestion.** It fires or it doesn't, and
`test_ps11_r2_fires_on_impactless_correct_answer` is a required test.

---

## 5. Employer Spine (deliberately narrow — this is the enterprise story, not a portal)

| In (6-day scope) | Out (labelled v2 in the docs and the deck) |
|---|---|
| Register tenant + admin | Team invites, RBAC beyond admin |
| Post job (JD → auto skill extraction) | Job ingestion crawler / Bright Data |
| Configure stages + pick panel + start difficulty | Multi-stage auto-advance thresholds |
| Applications list grouped by stage | Drag-and-drop Kanban |
| Start AI interview on an application | Scheduling, reminders |
| **Live monitor: transcript + Panel Memory + trace rail + scores** | — |
| **Whisper a question (W0)** | Pause / full takeover |
| Assessment report with clickable evidence | Percentile *(only rendered when n≥5 real sessions; hidden otherwise — no fabricated benchmark)* |
| Advance / reject with mandatory reason → audit log | Offer management |
| Release feedback to candidate | Dispute workflow |

**Candidate side:** register → browse open jobs → apply → consent modal → interview room → track status
→ view released feedback. That's it.

### Explicitly cut for the sprint
Human-only video interview room · Agora Cloud Recording + GCS · disputes · calibration baselines ·
D&I analytics · SMTP notifications · blind screening · learning pathways · job crawler
*(seed 8–10 real JDs by hand instead).*

Each of these is one line in ARCHITECTURE.md under "v2", so the design is visibly considered without
being built.

---

## 6. Six-Day Build Plan

**P1 = Prince (backend / AI engine) · P2 = Abhishek (media / frontend)**

| Day | P1 — engine | P2 — media & UI | End-of-day gate |
|---|---|---|---|
| **1** ✅ | FastAPI skeleton, SQLite, dual-audience JWT, tenant/candidate models, job posting + pipeline + apply endpoints, JD→skills→panel service | App shell, hash router, both portal shells + design system, job board, apply flow | **DONE** — `test_full_phase1_flow` green; 9/9 tests pass |
| **2** ✅ | PORTED from the PathFinder repo: `InterviewRuntime`, per-persona Gemini Live connections, floor token, WS protocol, 5 persona prompts | Interview room: 5 tiles, mic AudioWorklet @16kHz, hysteresis VAD, bot audio playback + Agora republish, **barge-in flush** | A single persona holds a real interruptible voice conversation |
| **3** | **`CandidateContext` blackboard**, Analyst scoring loop, moderator R1–R6, two-source detection | Trace rail (rule IDs live), speaking-state animation, consent modal + persistent AI badge | R2 fires end-to-end in a live session; handoff briefing visible in logs |
| **4** | **Difficulty ladder**, **scenario engine + R7**, seed 6 scenarios, dynamic follow-up generation | SSE monitor page: transcript + **Panel Memory panel** + difficulty badge + whisper box | Difficulty flips on screen; Customer persona runs a scenario |
| **5** | Assessment engine (arithmetic overall, citation enforcement, drop-uncited), advance/reject + audit, feedback release | Report renderer: dimensions, clickable citations → transcript highlight, difficulty trajectory chart | Full run produces a report where every line jumps to a real turn |
| **6** ✅ | E2E `test_full_hiring_flow_ps11_fires` + the whole §8 suite, 4-layer disclosure audit, `deploy.sh` + `SECRETS.md`, `.env` loading | Mobile pass: 16/16 views clean at 390px and 768px | **DONE** — 137 tests green |

**Daily 20-minute sync at day end. If a day's gate misses, cut from §5's "In" column — never from §1.**

---

## 7. Demo Script (9 minutes, rehearsed)

| Time | Beat | PS11 rows proven |
|---|---|---|
| 0:00 | Employer opens application → Start AI Interview. Candidate joins: **consent modal**, badge visible | 11 |
| 0:30 | Technical Interviewer greets, **spoken AI disclosure**, opens at difficulty 3 | 1, 2, 11 |
| 1:15 | Candidate answers correctly about the caching layer — **no customer impact** | — |
| 1:45 | **R2 fires.** Floor → Product Manager, who quotes the candidate back and challenges business impact. Panel Memory panel updates on the monitor | **3, 4, 5, 8** |
| 3:00 | Candidate gives a vague answer → **R3**, pressed for numbers | 8 |
| 3:45 | Candidate **interrupts mid-question** — audio cuts instantly | **1** |
| 4:15 | Two strong turns → monitor shows **`Difficulty 3 → 4 ▲`** | **7** |
| 4:45 | **R7 fires.** Customer persona opens the stale-cache role-play, escalates at L4 | **6** |
| 6:15 | Candidate says "team of 3" after earlier saying "led a team of 8" → **R1**, both turns highlighted | **8** |
| 7:00 | Employer types a whisper → **W0** surfaces it at the next handoff | 5 |
| 7:45 | End interview → report: dimensions, recommendation, difficulty trajectory | **10** |
| 8:15 | Click an evidence line → transcript scrolls and highlights the exact turn | **9** |
| 8:45 | Employer advances with reason → audit log entry → candidate sees released feedback | — |

---

## 8. Definition of Done — ALL GREEN

`pytest tests/test_definition_of_done.py -v` (21 tests). Required tests:
- [x] `test_ps11_r2_fires_on_impactless_correct_answer` — the centrepiece rule
- [x] `test_context_carries_fact_across_personas` — a fact stated to Technical appears in the PM's briefing
- [x] `test_difficulty_raises_after_two_strong_turns` and `test_difficulty_lowers_when_struggling`
- [x] `test_contradiction_flags_both_turn_ids`
- [x] `test_uncited_report_line_is_dropped`
- [x] `test_overall_is_arithmetic_not_model` — assert against hand-computed mean
- [x] `test_scenario_r7_fires_and_escalates_at_level_4`
- [x] `test_session_cannot_go_live_without_disclosure_timestamp`
- [x] `test_full_hiring_flow_ps11_fires` — post job → apply → interview → report → advance

Manual gates:
- [x] Barge-in cuts audio in <200ms, both directions *(symmetric: client buffer flush + server activity_end + Agora /interrupt)*
- [x] AI disclosure present in all four layers *(`test_ai_disclosure_present_in_all_four_layers`)*
- [ ] ≥70% of questions in a demo run are `generated`, not `bank` *(not instrumented — see Open items)*
- [x] Every report line clicks through to a real transcript turn *(`test_report_lines_are_all_click_through_able`)*

---

## 9. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Gemini Live per-persona connections exhaust quota or add handoff latency | High | Lazy-open, idle-close at 90s, pre-warm the *next* persona the moment a rule selects it |
| Analyst latency stalls turn-taking | Med | Analyst runs async; moderator uses the previous turn's flags if scoring hasn't landed by handoff |
| Scenario persona breaks character / narrates the role-play | Med | Explicit prompt constraint + one canned opening line per scenario; verified in rehearsal |
| Two-sided scope eats the engine days | **High** | §5 "In" column is the cut list. Employer spine is 1.5 days total, not more |
| Cloud Run single-instance is a demo config, not a scaling story | Certain | Say so plainly; state the Redis pub/sub swap as the multi-instance path |
| Live demo network failure | Med | Record a clean backup run on Day 6 |

---

## 10. Honest Positioning Notes

- **Say `--max-instances: 1` is a demo constraint.** Lead with "in-memory runtime registry; swap to
  Redis pub/sub for multi-instance, API contract unchanged." Claiming it as session affinity invites a
  question you'll lose.
- **Don't fabricate a percentile.** Compute against real sessions, show `n`, hide below n=5. In a
  hiring product, a made-up benchmark is the one thing a judge can call unethical.
- **Lead the demo with R2.** It's the scenario the problem statement itself names. Show it in the first
  two minutes, before attention drifts.
