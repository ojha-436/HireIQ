"""The Definition of Done from plan.md §8, using the names the plan names.

These are not extra coverage — each one backs a claim the product makes to a reviewer.
They live in one file so the exit criterion can be checked with a single command:

    pytest tests/test_definition_of_done.py -v
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.interview import scenarios as SC
from app.interview.analyst import analyse_local
from app.interview.assessment import _evidence, compute_overall
from app.interview.moderator import Moderator
from app.main import app
from app.models import InterviewAssessment, InterviewSession, InterviewTurn, Scenario

JD = ("Senior Backend Engineer. 5-8 years. Python, Kafka, Kubernetes, AWS, Redis. "
      "Strong system design and API design. Mentor engineers, join the on-call rotation.")

CORRECT_NO_IMPACT = (
    "I added a Redis write-through cache in front of the read path and partitioned the "
    "Kafka topic by tenant, which cut p99 latency from 800ms to 90ms.")

STRONG = {"scores": {"correctness": 5, "depth": 5, "impact": 5, "structure": 5,
                     "ownership": 5}, "impact_stated": True,
          "specificity": "specific", "flags": [], "turn_id": "t1"}
WEAK = {"scores": {"correctness": 1, "depth": 1, "impact": 1, "structure": 1,
                   "ownership": 1}, "impact_stated": False,
        "specificity": "vague", "flags": [], "turn_id": "t2"}


@pytest.fixture(scope="module")
def client():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(Scenario).count() == 0:
            import scripts.seed_scenarios as seed  # noqa: PLC0415
            for row in seed.SCENARIOS:
                db.add(Scenario(**row))
            db.commit()
    finally:
        db.close()
    with TestClient(app) as c:
        yield c


# ===========================================================================
# 1. The centrepiece rule
# ===========================================================================
def test_ps11_r2_fires_on_impactless_correct_answer():
    """A technically correct answer that states no customer impact moves the floor
    to the product interviewer. This is the problem statement's own example."""
    mod = Moderator(["tech", "product", "hiring_manager"], required_skill_ids=["python"])
    mod.note_turn("tech")

    analysis = analyse_local(answer=CORRECT_NO_IMPACT, persona="tech", turn_id="t1",
                             required_skill_names=["Python", "Kafka", "System Design"])
    assert analysis["scores"]["correctness"] >= 3, analysis["scores"]
    assert analysis["impact_stated"] is False, "the answer names no beneficiary"

    mod.ingest(analysis, target_skill_id="python")
    directive = mod.decide(analysis, target_skill_id="python")

    assert directive["rule"] == "R2", directive
    assert directive["next_speaker"] == "product"
    assert directive["intent"] == "challenge"


def test_r2_does_not_fire_when_impact_is_stated():
    """The rule must discriminate, or it is just "always hand off to product"."""
    mod = Moderator(["tech", "product"], required_skill_ids=["python"])
    mod.note_turn("tech")
    analysis = dict(STRONG, turn_id="t1")
    mod.ingest(analysis, target_skill_id="python")
    assert mod.decide(analysis, target_skill_id="python")["rule"] != "R2"


# ===========================================================================
# 2. Shared candidate context
# ===========================================================================
def test_context_carries_fact_across_personas():
    """A fact stated to the technical interviewer must appear in the product
    interviewer's briefing. This is PS11 requirement #3."""
    mod = Moderator(["tech", "product"], required_skill_ids=["python"])
    mod.note_turn("tech")

    analysis = analyse_local(answer=CORRECT_NO_IMPACT, persona="tech", turn_id="turn-a",
                             required_skill_names=["Python", "Kafka"])
    mod.ingest(analysis, target_skill_id="python")

    # The number the candidate gave is now panel-wide knowledge.
    established = mod.established["numbers"]
    assert established, "no fact was established from a specific answer"
    assert any("800" in str(v.get("value")) or "90" in str(v.get("value"))
               for v in established.values()), established

    directive = mod.decide(analysis, target_skill_id="python")
    assert directive["next_speaker"] == "product"

    briefing = mod.directive_block(directive, {"turn-a": CORRECT_NO_IMPACT})
    assert "Redis" in briefing or "Kafka" in briefing, (
        f"the incoming persona was not told what the candidate said:\n{briefing}")
    assert "impact" in briefing.lower(), "the open concern did not reach the briefing"


