"""Replacing a résumé must actually replace what the last one contributed.

The original merge was additive: a field with a value was never touched and skills were
unioned. Uploading a new CV therefore changed nothing visible, left the match score
frozen (it is computed from profile skills), and blended two careers into one profile.
"""
from __future__ import annotations

from app.services.profile_merge import (PROVENANCE_KEY, apply_manual_edit,
                                        merge_resume, public_sections)
from app.services.resume import match_percent

RESUME_A = {
    "headline": "Backend engineer, payments",
    "summary": "I build high-throughput event pipelines.",
    "experience": [{"title": "Senior Backend Engineer", "org": "Acme", "dates": "2020-2024"}],
    "education": [{"degree": "B.Tech Computer Science", "org": "IIT", "dates": "2016-2020"}],
    "projects": [{"title": "Ledger rewrite", "detail": "exactly-once settlement"}],
}
SKILLS_A = ["Python", "Kafka", "AWS"]

RESUME_B = {
    "headline": "Mechanical design engineer",
    "summary": "I design rail vehicles.",
    "experience": [{"title": "Design Engineer", "org": "Premnath", "dates": "2024-Present"}],
    "education": [{"degree": "B.Tech Mechanical", "org": "NIAMT", "dates": "2020-2024"}],
    "projects": [{"title": "Road-Cum-Rail Vehicle", "detail": "anti-roll arrangement"}],
}
SKILLS_B = ["SolidWorks", "AutoCAD"]


def test_second_resume_replaces_the_first():
    after_a, _ = merge_resume({}, RESUME_A, SKILLS_A, filename="a.pdf")
    after_b, report = merge_resume(after_a, RESUME_B, SKILLS_B, filename="b.pdf")

    assert after_b["headline"] == "Mechanical design engineer"
    assert after_b["summary"] == "I design rail vehicles."
    assert sorted(after_b["skills"]) == ["AutoCAD", "SolidWorks"], (
        "the first résumé's skills survived, so the profile blends two careers"
    )
    assert [e["org"] for e in after_b["experience"]] == ["Premnath"]
    assert [e["org"] for e in after_b["education"]] == ["NIAMT"]
    assert [p["title"] for p in after_b["projects"]] == ["Road-Cum-Rail Vehicle"]
    assert set(report["replaced"]) == {"headline", "summary"}


def test_a_replacement_moves_the_match_score():
    """The symptom the candidate actually reports: the percentage never changes."""
    required = ["Python", "Kafka", "AWS"]
    after_a, _ = merge_resume({}, RESUME_A, SKILLS_A, filename="a.pdf")
    after_b, _ = merge_resume(after_a, RESUME_B, SKILLS_B, filename="b.pdf")

    assert match_percent(after_a["skills"], required) == 100
    assert match_percent(after_b["skills"], required) == 0


def test_hand_typed_fields_are_never_overwritten():
    typed = {"headline": "My own words", "summary": "Also mine",
             "skills": ["Negotiation"], "experience": [], "education": [], "projects": []}
    typed = apply_manual_edit({}, typed)

    after, report = merge_resume(typed, RESUME_A, SKILLS_A, filename="a.pdf")
    assert after["headline"] == "My own words"
    assert after["summary"] == "Also mine"
    assert set(report["kept_manual"]) == {"headline", "summary"}
    assert "Negotiation" in after["skills"], "a manually added skill was dropped"
    assert "Python" in after["skills"], "the résumé's skills should still arrive"


def test_manual_skill_survives_a_later_resume_but_the_old_resume_skills_do_not():
    after_a, _ = merge_resume({}, RESUME_A, SKILLS_A, filename="a.pdf")
    with_manual = dict(public_sections(after_a))
    with_manual["skills"] = [*after_a["skills"], "Negotiation"]
    saved = apply_manual_edit(after_a, with_manual)

    after_b, _ = merge_resume(saved, RESUME_B, SKILLS_B, filename="b.pdf")
    assert "Negotiation" in after_b["skills"]
    assert "Kafka" not in after_b["skills"]


def test_editing_a_drafted_field_makes_it_yours():
    after_a, _ = merge_resume({}, RESUME_A, SKILLS_A, filename="a.pdf")
    edited = dict(public_sections(after_a))
    edited["headline"] = "Rewritten by hand"
    saved = apply_manual_edit(after_a, edited)

    after_b, report = merge_resume(saved, RESUME_B, SKILLS_B, filename="b.pdf")
    assert after_b["headline"] == "Rewritten by hand"
    assert "headline" in report["kept_manual"]
    assert after_b["summary"] == "I design rail vehicles.", (
        "an untouched drafted field should still be replaceable"
    )


def test_the_same_resume_twice_is_idempotent():
    once, _ = merge_resume({}, RESUME_A, SKILLS_A, filename="a.pdf")
    twice, report = merge_resume(once, RESUME_A, SKILLS_A, filename="a.pdf")
    assert public_sections(once) == public_sections(twice)
    assert report["added"]["experience"] == 0


def test_provenance_never_reaches_the_candidate():
    after, _ = merge_resume({}, RESUME_A, SKILLS_A, filename="a.pdf")
    assert PROVENANCE_KEY in after
    assert PROVENANCE_KEY not in public_sections(after)


def test_a_profile_saved_by_a_client_that_strips_provenance_still_tracks_it():
    """The UI PATCHes the whole object and knows nothing about the reserved key."""
    after_a, _ = merge_resume({}, RESUME_A, SKILLS_A, filename="a.pdf")
    round_tripped = apply_manual_edit(after_a, public_sections(after_a))
    assert round_tripped[PROVENANCE_KEY]["skills"], "provenance was lost on save"

    after_b, _ = merge_resume(round_tripped, RESUME_B, SKILLS_B, filename="b.pdf")
    assert "Kafka" not in after_b["skills"]
