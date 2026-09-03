"""Seed the role-play scenario bank (PS11 #6).

Scenarios are content, not code, so they live in the database and are selected by role
family plus skill overlap. Each carries an escalation per difficulty band: the same
situation gets materially harder, not just tersely worded.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Scenario  # noqa: E402

SCENARIOS = [
    {
        "role_family": "backend",
        "title": "Stale cache after your release",
        "persona_owner": "customer",
        "difficulty_floor": 3,
        "skills_json": ["Redis", "Kafka", "System Design", "Observability", "Incident Response"],
        "setup_text": (
            "You are the head of data at an enterprise customer. Since this candidate's "
            "caching release went out on Tuesday, your nightly revenue report has been "
            "showing yesterday's numbers. You are not technical and you do not care how "
            "the cache works. You care that your CFO asked you about it this morning."),
        "injects_json": [
            "the report is used in a Monday exec meeting",
            "you noticed it two days before reporting it",
            "another team told you it was 'just a caching thing'",
        ],
        "escalations_json": {
            "4": ("Mention that this is the second data issue this quarter, and that your "
                  "renewal is signed in March."),
            "5": ("Say you want a written commitment on how stale the data can ever get "
                  "before you approve anything else from their team."),
        },
        "success_signals_json": [
            "acknowledges the business consequence before explaining the mechanism",
            "avoids jargon or explains it in plain language",
            "commits to a specific next step and a timeframe",
            "distinguishes the immediate fix from the durable one",
        ],
    },
    {
        "role_family": "backend",
        "title": "On-call handover during an outage",
        "persona_owner": "hiring_manager",
        "difficulty_floor": 4,
        "skills_json": ["Incident Response", "Observability", "Kubernetes", "AWS"],
        "setup_text": (
            "You are this candidate's manager. It is 2am, they are on call, and the "
            "checkout service is failing for about a third of users. They have been "
            "debugging for forty minutes with no root cause. You have just joined the call."),
        "injects_json": [
            "the deploy four hours ago is the obvious suspect",
            "rolling back loses a fix another team shipped",
            "you have not told the customer-facing team anything yet",
        ],
        "escalations_json": {
            "5": ("Tell them support is now asking what to say to customers, and you need "
                  "a decision from them in the next two minutes."),
        },
        "success_signals_json": [
            "stabilises before diagnosing",
            "states a decision rather than asking to be told",
            "communicates outward, not only inward",
        ],
    },
    {
        "role_family": "product",
        "title": "Cutting a feature two weeks from launch",
        "persona_owner": "customer",
        "difficulty_floor": 3,
        "skills_json": ["Product Sense", "Stakeholder Management", "SQL", "Observability"],
        "setup_text": (
            "You run operations at a mid-size logistics customer. You were promised bulk "
            "editing in the next release and you have already told your team it is coming. "
            "The candidate is about to tell you it has been cut."),
        "injects_json": [
            "you rebuilt a weekly process around the promise",
            "your alternative is a spreadsheet and three hours a week",
            "you have a competitor's demo booked next month",
        ],
        "escalations_json": {
            "4": "Ask directly whether you should be looking at other vendors.",
            "5": ("Say your director wants this in writing, and ask what they are "
                  "actually committing to and by when."),
        },
        "success_signals_json": [
            "leads with the consequence for this customer",
            "does not hide behind process or blame another team",
            "offers a concrete interim path",
            "is honest about what they cannot promise",
        ],
    },
    {
        "role_family": "product",
        "title": "Two teams want the same quarter",
        "persona_owner": "hiring_manager",
        "difficulty_floor": 4,
        "skills_json": ["Product Sense", "Stakeholder Management", "Mentorship"],
        "setup_text": (
            "You are a director. Two engineering leads both need the candidate's team next "
            "quarter, one for a compliance deadline and one for a revenue feature. The "
            "candidate has to choose and defend it."),
        "injects_json": [
            "the compliance deadline is external and dated",
            "the revenue feature has a named customer attached",
            "neither lead will volunteer to wait",
        ],
        "escalations_json": {
            "5": ("Tell them the lead they deprioritised has escalated to your VP, and ask "
                  "what they want you to say."),
        },
        "success_signals_json": [
            "names the tradeoff explicitly rather than promising both",
            "reasons from consequence, not seniority",
            "says what would change their mind",
        ],
    },
    {
        "role_family": "frontend",
        "title": "The redesign nobody can use",
        "persona_owner": "customer",
        "difficulty_floor": 3,
        "skills_json": ["React", "TypeScript", "Product Sense"],
        "setup_text": (
            "You are a daily power user of the product. The candidate's team shipped a "
            "redesign last week. It looks better and you now take twice as long to do the "
            "one task you do fifty times a day."),
        "injects_json": [
            "the old flow was three clicks, the new one is seven",
            "you have not filed a ticket, you told your account manager",
            "two colleagues have gone back to the old export instead",
        ],
        "escalations_json": {
            "4": "Say your team is talking about not upgrading again.",
            "5": ("Ask whether anyone actually watched someone use this before shipping it."),
        },
        "success_signals_json": [
            "asks what the task actually is before defending the design",
            "does not argue that the user is wrong",
            "separates the visual change from the workflow regression",
        ],
    },
    {
        "role_family": "general",
        "title": "Explaining your work to the person who pays for it",
        "persona_owner": "customer",
        "difficulty_floor": 2,
        "skills_json": ["System Design", "Product Sense", "API Design", "Security"],
        "setup_text": (
            "You are a non-technical buyer evaluating this team's work. You have been told "
            "the project the candidate just described was a success. You want to understand, "
            "in your own terms, what you got for it."),
        "injects_json": [
            "you do not know what any of the technology names mean",
            "you are comparing this against doing nothing",
            "you will repeat their answer to your board",
        ],
        "escalations_json": {
            "4": "Ask what would have happened if they had done nothing at all.",
            "5": ("Say you are not convinced this was worth the quarter it took, and ask "
                  "them to change your mind."),
        },
        "success_signals_json": [
            "answers without jargon",
            "frames value in the buyer's terms",
            "is willing to say what the work did not achieve",
        ],
    },
]


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        existing = {s.title for s in db.query(Scenario).all()}
        added = 0
        for row in SCENARIOS:
            if row["title"] in existing:
                continue
            db.add(Scenario(**row))
            added += 1
        db.commit()
        total = db.query(Scenario).count()
        print(f"scenarios: +{added} added, {total} in the bank")
        for s in db.query(Scenario).all():
            esc = ", ".join(f"L{k}" for k in sorted((s.escalations_json or {}).keys()))
            print(f"  [{s.role_family:<8}] {s.title:<44} owner={s.persona_owner:<15} "
                  f"floor={s.difficulty_floor} escalations={esc or 'none'}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