# ===========================================================================
# 3. Difficulty adjustment
# ===========================================================================
def test_difficulty_raises_after_two_strong_turns():
    mod = Moderator(["tech"], required_skill_ids=["python"])
    start = mod.band_for("python")
    for _ in range(3):
        mod.ingest(STRONG, target_skill_id="python")
    assert mod.band_for("python") > start, (
        f"difficulty stayed at {start} through three strong answers")


def test_difficulty_lowers_when_struggling():
    mod = Moderator(["tech"], required_skill_ids=["python"])
    for _ in range(3):
        mod.ingest(STRONG, target_skill_id="python")
    high = mod.band_for("python")
    for _ in range(4):
        mod.ingest(WEAK, target_skill_id="python")
    assert mod.band_for("python") < high, (
        f"difficulty stayed at {high} while the candidate struggled")


def test_difficulty_never_leaves_the_one_to_five_band():
    mod = Moderator(["tech"], required_skill_ids=["python"])
    for _ in range(30):
        mod.ingest(STRONG, target_skill_id="python")
    assert 1 <= mod.band_for("python") <= 5
    for _ in range(40):
        mod.ingest(WEAK, target_skill_id="python")
    assert 1 <= mod.band_for("python") <= 5


# ===========================================================================
# 4. Contradiction detection
# ===========================================================================
def test_contradiction_flags_both_turn_ids():
    """A contradiction is only useful if the report can show BOTH statements."""
    first = analyse_local(answer="The team was eight engineers.", persona="hiring_manager",
                          turn_id="turn-1", required_skill_names=["Mentorship"])
    established = {"numbers": {}}
    import re
    for token in first.get("numbers") or []:
        key = re.sub(r"[\d.,]", "", str(token)).strip().lower()
        if key:
            established["numbers"][key] = {"value": str(token).strip(), "turn_id": "turn-1"}

    second = analyse_local(answer="Actually the team was three engineers.",
                           persona="hiring_manager", turn_id="turn-2",
                           established=established, required_skill_names=["Mentorship"])

    contradictions = [f for f in second["flags"] if f["type"] == "contradiction"]
    assert contradictions, second["flags"]
    turn_ids = contradictions[0]["turn_ids"]
    assert "turn-1" in turn_ids and "turn-2" in turn_ids, turn_ids


def test_contradiction_outranks_every_other_rule():
    mod = Moderator(["tech", "product", "customer"], required_skill_ids=["python"])
    mod.note_turn("tech")
    analysis = {
        "scores": {"correctness": 5}, "impact_stated": False, "specificity": "vague",
        "turn_id": "t2",
        "flags": [
            {"type": "contradiction", "turn_ids": ["t1", "t2"]},
            {"type": "impact_gap", "turn_ids": ["t2"]},
            {"type": "jargon", "turn_ids": ["t2"]},
        ],
    }
    mod.ingest(analysis, target_skill_id="python")
    directive = mod.decide(analysis, target_skill_id="python")
    assert directive["rule"] == "R1", directive
    assert set(directive["must_reference_turn_ids"]) == {"t1", "t2"}


# ===========================================================================
# 5. Evidence enforcement
# ===========================================================================
def test_uncited_report_line_is_dropped():
    """No evidence, no line. An assessment claim without a real turn is an opinion."""
    turns = {"real-turn": {"text": "I partitioned the Kafka topic by tenant."}}

    kept = _evidence("Handled partitioning.", ["real-turn"], turns)
    assert kept is not None
    assert kept["turn_ids"] == ["real-turn"]
    assert "Kafka" in kept["quote"], "the quote was not snapshotted"

    assert _evidence("Sounds senior.", [], turns) is None, "an uncited line survived"
    assert _evidence("Sounds senior.", ["ghost-turn"], turns) is None, (
        "a line citing a non-existent turn survived")
    assert _evidence("", ["real-turn"], turns) is None, "an empty claim survived"


# ===========================================================================
# 6. Arithmetic scoring
# ===========================================================================
def test_overall_is_arithmetic_not_model():
    """`overall` is computed in Python from stored per-dimension scores.

    Asserted against a hand-computed mean so no model can be prompted to inflate it.
    Dimensions are averaged first, then averaged together — a dimension probed once
    counts the same as one probed five times.
    """
    dim_scores = {"correctness": [4, 4], "depth": [3], "impact": [5]}
    # means: 4.0, 3.0, 5.0 -> mean of means 4.0 -> 4.0/5 * 100 = 80
    assert compute_overall(dim_scores) == 80

    # A dimension probed many times must not dominate.
    lopsided = {"correctness": [5, 5, 5, 5, 5, 5, 5, 5], "impact": [1]}
    # means: 5.0, 1.0 -> 3.0 -> 60
    assert compute_overall(lopsided) == 60

    assert compute_overall({}) == 0
    assert compute_overall({"correctness": []}) == 0


