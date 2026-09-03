"""O*NET-derived question grounding.

WHAT THIS SOLVES
----------------
Two different problems, and they are worth separating:

  1. *Which areas is it legitimate to probe for this role?* — answered by the occupation's
     Core task statements. These are what the U.S. Department of Labor says the job
     actually involves, which is a far better basis than our own guess at "top questions".

  2. *What do we fall back to when nothing in the conversation suggests a follow-up?* —
     answered by the seeded question bank. It is a FALLBACK: the adaptive path (a question
     generated from what the candidate just said) always wins. The bank exists so that a
     silence never produces a silence.

Every derived row carries `source = "onet:<Task ID>"`, so any question a candidate is
asked traces to a public record. Licence and attribution: data/onet/ATTRIBUTION.md.
"""
from __future__ import annotations

import csv
import pathlib
import re
from typing import Any, Iterator

DATA = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "onet"
TASKS_CSV = DATA / "task_statements.csv"
OCCUPATIONS_CSV = DATA / "occupation_data.csv"

#: Our skill taxonomy mapped onto O*NET occupations. Deliberately hand-curated: an
#: automatic string match puts "Python" under "Zoologists" (who genuinely use it) and
#: the resulting questions are nonsense.
SKILL_TO_OCCUPATIONS: dict[str, tuple[str, ...]] = {
    # Software craft
    "Python": ("15-1252.00",),                        # Software Developers
    "Java": ("15-1252.00",),
    "Go": ("15-1252.00",),
    "TypeScript": ("15-1254.00",),                    # Web Developers
    "JavaScript": ("15-1254.00",),
    "React": ("15-1254.00",),
    "Node.js": ("15-1254.00", "15-1252.00"),
    "FastAPI": ("15-1252.00",),
    "Django": ("15-1252.00",),
    "API Design": ("15-1252.00", "15-1254.00"),
    "Microservices": ("15-1252.00", "15-1241.00"),
    "WebRTC": ("15-1252.00",),
    "CI/CD": ("15-1253.00", "15-1252.00"),            # QA Analysts and Testers

    # Data
    "SQL": ("15-1242.00", "15-2051.00"),              # DB Administrators, Data Scientists
    "NoSQL": ("15-1243.00",),                         # Database Architects
    "Data Engineering": ("15-1243.00", "15-2051.00"),
    "Machine Learning": ("15-2051.00", "15-1221.00"), # Data Scientists, Research Scientists
    # Kafka and Redis are application infrastructure, not database administration. Mapping
    # them to DBAs produced questions about "logical and physical database descriptions".
    "Kafka": ("15-1252.00",),
    "Redis": ("15-1252.00",),

    # Infrastructure and operations
    "Docker": ("15-1252.00",),
    "Kubernetes": ("15-1241.00", "15-1244.00"),       # Network Architects, Sysadmins
    "AWS": ("15-1241.00",),
    "GCP": ("15-1241.00",),
    "Azure": ("15-1241.00",),
    "Terraform": ("15-1244.00",),
    "Observability": ("15-1244.00",),
    "System Design": ("15-1211.00", "15-1241.00"),    # Systems Analysts, Network Architects

    # Security
    "Security": ("15-1212.00",),                      # Information Security Analysts
    "Incident Response": ("15-1212.00",),

    # Product and people. There is no "Product Manager" SOC code; Computer Systems
    # Analysts is the closest real match ("analyze user needs ... determine feasibility"),
    # and it is far closer than Marketing Managers, which supplied trade-show questions.
    "Product Sense": ("15-1211.00", "13-1111.00"),    # Systems Analysts, Management Analysts
    "Stakeholder Management": ("13-1111.00", "11-3021.00"),
    "Mentorship": ("11-3021.00",),                    # IS Managers
}

#: Which persona is the right mouth for which kind of task.
#:
#: Split into two kinds of signal, because they behave differently:
#:
#: LEADING VERBS decide the managerial personas. "Direct daily operations" is a manager's
#: task; "specify identifiers of database to management system" is not, even though it
#: contains the letters of "manage". Substring matching conflated the two and handed
#: technical database work to the hiring manager.
#:
#: TOPIC CUES decide product and customer, where the subject matter matters more than
#: the verb — "analyze user needs" is a product question however it is phrased.
_LEADING_VERBS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hiring_manager", ("direct", "assign", "supervise", "manage", "coordinate",
                        "delegate", "hire", "train", "mentor", "schedule", "budget")),
    ("customer", ("present", "explain", "consult", "advise", "respond")),
    ("behavioural", ("collaborate", "confer", "negotiate", "mediate")),
)

_TOPIC_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("product", ("user needs", "customer needs", "business problem", "business need",
                 "feasibility", "cost", "profitab", "stakeholder", "market",
                 "requirements to determine", "recommend data-driven",
                 "recommend modifications")),
    ("customer", ("technical support", "user support", "customer service",
                  "plain language", "non-technical")),
)

