"""Skill extraction from a job description.

Phase 1 uses a deterministic lexicon match — no model call, no API key, fully testable.
`extract_skills` is the seam: Phase 3 can swap in a Gemini pass behind the same signature
without touching callers.
"""
from __future__ import annotations

import re

# Curated lexicon. Canonical name -> surface forms found in real JDs.
LEXICON: dict[str, tuple[str, ...]] = {
    "Python": ("python",),
    "Java": ("java",),
    "Go": ("golang", "go lang"),
    "TypeScript": ("typescript", "ts"),
    "JavaScript": ("javascript",),
    "React": ("react", "reactjs", "react.js"),
    "Node.js": ("node.js", "nodejs", "node"),
    "FastAPI": ("fastapi",),
    "Django": ("django",),
    "SQL": ("sql", "postgres", "postgresql", "mysql"),
    "NoSQL": ("mongodb", "dynamodb", "cassandra"),
    "Redis": ("redis",),
    "Kafka": ("kafka",),
    "Docker": ("docker",),
    "Kubernetes": ("kubernetes", "k8s"),
    "AWS": ("aws", "amazon web services"),
    "GCP": ("gcp", "google cloud"),
    "Azure": ("azure",),
    "Terraform": ("terraform",),
    "CI/CD": ("ci/cd", "continuous integration", "continuous delivery"),
    "System Design": ("system design", "distributed systems", "scalability"),
    "Microservices": ("microservices", "micro-services"),
    "API Design": ("rest api", "api design", "graphql"),
    "Machine Learning": ("machine learning", "ml", "deep learning"),
    "Data Engineering": ("etl", "data pipeline", "airflow", "spark"),
    "Observability": ("observability", "monitoring", "prometheus", "grafana"),
    "Security": ("security", "owasp", "penetration testing"),
    "Product Sense": ("product sense", "product thinking", "roadmap"),
    "Stakeholder Management": ("stakeholder", "cross-functional"),
    "Mentorship": ("mentor", "mentoring", "coaching"),
    "Incident Response": ("on-call", "oncall", "incident", "postmortem"),
    "WebRTC": ("webrtc", "real-time audio", "real time video"),
}

def extract_skills(jd_text: str, limit: int = 12) -> list[str]:
    """Return canonical skill names found in the JD, ordered by first appearance."""
    if not jd_text:
        return []
    haystack = jd_text.lower()
    hits: list[tuple[int, str]] = []

    for canonical, forms in LEXICON.items():
        best: int | None = None
        for form in forms:
            # Word-boundary match so 'go' doesn't fire inside 'going'.
            match = re.search(rf"(?<![\w.]){re.escape(form)}(?![\w])", haystack)
            if match and (best is None or match.start() < best):
                best = match.start()
        if best is not None:
            hits.append((best, canonical))

    hits.sort(key=lambda pair: pair[0])
    return [name for _, name in hits[:limit]]


def propose_panel(skills: list[str]) -> list[str]:
    """Default panel for a job, derived from its skills.

    Delegates to the interview engine so persona keys have exactly one source of
    truth ('tech', 'product', 'hiring_manager', 'customer', 'behavioural').
    """
    from app.interview import personas as P  # local import: engine is optional at import time

    return P.propose_panel(skills or [], "")


def default_pipeline(skills: list[str]) -> list[dict]:
    """The stages a new job starts with. Employer can replace wholesale."""
    return [
        {
            "seq": 1,
            "name": "AI Panel Interview",
            "kind": "ai_interview",
            "interview_config_json": {
                "panel": propose_panel(skills),
                "preset": "panel",
                "start_difficulty": 3,
                "target_skills": skills[:6],
            },
        },
        {
            "seq": 2,
            "name": "Human Round",
            "kind": "human_interview",
            "interview_config_json": {},
        },
    ]


def extract_experience_range(jd_text: str) -> tuple[int | None, int | None]:
    """(min, max) years from a JD. Either side may be None when the JD is silent."""
    import re as _re

    if not jd_text:
        return (None, None)
    span = _re.search(r"(\d{1,2})\s*[-\u2013to]+\s*(\d{1,2})\s*(?:years?|yrs?)", jd_text, _re.I)
    if span:
        lo, hi = int(span.group(1)), int(span.group(2))
        return (min(lo, hi), max(lo, hi))
    single = _re.search(r"(\d{1,2})\s*\+\s*(?:years?|yrs?)", jd_text, _re.I)
    if single:
        return (int(single.group(1)), None)
    plain = _re.search(r"(?:at least|minimum(?: of)?)\s*(\d{1,2})\s*(?:years?|yrs?)", jd_text, _re.I)
    if plain:
        return (int(plain.group(1)), None)
    return (None, None)