def test_unresolved_contradictions_reduce_the_score():
    clean = compute_overall({"correctness": [4]}, unresolved_contradictions=0)
    dirty = compute_overall({"correctness": [4]}, unresolved_contradictions=2)
    assert dirty < clean
    assert dirty >= 0


# ===========================================================================
# 7. Role-play
# ===========================================================================
def test_scenario_r7_fires_and_escalates_at_level_4():
    mod = Moderator(["tech", "product", "customer"], required_skill_ids=["python"],
                    max_turns=14)
    mod.pending_scenario_persona = "customer"
    mod.note_turn("tech")

    directive = None
    for _ in range(2):
        mod.ingest(STRONG, target_skill_id="python")
        directive = mod.decide(STRONG, target_skill_id="python")
        mod.note_turn(directive["next_speaker"])

    assert directive["rule"] == "R7", directive
    assert directive["intent"] == "scenario"
    assert directive["next_speaker"] == "customer"

    state = SC.ScenarioState("s1", "customer", "Stale cache",
                             {"4": "the renewal is at risk", "5": "put it in writing"}, [])
    state.note_turn()
    block = SC.continuation_block(state, 4)
    assert "the renewal is at risk" in block, block
    assert "put it in writing" not in block, "a level-5 escalation leaked at level 4"


# ===========================================================================
# 8. The disclosure gate
# ===========================================================================
def test_session_cannot_go_live_without_disclosure_timestamp(client):
    """Enforced server-side, not by the UI. Layer 1 of the AI disclosure."""
    emp = client.post("/api/employer/auth/register", json={
        "company_name": "Gate Check Ltd", "full_name": "Ines Roth",
        "email": "ines@gatecheck.com", "password": "a-long-enough-passphrase"}).json()
    emp_h = {"Authorization": f"Bearer {emp['token']}"}
    job = client.post("/api/employer/jobs/", headers=emp_h,
                      json={"title": "Backend Engineer", "jd_text": JD}).json()
    client.post(f"/api/employer/jobs/{job['id']}/publish", headers=emp_h)

    cand = client.post("/api/candidate/auth/register", json={
        "full_name": "Tam Ojo", "email": "tam@example.com",
        "password": "another-long-passphrase"}).json()
    cand_h = {"Authorization": f"Bearer {cand['token']}"}
    app_row = client.post(f"/api/candidate/apply/{job['id']}", headers=cand_h).json()
    sid = client.post(f"/api/employer/applications/{app_row['id']}/start-interview",
                      headers=emp_h).json()["session_id"]

    db = SessionLocal()
    try:
        assert db.get(InterviewSession, sid).disclosure_accepted_at is None
    finally:
        db.close()

    # The socket must refuse, and the Agora token must refuse too.
    with pytest.raises(Exception):
        with client.websocket_connect(f"/api/interview/ws/{sid}?token={cand['token']}") as ws:
            ws.receive()
    assert client.get(f"/api/candidate/sessions/{sid}/agora-token",
                      headers=cand_h).status_code == 409

    client.post(f"/api/candidate/sessions/{sid}/consent", headers=cand_h)
    db = SessionLocal()
    try:
        assert db.get(InterviewSession, sid).disclosure_accepted_at is not None
    finally:
        db.close()
    assert client.get(f"/api/candidate/sessions/{sid}/agora-token",
                      headers=cand_h).status_code == 200


def test_ai_disclosure_present_in_all_four_layers(client):
    """plan.md §1 row 11. Four independent places, so removing one cannot hide it."""
    from app.interview import personas as P

    # Layer 3 — spoken by the persona, and layer 1 — the invariant that requires it.
    assert "AI" in P.AI_DISCLOSURE
    assert "DISCLOSURE" in P.INVARIANTS
    assert "you are an ai interviewer" in P.INVARIANTS.lower()

    # Layer 1 — the consent text the candidate must accept.
    from app.routers.interview import DISCLOSURE_TEXT
    assert "conducted by AI" in DISCLOSURE_TEXT

    # Layer 2 — the persistent badge in the room chrome.
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent.parent
    room = (root / "frontend" / "js" / "interview" / "room.js").read_text()
    assert "ai-badge" in room, "the room has no persistent disclosure badge"

    # Layer 4 — printed on every report.
    assert "ai_disclosure" in (root / "backend" / "app" / "routers" / "interview.py").read_text()