#: Difficulty bands. Band 1 asks what they did; band 5 asks what they chose against and
#: what it cost. Same task statement, five depths of question.
#: `{topic}` arrives without trailing punctuation, so each frame owns its own.
_FRAMES: dict[int, str] = {
    1: "Walk me through a time you had to {topic}. Keep it concrete.",
    2: "How do you approach {topic}? Give me a specific example.",
    3: "Tell me about the hardest time you had to {topic}. What made it hard?",
    4: "When you {topic}, what tradeoff did you make, and what did it cost?",
    5: "Suppose you {topic} and it went wrong in production. What would you have done "
       "differently from the start, and what would you have given up?",
}


def available() -> bool:
    return TASKS_CSV.is_file() and OCCUPATIONS_CSV.is_file()


def _rows(path: pathlib.Path) -> Iterator[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        yield from csv.DictReader(handle)


def occupations() -> dict[str, str]:
    """O*NET-SOC code -> occupation title."""
    return {r["O*NET-SOC Code"]: r["Title"] for r in _rows(OCCUPATIONS_CSV)}


def core_tasks(soc_code: str) -> list[dict[str, str]]:
    """Core task statements for one occupation. Supplemental tasks are skipped: they are
    peripheral to the job and produce questions that feel beside the point."""
    return [
        {"task_id": r["Task ID"], "task": r["Task"].strip(), "title": r["Title"]}
        for r in _rows(TASKS_CSV)
        if r["O*NET-SOC Code"] == soc_code and r.get("Task Type") == "Core"
    ]


def persona_for(task: str) -> str:
    """Which interviewer should own a probe about this task."""
    low = re.sub(r"\s+", " ", task.lower()).strip()
    lead = re.split(r"[,;]| such as | or direct | and direct ", low, maxsplit=1)[0]

    # The sentence's own opening verb, which is what distinguishes a managerial task
    # from a technical one that merely mentions management.
    first = lead.split()[0].rstrip("s") if lead.split() else ""
    for persona, verbs in _LEADING_VERBS:
        if any(first == v or first == v.rstrip("e") for v in verbs):
            return persona

    for persona, cues in _TOPIC_CUES:
        if any(cue in lead for cue in cues):
            return persona
    return "tech"


#: A spoken turn is capped at about 45 words by the persona invariants, and the frame
#: itself costs some. O*NET statements run to 40 words on their own, so the clause has to
#: be trimmed to the part that carries the meaning.
_MAX_CLAUSE_WORDS = 16


def _to_clause(task: str) -> str:
    """Turn a task statement into something that reads after "a time you had to ...".

    O*NET statements are long imperative sentences with enumerations: "Design database
    applications, such as interfaces, data transfer mechanisms, global temporary tables,
    data partitions, and function-based indexes...". Read aloud that is unusable, so the
    enumeration is dropped and the leading clause kept. Trimming rather than rewriting
    keeps us from inventing meaning the source does not carry.
    """
    clause = re.sub(r"\s+", " ", task.strip().rstrip("."))

    # Drop enumerations and trailing purpose clauses that add words but not substance.
    for marker in (" such as ", ", including ", ", e.g.", " in order to "):
        idx = clause.lower().find(marker)
        if idx > 20:
            clause = clause[:idx]
            break

    words = clause.split()
    if len(words) > _MAX_CLAUSE_WORDS:
        clipped = words[:_MAX_CLAUSE_WORDS]
        # Never end on a dangling conjunction or preposition.
        while clipped and clipped[-1].lower() in {
                "and", "or", "to", "for", "with", "of", "the", "a", "an", "in", "on", "by"}:
            clipped.pop()
        clause = " ".join(clipped)

    clause = clause.rstrip(" ,;.")
    if clause:
        clause = clause[0].lower() + clause[1:]
    return clause


#: Occupations added to every skill's pool purely to give the non-technical personas
#: something to draw on. Without them the bank was 70% technical and the hiring manager
#: had five questions across the whole taxonomy — which is no fallback at all, and R2
#: hands the floor to the product interviewer.
_CROSS_CUTTING = ("15-1211.00", "11-3021.00", "13-1111.00")

#: Tasks kept per persona per skill. Balanced deliberately: "longest statement first"
#: favours technical prose and starved the personas PS11 leans on hardest.
_PER_PERSONA = 2


def build_questions(limit_per_skill: int = 6) -> list[dict[str, Any]]:
    """The derived bank: rows shaped for the InterviewQuestion model.

    `skill_id` is stored as the SLUG (`system_design`), not the display name, because
    that is what `retrieval.retrieve` filters on. Storing the display name meant the
    filter never matched and the bank could never be read at all.

    Nothing is written here — the seed script owns persistence, so this stays a pure and
    testable transform.
    """
    if not available():
        return []

    from app.engines import datasets as ds  # noqa: PLC0415 — cycle-safe

    # Read the 3.4 MB task file once, not once per skill.
    by_occupation: dict[str, list[dict[str, str]]] = {}
    for r in _rows(TASKS_CSV):
        if r.get("Task Type") != "Core":
            continue
        by_occupation.setdefault(r["O*NET-SOC Code"], []).append(
            {"task_id": r["Task ID"], "task": r["Task"].strip()})

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()

    def emit(skill_key: str, task: dict[str, str], persona: str) -> None:
        clause = _to_clause(task["task"])
        for band, frame in _FRAMES.items():
            key = (skill_key, task["task_id"], band)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "skill_id": skill_key,
                "persona": persona,
                "difficulty": band,
                "kind": "probe",
                "question": frame.format(topic=clause)[:600],
                "source": "onet:{}".format(task["task_id"]),
            })

    for skill_name, soc_codes in SKILL_TO_OCCUPATIONS.items():
        skill_key = ds.SKILL_ID.get(skill_name)
        if not skill_key:
            continue

        own = [t for code in soc_codes for t in by_occupation.get(code, [])]
        # Longer statements carry more to probe, so they make better questions.
        own.sort(key=lambda t: len(t["task"]), reverse=True)

        # Bucket this skill's own tasks by the persona that should ask them, then take a
        # fixed quota from each so no persona is crowded out by technical prose.
        buckets: dict[str, list[dict[str, str]]] = {}
        for task in own:
            buckets.setdefault(persona_for(task["task"]), []).append(task)

        taken = 0
        for persona, tasks in buckets.items():
            for task in tasks[:_PER_PERSONA]:
                emit(skill_key, task, persona)
                taken += 1

        # Fill any remaining budget with the strongest statements regardless of persona.
        for task in own:
            if taken >= limit_per_skill:
                break
            if any(k[1] == task["task_id"] and k[0] == skill_key for k in seen):
                continue
            emit(skill_key, task, persona_for(task["task"]))
            taken += 1

    # Persona-generic rows (`skill_id = ""`). `retrieval.retrieve` keeps these eligible
    # for every skill precisely so the non-technical personas always have something.
    generic = [t for code in _CROSS_CUTTING for t in by_occupation.get(code, [])]
    generic.sort(key=lambda t: len(t["task"]), reverse=True)
    per_persona: dict[str, int] = {}
    for task in generic:
        persona = persona_for(task["task"])
        if per_persona.get(persona, 0) >= 6:
            continue
        per_persona[persona] = per_persona.get(persona, 0) + 1
        emit("", task, persona)

    return out

