"""Prior-round context for multi-stage hiring pipelines (changeplan.md Q4).

When a candidate reaches Round 2 (e.g. AI Panel after AI Screen), Round 2's
interviewer should know what Round 1 already established — so it can go deeper
rather than repeating ground. We inject a compact summary (~400 tokens) of prior
assessments, NOT the verbatim transcript (too long; also the next interviewer
should form their own impression of new answers).
"""
from __future__ import annotations

from typing import List

from sqlalchemy.orm import Session

from app.models import InterviewAssessment, InterviewSession, PipelineStage

MAX_PRIOR_CHARS = 1600


def build_prior_round_summary(
    job_application_id: str,
    current_stage_seq: int,
    db: Session,
) -> str:
    """Return a compact text block of prior-round findings, or empty string if first round."""
    prior = (
        db.query(InterviewAssessment, InterviewSession, PipelineStage)
        .join(InterviewSession, InterviewAssessment.session_id == InterviewSession.id)
        .join(PipelineStage, InterviewSession.pipeline_stage_id == PipelineStage.id)
        .filter(
            InterviewSession.job_application_id == job_application_id,
            PipelineStage.seq < current_stage_seq,
            InterviewSession.status == "ended",
        )
        .order_by(PipelineStage.seq.asc())
        .all()
    )
    if not prior:
        return ""

    lines: List[str] = ["PRIOR ROUND FINDINGS (earlier stages of this candidate's interview):"]
    for assess, sess, stage in prior:
        report = assess.report_json or {}
        lines.append(f"\nRound: {stage.name} (overall {assess.overall}/100, {assess.recommendation})")
        dims = report.get("per_dimension") or []
        strong = [d["dimension"].replace("_", " ") for d in dims if d.get("score", 0) >= 3.5]
        weak = [d["dimension"].replace("_", " ") for d in dims if d.get("score", 0) <= 2.5]
        if strong:
            lines.append(f"  Strengths confirmed: {', '.join(strong[:4])}")
        if weak:
            lines.append(f"  Gaps to probe further: {', '.join(weak[:4])}")
        flags = report.get("focus_areas") or []
        flag_notes = list({f.get("claim", "")[:60] for f in flags[:3] if f.get("claim")})
        if flag_notes:
            lines.append(f"  Open concerns: {'; '.join(flag_notes)}")
        lines.append(f"  Contradictions logged: {len(report.get('contradictions') or [])}")

    lines.append(
        "\nDo NOT repeat questions already answered. Build on established facts. "
        "Probe the gaps listed above."
    )
    return "\n".join(lines)[:MAX_PRIOR_CHARS]