# ===========================================================================
# 9. THE EXIT CRITERION
# ===========================================================================
def test_full_hiring_flow_ps11_fires(client):
    """post job -> apply -> interview -> R2 fires -> report -> advance -> feedback.

    The single test the plan names as the exit criterion for the whole build.
    """
    # 1. Employer posts and publishes a role with a product interviewer on the panel.
    emp = client.post("/api/employer/auth/register", json={
        "company_name": "Exit Criterion Inc", "full_name": "Yara Bloom",
        "email": "yara@exitcriterion.com", "password": "a-properly-long-passphrase"}).json()
    emp_h = {"Authorization": f"Bearer {emp['token']}"}

    job = client.post("/api/employer/jobs/", headers=emp_h, json={
        "title": "Senior Backend Engineer", "jd_text": JD, "country": "India",
        "stages": [
            {"seq": 1, "name": "AI Panel", "kind": "ai_interview",
             "interview_config_json": {"panel": ["tech", "product", "customer"],
                                       "preset": "panel", "start_difficulty": 3}},
            {"seq": 2, "name": "Hiring Manager Call", "kind": "human_interview"},
        ]}).json()
    assert job["required_skills_json"], "no skills extracted, so nothing to interview against"
    client.post(f"/api/employer/jobs/{job['id']}/publish", headers=emp_h)

    # 2. Candidate registers, builds a profile, applies.
    cand = client.post("/api/candidate/auth/register", json={
        "full_name": "Idris Kane", "email": "idris@example.com",
        "password": "another-properly-long-pass"}).json()
    cand_h = {"Authorization": f"Bearer {cand['token']}"}
    client.patch("/api/candidate/auth/me", headers=cand_h, json={
        "years_experience": 6,
        "profile_sections_json": {"headline": "Backend engineer, 6 yrs",
                                  "skills": ["Python", "Kafka", "Kubernetes"]}})

    board = client.get("/api/candidate/jobs?sort=match", headers=cand_h).json()
    assert board["jobs"], "the published role never reached the candidate board"
    app_id = client.post(f"/api/candidate/apply/{job['id']}", headers=cand_h).json()["id"]

    # 3. Employer starts the interview; candidate accepts the disclosure.
    started = client.post(f"/api/employer/applications/{app_id}/start-interview",
                          headers=emp_h).json()
    sid = started["session_id"]
    assert "product" in [p["key"] for p in started["panel"]], "R2 has no product interviewer"
    client.post(f"/api/candidate/sessions/{sid}/consent", headers=cand_h)

    # 4. A live interview where every answer is correct and states no impact.
    traces, floors, answered = [], [], 0
    with client.websocket_connect(f"/api/interview/ws/{sid}?token={cand['token']}") as ws:
        for _ in range(600):
            msg = ws.receive()
            if msg.get("bytes") is not None or msg.get("text") is None:
                continue
            ev = json.loads(msg["text"])
            kind = ev.get("type")
            if kind == "floor":
                floors.append(ev["persona"])
            elif kind == "trace":
                traces.append(ev)
            elif kind == "your_turn":
                if answered >= 4:
                    ws.send_text(json.dumps({"type": "end"}))
                    continue
                answered += 1
                ws.send_text(json.dumps({"type": "text", "text": CORRECT_NO_IMPACT}))
            elif kind == "ended":
                break
            elif kind == "error":
                raise AssertionError(f"Interview errored: {ev}")

    # 5. PS11 R2 must have fired.
    r2 = [t for t in traces if t.get("rule") == "R2"]
    assert r2, f"R2 did not fire. Rules: {[t.get('rule') for t in traces]}"
    assert r2[0]["next"] == "product" and r2[0]["intent"] == "challenge", r2[0]
    assert "product" in floors, floors

    # 6. An assessment exists, is arithmetic, and is gated.
    packet = client.get(f"/api/employer/applications/{app_id}/assessment",
                        headers=emp_h).json()
    rnd = packet["rounds"][0]
    assessment = rnd["assessment"]
    assert assessment is not None
    assert 0 <= assessment["overall"] <= 100
    assert assessment["released_to_candidate"] is False, "feedback leaked before review"
    assert rnd["disclosure_accepted_at"], "an interview ran without recorded consent"

    # 7. Every evidence line cites a real turn.
    turn_ids = {t["id"] for t in rnd["transcript"]}
    assert assessment["evidence"], "a report with no cited evidence"
    for line in assessment["evidence"]:
        assert line["turn_ids"] and any(t in turn_ids for t in line["turn_ids"]), line

    # 8. Employer advances with a reason; feedback is released.
    adv = client.post(f"/api/employer/applications/{app_id}/advance", headers=emp_h,
                      json={"reason": "Correct on the mechanics; HM should probe impact.",
                            "release_feedback": True}).json()
    assert adv["status"] == "in_progress"
    assert adv["moved_to"] == "Hiring Manager Call"
    assert adv["assessments_released"] == 1

    # 9. The candidate can now see it, with quotes.
    seen = client.get(f"/api/candidate/me/applications/{app_id}", headers=cand_h).json()
    assert seen["feedback"], "released feedback never reached the candidate"
    fb = seen["feedback"][0]
    assert fb["overall"] == assessment["overall"], "the two sides disagree on the score"
    assert fb["ai_disclosure"], "layer 4 of the disclosure is missing from the report"
    assert all(line["quote"] for line in fb["evidence"]), "a citation resolved to no quote"

    # 10. The decision is on the record.
    assert any(e["action"] == "application.advance" for e in
               client.get(f"/api/employer/applications/{app_id}/assessment",
                          headers=emp_h).json()["audit"])


