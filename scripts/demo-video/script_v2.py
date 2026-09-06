"""Narration and card copy, v2.

Two changes from the first cut. The panel now introduces itself by name, so the script
says Alex and Peter rather than "the technical interviewer". And the centre of gravity
moves to the back half: what the assessment actually contains, and what the candidate is
actually told. That is the part a buyer interrogates, and it was the thinnest part of v1.
"""

CARD_VO = {
    "00_open":
        "Most hiring tools screen resumes. HireIQ runs the interview. A panel of A I "
        "interviewers takes turns with the candidate, adapts to what they actually say, "
        "and produces an assessment you can audit line by line. Here is the whole "
        "journey, end to end.",
    "01_post":
        "It starts with the role. The employer writes the job description, and HireIQ "
        "parses it into the required skills. Those skills matter: they become the only "
        "things the panel is allowed to probe.",
    "02_match":
        "On the other side, a candidate imports a resume. It becomes a structured "
        "profile, and it is scored against the role, so the candidate can see which "
        "required skills they match and which they are missing before they apply.",
    "03_interview":
        "Now the interview. The panel is not one bot wearing different hats. Alex is the "
        "technical interviewer. Peter is the hiring manager. They introduce themselves, "
        "they take turns, and a moderator decides which of them speaks next based on what "
        "the candidate has just proved — and what they have not.",
    "04_score":
        "Then the part that decides whether any of this is usable: the assessment. Not a "
        "score from nowhere. Every dimension is graded against evidence quoted from the "
        "transcript, and every recommendation traces back to a sentence the candidate "
        "actually said.",
    "05_candidate":
        "And the candidate is told. Not a silent rejection weeks later — a released "
        "assessment, the same evidence the employer saw, and a clear position in the "
        "pipeline.",
    "06_admin":
        "Around all of it, the controls an enterprise expects: workspace and account "
        "suspension, live service health, and hiring K P Is across the whole tenant.",
    "07_close":
        "Adaptive interviews. Auditable outcomes. That is HireIQ.",
}

SEG_VO = {
    "s1":
        "The employer signs in, gives the role a title and a description, and saves it. "
        "HireIQ reads the description and pulls out the required skills automatically — "
        "Python, Kafka, Postgres, Kubernetes, A W S, system design. The pipeline is then "
        "set: an A I panel interview first, then a founder conversation.",
    "s2":
        "Priya has already imported her resume. It is parsed into a structured profile: "
        "her headline, her skills, her roles, her projects. Import a new resume and it "
        "replaces what the last one contributed, rather than piling one career on top of "
        "another. On the board, this role comes up at a hundred per cent match — every "
        "required skill accounted for, nothing missing. She applies in one click.",
    "s3":
        "The A I disclosure is explicit and timestamped, and the interview cannot start "
        "until she accepts it. That gate is enforced on the server, not just in the "
        "browser. "
        "Alex opens — by name — on her exactly-once settlement ledger. She answers by "
        "typing, which the panel treats exactly the same as speaking, and the assessment "
        "never records which she chose. "
        "Watch the follow-up. It is not from a script. It picks up the specific mechanism "
        "she just named and pushes on the part she left implicit. "
        "Then the floor changes hands. Peter, the hiring manager, takes over — and he is "
        "not asking about implementation any more. He wants to know what SHE did, as "
        "opposed to her team. That hand-off is a decision: the moderator scores every "
        "answer, checks what is still unproven, and picks who is best placed to prove it. "
        "The difficulty marker in the corner moves with her. Strong answers earn harder "
        "questions. And the transcript builds alongside, because every line of it becomes "
        "the evidence behind the score.",
    "s4":
        "Here is the assessment. Overall, a recommendation, and a percentile — and next "
        "to them, adaptive: the share of questions generated from her own answers rather "
        "than pulled from a bank. "
        "Underneath, the dimensions. Correctness, trade-offs, depth, ownership — each with "
        "a score, a band, and how many times it was actually probed. Every one carries "
        "the quote it was judged on, lifted straight from the transcript. If a dimension "
        "was never tested, it says so rather than guessing. "
        "Coverage does the same for the role's required skills: probed on one side, not "
        "probed on the other. An interview that never got to Kubernetes will not pretend "
        "it did. "
        "And the summary is specific enough to argue with. It names what was strong, and "
        "it names what was missing — not adjectives, but the behaviour it saw and the "
        "turns where it saw it. "
        "The recommendation is marked advisory. The panel produces the evidence. The "
        "human makes the decision, and the transcript sits right there to check it "
        "against.",
    "s5":
        "The candidate's side is not a black box either. Priya sees her pipeline: the A I "
        "panel complete, the founder conversation next. When the employer releases "
        "feedback — deliberately, as a decision — she gets the assessment itself. The same "
        "dimensions, the same evidence, the same summary the employer read. "
        "That is the part most hiring processes never do. A candidate who is turned down "
        "here knows which dimension fell short and which answer cost them, instead of "
        "hearing nothing at all.",
    "s6":
        "The admin portal covers the rest: employer workspaces, candidate accounts, "
        "service health, and the K P Is across every tenant.",
}
