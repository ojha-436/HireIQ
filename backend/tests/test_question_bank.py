"""The O*NET-derived question bank: provenance, shape, and speakability.

The bank is a FALLBACK — the adaptive path always wins — but a fallback that fires in
front of a real candidate still has to be a question a person can say out loud and a
question whose origin we can name.
"""
from __future__ import annotations

import re

import pytest

from app.services import onet


@pytest.fixture(scope="module")
def derived():
    if not onet.available():
        pytest.skip("O*NET data missing — run python data/onet/fetch.py")
    return onet.build_questions(limit_per_skill=6)


# ================================================================== provenance
def test_every_question_traces_to_a_public_record(derived):
    """A question a candidate is asked must have an origin we can point at.

    This is why O*NET was chosen over the Kaggle/HuggingFace question dumps, which are
    anonymous scrapes under unstated licences.
    """
    assert derived
    for row in derived:
        assert re.fullmatch(r"onet:\d+", row["source"]), row["source"]


def test_the_licence_is_documented():
    import pathlib

    doc = (pathlib.Path(__file__).resolve().parent.parent
           / "data" / "onet" / "ATTRIBUTION.md")
    assert doc.is_file(), "CC BY 4.0 requires attribution and there is none"
    text = doc.read_text()
    assert "Creative Commons Attribution 4.0" in text
    assert "U.S. Department of Labor" in text
    # The licence also requires saying that we changed the data.
    assert "modified" in text.lower()


# ====================================================================== shape
def test_questions_are_short_enough_to_speak(derived):
    """The persona invariants cap a spoken turn at about 45 words."""
    too_long = [r for r in derived if len(r["question"].split()) > 45]
    assert too_long == [], [r["question"] for r in too_long[:3]]


def test_questions_are_not_double_punctuated(derived):
    """"Suppose you design database applications. and it went wrong" — a frame that
    appended to an already-terminated clause."""
    broken = [r["question"] for r in derived
              if re.search(r"[.?!]\s+(?:and|or|but|what|when)\b", r["question"])]
    assert broken == [], broken[:3]


def test_every_persona_and_band_is_covered(derived):
    """Every persona needs a fallback of its own.

    Selecting by "longest statement first" made the bank 70% technical and left the
    hiring manager with five questions across the whole taxonomy — and R2 hands the
    floor to the product interviewer, so a starved product bank is the worst case.
    """
    assert {r["difficulty"] for r in derived} == {1, 2, 3, 4, 5}

    from collections import Counter
    counts = Counter(r["persona"] for r in derived)
    for persona in ("tech", "product", "hiring_manager", "customer", "behavioural"):
        assert counts[persona] >= 50, f"{persona} has only {counts[persona]}: {counts}"


def test_skill_ids_are_slugs_not_display_names(derived):
    """`retrieval.retrieve` filters on skill IDs (`system_design`). Seeding display
    names (`System Design`) meant the filter never matched and the bank was unreachable
    — 640 rows that could never be read."""
    from app.engines import datasets as ds

    scoped = {r["skill_id"] for r in derived if r["skill_id"]}
    assert scoped, "no skill-scoped rows at all"
    unknown = scoped - set(ds.SKILL_NAME)
    assert unknown == set(), f"not valid skill ids: {sorted(unknown)[:5]}"


def test_persona_generic_rows_exist(derived):
    """`retrieval` keeps `skill_id == ""` rows eligible for every skill, which is how
    the non-technical personas always have something to fall back on."""
    generic = [r for r in derived if r["skill_id"] == ""]
    assert generic, "no persona-generic rows were seeded"
    assert {r["persona"] for r in generic} >= {"product", "hiring_manager"}


# ================================================================== routing
def test_a_management_verb_inside_a_technical_task_stays_technical():
    """"Write and code database descriptions ... or direct others in coding" is a
    technical task. Scanning the whole sentence for "direct" handed it to the hiring
    manager."""
    task = ("Write and code logical and physical database descriptions and specify "
            "identifiers of database to management system or direct others in coding.")
    assert onet.persona_for(task) == "tech"


def test_a_genuinely_managerial_task_routes_to_the_hiring_manager():
    assert onet.persona_for(
        "Assign and review the work of systems analysts and programmers.") == "hiring_manager"


def test_a_stakeholder_task_routes_to_product():
    assert onet.persona_for(
        "Analyze user needs and software requirements to determine feasibility.") == "product"


# ============================================================ skill mapping
def test_skills_map_to_plausible_occupations():
    """Kafka mapped to Database Administrators produced questions about "logical and
    physical database descriptions"; Product Sense mapped to Marketing Managers produced
    questions about trade shows."""
    occ = onet.occupations()
    for skill, codes in onet.SKILL_TO_OCCUPATIONS.items():
        assert codes, f"{skill} maps to nothing"
        for code in codes:
            assert code in occ, f"{skill} -> {code} is not a real O*NET occupation"

    # The two that were specifically wrong.
    assert "15-1242.00" not in onet.SKILL_TO_OCCUPATIONS["Kafka"], (
        "Kafka is application infrastructure, not database administration")
    assert "11-2021.00" not in onet.SKILL_TO_OCCUPATIONS["Product Sense"], (
        "Marketing Managers supplied trade-show questions to software PMs")


def test_clause_trimming_never_leaves_a_dangling_word():
    """"...to detect problems, identify." — a trim that cut mid-thought."""
    long_task = ("Monitor and analyze network performance and reports on data input or "
                 "output to detect problems, identify inefficient use of computer "
                 "resources, or perform capacity planning.")
    clause = onet._to_clause(long_task)
    assert not clause.rstrip(".").split()[-1].lower() in {
        "and", "or", "to", "for", "with", "of", "the", "a", "an", "in", "on", "by"}
    assert not clause.endswith(",")