def test_report_lines_are_all_click_through_able(client):
    """plan.md §8 manual gate, made automatic: every line must resolve to a real turn."""
    db = SessionLocal()
    try:
        rows = db.query(InterviewAssessment).all()
        assert rows, "no assessments to check"
        for row in rows:
            turns = {t.id for t in db.query(InterviewTurn).filter(
                InterviewTurn.session_id == row.session_id).all()}
            for dim in (row.report_json or {}).get("per_dimension") or []:
                for line in dim.get("evidence") or []:
                    assert line["turn_ids"], f"uncited line in {row.session_id}"
                    assert any(t in turns for t in line["turn_ids"]), line
    finally:
        db.close()


# ===========================================================================
# Voice-specific: contradictions spoken as words, not digits
# ===========================================================================
@pytest.mark.parametrize("spoken, expected", [
    ("The team was eight engineers.", "8 engineers"),
    ("We had twelve services.", "12 services"),
    ("Around fifty customers were affected.", "50 customers"),
])
def test_spelled_out_numbers_are_captured(spoken, expected):
    """This is a voice product. Speech-to-text writes small numbers as WORDS far more
    often than digits, so a digits-only extractor misses the most common contradiction."""
    out = analyse_local(answer=spoken, persona="hiring_manager", turn_id="t1",
                        required_skill_names=["Mentorship"])
    assert expected in out["numbers"], out["numbers"]


def test_a_spoken_contradiction_is_caught():
    import re

    first = analyse_local(answer="I led a team of eight engineers.",
                          persona="hiring_manager", turn_id="turn-1",
                          required_skill_names=["Mentorship"])
    established = {"numbers": {}}
    for token in first["numbers"]:
        key = re.sub(r"[\d.,]", "", str(token)).strip().lower()
        if key:
            established["numbers"][key] = {"value": str(token).strip(), "turn_id": "turn-1"}
    assert established["numbers"], "a counted noun produced no keyable fact"

    second = analyse_local(answer="Well, it was three engineers really.",
                           persona="hiring_manager", turn_id="turn-2",
                           established=established, required_skill_names=["Mentorship"])
    flags = [f for f in second["flags"] if f["type"] == "contradiction"]
    assert flags, second["flags"]
    assert set(flags[0]["turn_ids"]) == {"turn-1", "turn-2"}
    assert "8 engineers" in flags[0]["note"] and "3 engineers" in flags[0]["note"]


def test_verbatim_words_are_never_rewritten():
    """Digitising is for extraction only. A report quote must be what they said."""
    from app.interview.analyst import digitise

    spoken = "We had eight engineers on it."
    assert digitise(spoken) != spoken, "digitise did nothing"
    # The analyser must not mutate the answer it was handed.
    out = analyse_local(answer=spoken, persona="tech", turn_id="t1",
                        required_skill_names=["Python"])
    assert out.get("answer", spoken) == spoken