# ---------------------------------------------------------------- wording
#: O*NET task statements are written for occupational classification, not for speaking
#: aloud: "Design database applications, such as interfaces, data transfer mechanisms,
#: global temporary tables, data partitions, and function-based indexes to ensure...".
#: Slotting that into a frame produces dangling clauses and broken grammar whichever way
#: it is trimmed. So O*NET supplies the AUTHORITY over what to probe, and the wording is
#: generated from it — once, at seed time, so no latency lands mid-interview.
_REWRITE_PROMPT = """You are writing the fallback question bank for a spoken job interview.

Below is one task statement from the O*NET occupational database — an authoritative
description of something this job actually involves. Turn it into ONE interview question
at difficulty {band} of 5.

DIFFICULTY {band}: {band_guide}

TASK STATEMENT (authoritative — do not probe anything outside it):
{task}

Rules:
- Under 30 spoken words. It is read aloud, not printed.
- Plain speech. No markdown, no lists, no preamble, no quotation marks.
- Ask about THIS task only. Do not invent a scenario the statement does not support.
- Address the candidate as "you".
- Do not mention O*NET, databases of occupations, or that this is from a bank.

Return ONLY JSON: {{"question": "<the question>"}}"""

_BAND_GUIDE = {
    1: "ask what they did. A candidate who has done the work at all can answer.",
    2: "ask how they approach it, and require one concrete example.",
    3: "ask for the hardest instance and what made it hard.",
    4: "ask what tradeoff they made and what it cost.",
    5: "pose a failure and ask what they would have done differently from the start, "
       "and what they would have given up.",
}


def rewrite_question(task: str, band: int, *, attempts: int = 3) -> str | None:
    """A spoken question for one O*NET task, or None when no model is reachable.

    Retries on transient failure. This is a batch job against a shared endpoint: a
    single 503 would otherwise leave one question as a rough deterministic frame
    forever, and there is no reason to accept that when a retry costs a second.
    """
    import random
    import time

    from app.interview import gemini as GEM  # noqa: PLC0415 — optional at import time

    prompt = _REWRITE_PROMPT.format(
        band=band, band_guide=_BAND_GUIDE[band], task=task.strip())

    for attempt in range(attempts):
        data = GEM.generate_json(prompt, temperature=0.4)
        if isinstance(data, dict):
            text = str(data.get("question") or "").strip().strip('"')
            # Two failure modes worth catching: an empty answer, and the model ignoring
            # the word limit and returning a paragraph nobody can say aloud.
            if text and len(text.split()) <= 45:
                return text
        if attempt < attempts - 1:
            # Jittered backoff: eight workers retrying in lockstep would recreate the
            # burst that caused the failure.
            time.sleep((2 ** attempt) * 0.6 + random.random() * 0.4)
    return None
