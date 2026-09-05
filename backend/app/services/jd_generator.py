"""AI-assisted job-description drafting for employers.

Two paths, same output shape, same caller: `generate(title, department, seniority,
keywords)` tries Gemini first (when configured) and falls back to a deterministic
template when it isn't — the same "honest fallback, never a fake AI call" pattern the
rest of this codebase uses for scoring and question generation. The employer always
gets a full draft back and can edit every word before publishing; nothing here writes
directly to a job.
"""
from __future__ import annotations

from app.interview import gemini as GEM

# Keyword -> the responsibility/requirement bullets used by the offline template.
# Matched against the role title; falls through to a generic set when nothing hits.
_CATEGORIES: dict[tuple[str, ...], dict[str, list[str]]] = {
    ("engineer", "developer", "programmer", "sde"): {
        "responsibilities": [
            "Design, build and ship production features end to end, from technical design through rollout.",
            "Write clean, well-tested code and review teammates' changes with the same bar.",
            "Debug and resolve production issues, including on-call rotation as needed.",
            "Collaborate with product and design to turn requirements into a working system.",
        ],
        "requirements": [
            "Strong fundamentals in data structures, algorithms and system design.",
            "Experience shipping and operating production software.",
            "Comfortable working across the stack and picking up new tools quickly.",
            "Clear written and verbal communication with both technical and non-technical stakeholders.",
        ],
    },
    ("product", "pm"): {
        "responsibilities": [
            "Own the roadmap for your area, from discovery through launch and measurement.",
            "Translate customer and business needs into clear, prioritised requirements.",
            "Partner with engineering and design daily to ship the right thing, not just a thing.",
            "Define success metrics for every launch and report honestly on the outcome.",
        ],
        "requirements": [
            "Track record of shipping products that moved a real metric.",
            "Comfortable making prioritisation calls with incomplete information.",
            "Strong stakeholder communication, up and across the organisation.",
            "Structured, data-informed decision making.",
        ],
    },
    ("design", "designer", "ux", "ui"): {
        "responsibilities": [
            "Turn ambiguous problems into clear, usable flows and interfaces.",
            "Run user research and translate findings into design decisions.",
            "Partner with product and engineering from concept through to shipped product.",
            "Maintain and evolve the design system as the product grows.",
        ],
        "requirements": [
            "A portfolio showing shipped, real-world product work.",
            "Fluency in a modern design tool and in prototyping interactions.",
            "Comfortable presenting and defending design decisions with evidence.",
        ],
    },
    ("sales", "account executive", "business development"): {
        "responsibilities": [
            "Own a pipeline of prospects from first contact through close.",
            "Run discovery calls that uncover real business needs, not just feature requests.",
            "Forecast accurately and keep CRM records current.",
            "Partner with customer success to ensure a clean handoff after close.",
        ],
        "requirements": [
            "Track record of consistently hitting or exceeding quota.",
            "Comfortable with a consultative, needs-based sales process.",
            "Strong written and verbal communication.",
        ],
    },
    ("data", "analyst", "scientist"): {
        "responsibilities": [
            "Turn raw data into decisions: build the analysis, then make the recommendation.",
            "Build and maintain the queries, models or dashboards your stakeholders rely on.",
            "Partner with product and business teams to define what to measure and why.",
        ],
        "requirements": [
            "Strong SQL and comfort with at least one scripting language for analysis.",
            "Experience turning ambiguous questions into a measurable analysis plan.",
            "Ability to communicate findings clearly to a non-technical audience.",
        ],
    },
}

_GENERIC = {
    "responsibilities": [
        "Own outcomes in your area end to end, not just tasks assigned to you.",
        "Collaborate closely with cross-functional partners to get things shipped.",
        "Bring structure and judgement to ambiguous problems.",
        "Communicate progress and blockers clearly and early.",
    ],
    "requirements": [
        "A track record of ownership and delivery in a comparable role.",
        "Strong communication skills, written and verbal.",
        "Comfortable operating with some ambiguity.",
    ],
}


def _bullets_for(title: str) -> dict[str, list[str]]:
    low = title.lower()
    for keys, bullets in _CATEGORIES.items():
        if any(k in low for k in keys):
            return bullets
    return _GENERIC


def _template(title: str, department: str | None, seniority: str | None,
             keywords: list[str]) -> str:
    bullets = _bullets_for(title)
    seniority_line = "{} ".format(seniority.strip().title()) if seniority else ""
    dept_line = " in our {} team".format(department.strip()) if department else ""
    kw_line = ""
    if keywords:
        kw_line = "\n\nThis role works closely with: {}.".format(", ".join(keywords[:8]))

    return (
        "About the role\n"
        "We are hiring a {sen}{title}{dept}. You will work closely with a small, "
        "senior team and be trusted with real ownership from day one.{kw}\n\n"
        "What you will do\n"
        + "\n".join("- {}".format(b) for b in bullets["responsibilities"])
        + "\n\nWhat we are looking for\n"
        + "\n".join("- {}".format(b) for b in bullets["requirements"])
        + "\n\nThis description is a starting draft — edit anything before publishing."
    ).format(sen=seniority_line, title=title, dept=dept_line, kw=kw_line)


def generate(*, title: str, department: str | None = None, seniority: str | None = None,
            keywords: list[str] | None = None) -> dict[str, str]:
    """Returns {"jd_text": ..., "source": "ai" | "template"}. Never raises."""
    keywords = keywords or []

    if GEM.available():
        prompt = (
            "Write a clear, honest job description for the role below. Plain prose, "
            "no markdown symbols, organised as: a short intro paragraph, then "
            "'Responsibilities' and 'Requirements' sections with one bullet per line "
            "(use a leading '- '). Keep it under 300 words. Do not invent a company "
            "name, salary, or benefits — none were given.\n\n"
            "Title: {title}\n"
            "Department: {dept}\n"
            "Seniority: {sen}\n"
            "Key skills/tools to mention: {kw}\n"
        ).format(title=title, dept=department or "(unspecified)",
                 sen=seniority or "(unspecified)", kw=", ".join(keywords) or "(none given)")
        text = GEM.generate_text(prompt, temperature=0.4)
        if text:
            return {"jd_text": text, "source": "ai"}

    return {"jd_text": _template(title, department, seniority, keywords), "source": "template"}
