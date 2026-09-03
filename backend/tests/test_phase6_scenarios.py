"""Role-play scenarios and R7 — PS11 requirement #6, the last of the eleven."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.interview import scenarios as SC
from app.interview.moderator import (SCENARIO_MIN_SECONDS, SCENARIO_TURN_COST, Moderator)
from app.main import app
from app.models import Scenario

STRONG = {"scores": {"correctness": 4, "depth": 4}, "impact_stated": True,
          "specificity": "specific", "flags": [], "turn_id": "t1"}


def _mod(panel, owner="customer", **kw):
    m = Moderator(panel, required_skill_ids=["python"], **kw)
    m.pending_scenario_persona = owner
    m.note_turn(panel[0])
    return m


def _exchanges(m, n, analysis=STRONG):
    """Drive n candidate exchanges, returning the last directive."""
    directive = None
    for _ in range(n):
        m.ingest(analysis, target_skill_id="python")
        directive = m.decide(analysis, target_skill_id="python")
        m.note_turn(directive["next_speaker"])
    return directive


# ================================================================= R7 firing
def test_r7_fires_after_two_exchanges():
    m = _mod(["tech", "product", "customer"])
    d = _exchanges(m, 2)
    assert d["rule"] == "R7", d
    assert d["next_speaker"] == "customer"
    assert d["intent"] == "scenario"


def test_r7_does_not_fire_on_the_first_exchange():
    """A role-play with nothing established to role-play against is just a riddle."""
    m = _mod(["tech", "product", "customer"])
    d = _exchanges(m, 1)
    assert d["rule"] != "R7", d


def test_r7_is_skipped_when_no_owner_sits_on_the_panel():
    """A Customer scenario with no Customer interviewer is unplayable."""
    m = _mod(["tech", "product"], owner="customer")
    d = _exchanges(m, 3)
    assert d["rule"] != "R7", "R7 fired with its owner absent from the panel"


def test_r7_is_skipped_when_time_is_short():
    m = _mod(["tech", "product", "customer"])
    m.ingest(STRONG, target_skill_id="python")
    m.ingest(STRONG, target_skill_id="python")
    m.seconds_remaining = SCENARIO_MIN_SECONDS - 1
    d = m.decide(STRONG, target_skill_id="python")
    assert d["rule"] != "R7", "a scenario opened with no room to run it"


def test_r7_is_skipped_when_the_turn_budget_cannot_hold_it():
    m = _mod(["tech", "product", "customer"], max_turns=SCENARIO_TURN_COST + 1)
    d = _exchanges(m, 2)
    assert d["rule"] != "R7", "a scenario opened that would run past the turn budget"


def test_urgent_rules_outrank_r7():
    """A contradiction or a missing-impact answer is more interesting than a role-play."""
    m = _mod(["tech", "product", "customer"])
    m.ingest(STRONG, target_skill_id="python")
    m.ingest(STRONG, target_skill_id="python")

    impactless = {"scores": {"correctness": 4}, "impact_stated": False,
                  "specificity": "specific", "turn_id": "t9",
                  "flags": [{"type": "impact_gap", "turn_ids": ["t9"]}]}
    d = m.decide(impactless, target_skill_id="python")
    assert d["rule"] == "R2", f"R7 pre-empted the PS11 centrepiece: {d}"


# ============================================================ scenario lifecycle
def test_the_owner_keeps_the_floor_while_in_character():
    """Breaking character mid-scene destroys the exercise."""
    m = _mod(["tech", "product", "customer"])
    _exchanges(m, 2)
    m.open_scenario(SC.ScenarioState("s1", "customer", "T", {"4": "escalate"}, []))

    d = m.decide(STRONG, target_skill_id="python")
    assert d["next_speaker"] == "customer"
    assert d["intent"] == "scenario"


def test_scenario_closes_and_r7_cannot_re_fire():
    """One role-play per interview — a second would eat the remaining skill coverage."""
    m = _mod(["tech", "product", "customer"])
    _exchanges(m, 2)
    m.open_scenario(SC.ScenarioState("s1", "customer", "T", {}, []))

    for _ in range(SC.MAX_SCENARIO_TURNS):
        m.note_turn("customer")
    assert m.scenario is None, "the scenario never closed"
    assert m.scenario_done is True

    d = _exchanges(m, 3)
    assert d["rule"] != "R7", "R7 fired a second time"


def test_escalation_is_bound_to_difficulty_and_fires_once():
    state = SC.ScenarioState("s1", "customer", "T",
                             {"4": "renewal is at risk", "5": "put it in writing"}, [])
    assert state.escalation_for(3) is None, "no escalation defined at level 3"
    assert state.escalation_for(4) == "renewal is at risk"
    assert state.escalation_for(4) is None, "the same escalation repeated"
    assert state.escalation_for(5) == "put it in writing"


def test_state_survives_a_round_trip():
    """State is checkpointed into session.state_json, so it must serialise."""
    state = SC.ScenarioState("s1", "customer", "Stale cache", {"4": "x"}, ["signal"])
    state.note_turn()
    state.escalation_for(4)

    restored = SC.ScenarioState.from_dict(json.loads(json.dumps(state.to_dict())))
    assert restored.scenario_id == "s1"
    assert restored.turns == 1
    assert restored.escalation_for(4) is None, "escalation history was lost"
    assert restored.success_signals == ["signal"]


# ==================================================================== selection
@pytest.fixture(scope="module")
def seeded_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(Scenario).count() == 0:
            import scripts.seed_scenarios as seed  # noqa: PLC0415
            for row in seed.SCENARIOS:
                db.add(Scenario(**row))
            db.commit()
        yield db
    finally:
        db.close()


def test_selection_requires_the_owner_on_the_panel(seeded_db):
    assert SC.pick(seeded_db, panel=["tech"], skill_ids=["Redis"], difficulty=3) is None


def test_selection_prefers_skill_overlap(seeded_db):
    picked = SC.pick(seeded_db, panel=["tech", "customer"],
                     skill_ids=["Redis", "Kafka", "Observability"], difficulty=3)
    assert picked is not None
    assert "Stale cache" in picked.title, picked.title


def test_selection_respects_a_frontend_job(seeded_db):
    picked = SC.pick(seeded_db, panel=["tech", "customer"],
                     skill_ids=["React", "TypeScript"], difficulty=3)
    assert picked is not None
    assert picked.persona_owner == "customer"
    assert "redesign" in picked.title.lower(), picked.title


# ====================================================================== prompts
def test_opening_block_forbids_breaking_character(seeded_db):
    scenario = SC.pick(seeded_db, panel=["customer"], skill_ids=["Redis"], difficulty=3)
    block = SC.opening_block(scenario)
    # The prompt is hard-wrapped, so compare on collapsed whitespace.
    flat = " ".join(block.lower().split())
    assert "you are the person" in flat
    assert "do not announce that this is a role-play" in flat
    assert "do not break character" in flat
    assert scenario.setup_text.strip()[:40] in block


def test_continuation_block_injects_the_escalation():
    state = SC.ScenarioState("s1", "customer", "T", {"5": "renewal is at risk"}, [])
    state.note_turn()
    block = SC.continuation_block(state, 5)
    assert "renewal is at risk" in block
    assert "still the person" in block


def test_scenario_is_not_scored_on_correctness():
    """A role-play tests reading a person, not recalling a fact."""
    assert "correctness" not in SC.SCENARIO_DIMENSIONS
    assert "user_insight" in SC.SCENARIO_DIMENSIONS