# ===========================================================================
# Reasoning must never reach the candidate's ears
# ===========================================================================
@pytest.mark.parametrize("leaked, survives", [
    ("**Initiating The Interview** I've begun by reviewing the candidate's profile [c1]. "
     "So, tell me about the realtime pricing service.", "realtime pricing service"),
    ("**Framing the Technical Challenge** Okay, I'm pivoting. The candidate's forty "
     "million events a day is key. What broke first?", "What broke first"),
    ("Let me consider their answer. My goal is to probe depth. Why Redis and not "
     "Memcached?", "Why Redis"),
])
def test_model_reasoning_never_reaches_the_candidate(leaked, survives):
    """The 12-2025 native-audio model emits its reasoning into the output transcript.

    Left alone the candidate HEARS "I've begun by reviewing the candidate's profile
    [c1]" — internal claim ids and all. `thinking_config` suppresses it at the source;
    this is the belt to that braces, because a model update can turn it back on.
    """
    from app.interview.session import clean_spoken

    out = clean_spoken(leaked)
    assert out, "the whole turn was stripped"
    assert "[c1]" not in out, "an internal claim id would have been spoken aloud"
    assert "i've begun" not in out.lower()
    assert "i'm pivoting" not in out.lower()
    assert "my goal" not in out.lower()
    assert "the candidate" not in out.lower(), "the interviewer referred to them in the third person"
    # An interview prompt is often an imperative ("Walk me through..."), so check the
    # question's CONTENT survived rather than its punctuation.
    assert survives.lower() in out.lower(), f"the actual question did not survive: {out!r}"


def test_thinking_is_disabled_on_the_live_session():
    """Suppressing it at the source is what stops the turn-settle debounce from being
    cancelled forever by reasoning tokens — which persisted zero interviewer turns."""
    import inspect

    from app.interview import live_client as LC

    src = inspect.getsource(LC.GeminiLiveProvider.connect)
    assert "thinking_config" in src, "the Live session does not disable thinking"
    assert "include_thoughts=False" in src


def test_transcription_language_is_pinned_not_auto_detected():
    """Reported bug: a candidate's own English answer showed up in the transcript as
    a single character in an unrelated script. AudioTranscriptionConfig defaults to
    automatic language detection when `language_codes` is omitted, and unclear/noisy
    audio is exactly when a multilingual ASR model is most likely to guess a wrong
    script instead of its best English interpretation. This app is English-only end
    to end (personas, charters, UI, disclosure) — there is no candidate-language
    feature here to trade away by pinning it."""
    import inspect

    from app.interview import live_client as LC

    src = inspect.getsource(LC.GeminiLiveProvider.connect)
    assert "input_audio_transcription=types.AudioTranscriptionConfig(language_codes=" in src
    assert "output_audio_transcription=types.AudioTranscriptionConfig(language_codes=" in src


def test_speech_config_does_not_pin_a_language_code():
    """The natural first instinct (mine included) is to ALSO set
    `speech_config.language_code` alongside the transcription language pin above, for
    symmetry. Don't: confirmed directly against the real API that
    gemini-2.5-flash-native-audio-preview-12-2025 rejects it outright --
    `websockets.exceptions.ConnectionClosedError: received 1007 ... Unsupported
    language code 'en' for model models/gemini-2.5-flash-native-audio-preview-12-2025`
    -- which fails the live connection from opening at all, for every persona, for
    every candidate. This shipped once, briefly, before a live capability check caught
    it. The synthesized voice's own language was never the reported problem."""
    import inspect

    from app.interview import live_client as LC

    src = inspect.getsource(LC.GeminiLiveProvider.connect)
    speech_config_block = src.split("input_audio_transcription")[0]
    code_lines = [ln for ln in speech_config_block.splitlines() if not ln.strip().startswith("#")]
    offending = [ln for ln in code_lines if "language_code=" in ln]
    assert not offending, (
        "speech_config must not set language_code on this model -- it breaks the "
        f"live connection outright, confirmed against the real API. Found: {offending}"
    )


def test_a_turn_always_settles_even_if_output_never_stops():
    from app.interview import session as RT

    assert RT.TURN_MAX_S > RT.TURN_SETTLE_S
    assert hasattr(RT.InterviewRuntime, "_force_flush")
