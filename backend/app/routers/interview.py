"""Live panel interview — session lifecycle + the WebSocket channel.

The runtime, moderator, personas and analyst are the ported engine (`app/interview/`).
This module is the HireIQ-shaped edge around it: it owns session creation from a
pipeline stage, the AI-disclosure gate, Agora tokens, and the socket that carries
candidate mic in and panel audio out.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import (APIRouter, Depends, Header, HTTPException, WebSocket,
                     WebSocketDisconnect, status)
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.db import get_db
from app.deps import current_candidate, current_employer
from app.engines import datasets as ds
from app.interview import agora_token as AT
from app.interview import broadcast as BC
from app.interview import context as CTX
from app.interview import personas as P
from app.interview import session as RT
from app.interview.broadcast import broadcast as _broadcast_to_employers
from app.models import (AuditLog, Candidate, InterviewAssessment, InterviewSession,
                        InterviewTurn, JobApplication, JobPosting, PipelineStage,
                        Tenant, TenantUser)
from app.security import decode_access_token, decode_candidate_token

router = APIRouter(tags=["interview"])

DISCLOSURE_TEXT = (
    "This interview is conducted by AI, not by people. Several AI interviewers will take "
    "turns speaking with you. Your spoken answers are transcribed and kept for 60 days; the "
    "assessment produced from them is kept and you may dispute it. No audio or video is stored "
    "unless your interviewer tells you otherwise. You may end the interview at any time."
)


def _channel_for(session_id: str) -> str:
    return "hireiq-{}".format(session_id[:24])


def _rtc_token(channel: str, uid: int) -> str | None:
    """An Agora channel token, or None when the project has no certificate configured.

    Returning None (rather than an empty string) lets the client distinguish
    "Agora is off" from "Agora is on but the token failed".
    """
    if not (channel and settings.AGORA_APP_ID and settings.AGORA_APP_CERTIFICATE):
        return None
    return AT.build_rtc_token(
        settings.AGORA_APP_ID, settings.AGORA_APP_CERTIFICATE,
        channel, uid, ttl_seconds=settings.AGORA_TOKEN_TTL_S,
    )


def _session_summary(db: Session, sess: InterviewSession, job: JobPosting,
                     cand: Candidate, stage: PipelineStage) -> dict[str, Any]:
    panel = [k for k in (sess.panel_json or []) if k in P.PERSONAS]
    return {
        "session_id": sess.id,
        "agora_channel": sess.agora_channel,
        "panel": [{"key": k, "label": P.get(k).label} for k in panel],
        "job_title": job.title,
        "candidate_name": cand.full_name,
        "stage_name": stage.name,
        "minutes": P.PRESETS.get(sess.preset, P.PRESETS["panel"])["minutes"],
        "status": sess.status,
    }


def _sections_from_profile(cand: Candidate) -> list[dict[str, Any]]:
    """HireIQ stores a profile dict; the grounding assembler consumes typed sections."""
    p = cand.profile_sections_json or {}
    sections: list[dict[str, Any]] = [
        {"type": "personal", "full_name": cand.full_name or ""},
    ]
    if p.get("summary"):
        sections.append({"type": "summary", "text": p["summary"]})
    if p.get("headline"):
        sections.append({"type": "summary", "text": p["headline"]})
    if p.get("skills"):
        sections.append({"type": "skills", "items": p["skills"]})
    if p.get("experience"):
        sections.append({"type": "experience", "items": p["experience"]})
    if p.get("education"):
        sections.append({"type": "education", "items": p["education"]})
    if p.get("projects"):
        # The grounding assembler has always understood projects — and rates them the
        # best probe surface there is — but nothing ever handed them over, so every
        # project a résumé contributed was parsed, stored, shown on the profile, and
        # then ignored by the panel.
        sections.append({"type": "projects", "items": p["projects"]})
    return sections


# ===========================================================================
# Employer — start an interview on an application
# ===========================================================================
@router.post("/api/employer/applications/{application_id}/start-interview", status_code=201)
def start_interview(
    application_id: int,
    employer: TenantUser = Depends(current_employer),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    app_row = db.get(JobApplication, application_id)
    if app_row is None or app_row.tenant_id != employer.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")

    job = db.get(JobPosting, app_row.job_id)
    cand = db.get(Candidate, app_row.candidate_id)
    if job is None or cand is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application is incomplete")

    stages = db.scalars(
        select(PipelineStage)
        .where(PipelineStage.job_id == job.id, PipelineStage.kind == "ai_interview")
        .order_by(PipelineStage.seq)
    ).all()
    if not stages:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This role has no AI interview stage configured.")

    # Walk the stages in order. A stage already finished is skipped; a stage with a
    # session still waiting to be joined hands that SAME session back rather than
    # minting a duplicate — pressing "Start interview" twice is a normal thing to do.
    target = None
    for stage in stages:
        pending = db.scalar(
            select(InterviewSession).where(
                InterviewSession.job_application_id == app_row.id,
                InterviewSession.pipeline_stage_id == stage.id,
                InterviewSession.status.in_(["setup", "live"]),
            )
        )
        if pending is not None:
            return _session_summary(db, pending, job, cand, stage)

        done = db.scalar(
            select(InterviewSession.id).where(
                InterviewSession.job_application_id == app_row.id,
                InterviewSession.pipeline_stage_id == stage.id,
                InterviewSession.status == "ended",
            )
        )
        if not done:
            target = stage
            break
    if target is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Every AI interview stage for this application is already complete.")

    cfg = target.interview_config_json or {}
    preset = cfg.get("preset") or settings.INTERVIEW_PRESET
    if preset not in P.PRESETS:
        preset = "panel"

    panel = cfg.get("panel") or P.propose_panel(job.required_skills_json or [], job.title or "")
    panel = [k for k in panel if k in P.PERSONAS][:P.PRESETS[preset]["panel_size"]] or ["tech"]

    grounding = CTX.build(
        job={
            "job_title": job.title,
            "company": "",
            "required_skill_ids": ds.to_ids(job.required_skills_json or []),
            "sector": "",
        },
        sections=_sections_from_profile(cand),
    )

    sess = InterviewSession(
        job_application_id=app_row.id,
        pipeline_stage_id=target.id,
        candidate_id=cand.id,
        initiated_by=employer.id,
        preset=preset,
        panel_json=panel,
        status="setup",
        state_json={"grounding": grounding},
    )
    db.add(sess)
    db.flush()
    sess.agora_channel = _channel_for(sess.id)

    app_row.status = "in_progress"
    app_row.last_activity_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(sess)

    return _session_summary(db, sess, job, cand, target)


# ===========================================================================
# Candidate — pending interviews, disclosure, join
# ===========================================================================
@router.get("/api/candidate/me/interviews/pending")
def pending_interviews(
    cand: Candidate = Depends(current_candidate), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(InterviewSession)
        .where(InterviewSession.candidate_id == cand.id,
               InterviewSession.status.in_(["setup", "live"]),
               InterviewSession.session_type != "practice")
        .order_by(InterviewSession.created_at.desc())
    ).all()

    out = []
    for s in rows:
        app_row = db.get(JobApplication, s.job_application_id) if s.job_application_id else None
        job = db.get(JobPosting, app_row.job_id) if app_row else None
        out.append({
            "session_id": s.id,
            "job_title": job.title if job else "",
            "status": s.status,
            "panel": [{"key": k, "label": P.get(k).label} for k in (s.panel_json or [])],
            "disclosure_accepted": s.disclosure_accepted_at is not None,
        })
    return out


@router.get("/api/candidate/sessions/{session_id}")
def session_detail(
    session_id: str,
    cand: Candidate = Depends(current_candidate),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    s = db.get(InterviewSession, session_id)
    if s is None or s.candidate_id != cand.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview not found")

    app_row = db.get(JobApplication, s.job_application_id) if s.job_application_id else None
    job = db.get(JobPosting, app_row.job_id) if app_row else None
    is_practice = s.session_type == "practice"
    practice_job_title = ((s.state_json or {}).get("practice") or {}).get("job_title") or ""
    return {
        "session_id": s.id,
        "status": s.status,
        "preset": s.preset,
        "minutes": P.PRESETS.get(s.preset, P.PRESETS["panel"])["minutes"],
        "is_practice": is_practice,
        "job_title": (practice_job_title if is_practice else (job.title if job else "")),
        "panel": [
            {"key": k, "label": P.get(k).label, "voice": P.get(k).voice, "bot_uid": P.get(k).bot_uid}
            for k in (s.panel_json or [])
        ],
        "disclosure_text": DISCLOSURE_TEXT,
        "disclosure_accepted": s.disclosure_accepted_at is not None,
        "agora_channel": s.agora_channel,
    }


@router.post("/api/candidate/sessions/{session_id}/consent")
def accept_disclosure(
    session_id: str,
    cand: Candidate = Depends(current_candidate),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Layer 1 of the AI disclosure. The session cannot go live until this is stored."""
    s = db.get(InterviewSession, session_id)
    if s is None or s.candidate_id != cand.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview not found")
    if s.disclosure_accepted_at is None:
        s.disclosure_accepted_at = datetime.now(timezone.utc)
        db.commit()
    return {"ok": True, "accepted_at": s.disclosure_accepted_at}


@router.get("/api/candidate/sessions/{session_id}/agora-token")
def candidate_agora_token(
    session_id: str,
    cand: Candidate = Depends(current_candidate),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    s = db.get(InterviewSession, session_id)
    if s is None or s.candidate_id != cand.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview not found")
    if s.disclosure_accepted_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Accept the AI interview disclosure before joining.")

    channel = s.agora_channel or _channel_for(s.id)
    uid = int(cand.id) % 100000 or 1
    return {
        "app_id": settings.AGORA_APP_ID,
        "channel": channel,
        "uid": uid,
        "token": _rtc_token(channel, uid),
        "enabled": bool(settings.AGORA_APP_ID),
    }


# ===========================================================================
# The live channel
# ===========================================================================
def _ws_auth(token: str) -> str | None:
    """Only a candidate-audience JWT may open an interview socket."""
    if not token:
        return None
    payload = decode_candidate_token(token)
    return payload.get("sub") if payload else None


@router.websocket("/api/interview/ws/{session_id}")
async def interview_ws(websocket: WebSocket, session_id: str, token: str = "") -> None:
    candidate_id = _ws_auth(token)
    if not candidate_id:
        await websocket.close(code=4401)
        return

    db = SessionLocal()
    try:
        row = db.get(InterviewSession, session_id)
        if row is None:
            await websocket.close(code=4404)
            return
        if str(row.candidate_id) != str(candidate_id):
            await websocket.close(code=4401)
            return
        # The disclosure gate is enforced server-side, not by the UI.
        if row.disclosure_accepted_at is None:
            await websocket.close(code=4409)
            return
        if row.status == "ended":
            await websocket.close(code=4410)
            return

        panel = list(row.panel_json or ["tech"])
        preset = row.preset
        grounding = (row.state_json or {}).get("grounding") or {}
        agora_channel = row.agora_channel or ""
    finally:
        db.close()

    # ConvoAI agents need a channel token of their own to join. Minted here so the
    # runtime never touches Agora credentials directly.
    # uid 0 means "valid for any uid on this channel", which is what the agent pool needs.
    agora_token_for_agents = _rtc_token(agora_channel, 0) or ""

    await websocket.accept()
    send_lock = asyncio.Lock()

    async def emit(msg: dict[str, Any]) -> None:
        async with send_lock:
            try:
                await websocket.send_text(json.dumps(msg))
            except (WebSocketDisconnect, RuntimeError):
                pass
        # Mirror every event to any employer watching this session over SSE.
        asyncio.create_task(_broadcast_to_employers(session_id, msg))

    async def emit_audio(pcm: bytes) -> None:
        async with send_lock:
            try:
                await websocket.send_bytes(pcm)
            except (WebSocketDisconnect, RuntimeError):
                pass

    runtime = RT.InterviewRuntime(
        session_id=session_id, user_id=str(candidate_id), panel=panel, preset=preset,
        grounding=grounding, emit=emit, emit_audio=emit_audio,
    )
    runtime.agora_channel = agora_channel
    runtime.agora_token = agora_token_for_agents
    RT.register(runtime)

    try:
        await runtime.start()
        while not runtime.ended:
            frame = await websocket.receive()
            if frame.get("type") == "websocket.disconnect":
                break
            if frame.get("bytes") is not None:
                await runtime.on_audio(frame["bytes"])   # PCM16 mono 16 kHz
                continue
            raw = frame.get("text")
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except (TypeError, ValueError):
                continue

            mtype = msg.get("type")
            if mtype == "speech_start":
                # The browser's VAD decides turn boundaries, not the model's — that is
                # what leaves the moderator in control of who answers.
                await runtime.on_speech_start()
            elif mtype == "speech_end":
                await runtime.on_speech_end()
            elif mtype == "activity_end":
                await runtime.on_activity_end()
            elif mtype == "text":
                await runtime.on_text(str(msg.get("text") or "")[:4000])
            elif mtype == "end":
                await runtime.finish(reason="candidate")
                break
            elif mtype == "ping":
                await emit({"type": "pong", "seconds_remaining": runtime.time_left_s()})
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        await emit({"type": "error", "detail": "{}: {}".format(type(exc).__name__, exc)})
    finally:
        # A mid-interview disconnect is 'abandoned', not 'ended'. The per-turn
        # state_json checkpoint is what a resume reads.
        if not runtime.ended:
            db2 = SessionLocal()
            try:
                r = db2.get(InterviewSession, session_id)
                if r and r.status == "live":
                    r.status = "abandoned"
                    db2.commit()
            finally:
                db2.close()
            await runtime.floor.close_all()
            # Agora agents are billed and hold channel slots. An abandoned interview
            # would otherwise leak them until their idle_timeout expires.
            await runtime._convoai_shutdown()
        RT.unregister(session_id)
        try:
            await websocket.close()
        except RuntimeError:
            pass


# ===========================================================================
# Employer live monitor — SSE, Panel Memory, and the whisper channel (W0)
# ===========================================================================
def _employer_owns_session(db: Session, session_id: str, employer: TenantUser) -> InterviewSession:
    sess = db.get(InterviewSession, session_id)
    if sess is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview not found")
    app_row = db.get(JobApplication, sess.job_application_id) if sess.job_application_id else None
    if app_row is None or app_row.tenant_id != employer.tenant_id:
        # 404 rather than 403: do not confirm the session exists to another tenant.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview not found")
    return sess


@router.get("/api/employer/monitor/{session_id}")
async def monitor(
    session_id: str,
    token: str = "",
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Server-sent events: the same stream the candidate's socket produces.

    Read-only by construction — this endpoint has no way to influence the interview.
    Whispering is a separate, audited POST.

    Auth accepts a `token` query parameter as well as a header, because EventSource
    cannot set headers. The token is still an employer-audience JWT verified the same
    way; the query string only changes where it is carried.
    """
    raw = token or (authorization or "").removeprefix("Bearer ").strip()
    payload = decode_access_token(raw) if raw else None
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing token")
    employer = db.get(TenantUser, int(payload["sub"]))
    if employer is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user")

    sess = _employer_owns_session(db, session_id, employer)

    runtime = RT.get_runtime(session_id)
    opening: list[dict[str, Any]] = [{
        "type": "hello", "status": sess.status,
        "panel": [{"key": k, "label": P.get(k).label} for k in (sess.panel_json or [])
                  if k in P.PERSONAS],
    }]
    # A monitor joining mid-interview gets the current state, not an empty screen.
    if runtime is not None:
        opening.append({"type": "panel_memory", **runtime.panel_memory()})

    queue = BC.subscribe(session_id)

    async def stream():
        try:
            for event in opening:
                yield "data: {}\n\n".format(json.dumps(event))
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"    # keep proxies from closing the connection
                    continue
                yield "data: {}\n\n".format(json.dumps(event))
                if event.get("type") == "ended":
                    break
        finally:
            BC.unsubscribe(session_id, queue)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@router.post("/api/employer/whisper/{session_id}")
async def whisper(
    session_id: str,
    payload: dict[str, Any],
    employer: TenantUser = Depends(current_employer),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Rule W0: inject a question the employer wants asked.

    It is surfaced at the next natural handoff rather than immediately, so it never
    cuts across a candidate mid-answer. Every whisper is written to the audit log —
    a human influenced this interview and the record has to say so.
    """
    sess = _employer_owns_session(db, session_id, employer)
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Write the question you want asked.")
    if len(text) > 400:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Keep it under 400 characters — it is spoken aloud.")

    runtime = RT.get_runtime(session_id)
    if runtime is None or sess.status != "live":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This interview is not live, so there is nothing to whisper into.")

    db.add(AuditLog(
        tenant_id=employer.tenant_id, actor_id=employer.id, action="interview.whisper",
        subject_type="interview_session", subject_id=0,
        payload_json={"session_id": session_id, "text": text},
    ))
    db.commit()

    await runtime.inject_whisper(text)
    return {"ok": True, "queued": True}


@router.get("/api/employer/sessions/{session_id}/panel-memory")
def panel_memory_snapshot(
    session_id: str,
    employer: TenantUser = Depends(current_employer),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Polling fallback for environments where SSE is proxied away."""
    _employer_owns_session(db, session_id, employer)
    runtime = RT.get_runtime(session_id)
    if runtime is None:
        return {"live": False}
    return {"live": True, **runtime.panel_memory()}


# ===========================================================================
# Phase 6 — assessment review, decisions, and gated feedback release
# ===========================================================================
def _owned_application(db: Session, application_id: int, employer: TenantUser) -> JobApplication:
    app_row = db.get(JobApplication, application_id)
    if app_row is None or app_row.tenant_id != employer.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    return app_row


def _adaptivity(turns: list[InterviewTurn]) -> dict[str, Any]:
    """What share of the interviewer's questions came from this conversation.

    The product's central claim is that the panel adapts rather than reciting a list.
    This turns that into a number a reviewer can check: `generated` means the moderator
    had something from the candidate's own answers to work with; `bank` means nothing in
    the conversation drove the question and the O*NET fallback supplied it.
    """
    asked = [t for t in turns if t.speaker != "candidate" and t.question_source]
    if not asked:
        return {"total": 0, "generated": 0, "bank": 0, "scenario": 0, "generated_pct": None}
    tally = {"generated": 0, "bank": 0, "scenario": 0}
    for t in asked:
        tally[t.question_source] = tally.get(t.question_source, 0) + 1
    adaptive = tally["generated"] + tally["scenario"]
    return {"total": len(asked), **tally,
            "generated_pct": round(100 * adaptive / len(asked))}


def _report_lines(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a report into citable lines, dropping anything uncited.

    An assessment line without `turn_ids` is an opinion, not evidence. The engine
    already enforces this when building the report; this is the read-side guarantee so
    a hand-edited or legacy row can never surface an unfounded claim to a reviewer.

    `quote` is snapshotted into the report at build time, which is what keeps citations
    readable after the 60-day transcript purge.
    """
    out: list[dict[str, Any]] = []
    for dim in (report.get("per_dimension") or []):
        for line in (dim.get("evidence") or []):
            turn_ids = line.get("turn_ids") or []
            if not turn_ids:
                continue
            out.append({
                "dimension": dim.get("dimension"),
                "score": dim.get("score"),
                "band": dim.get("band"),
                "verdict": dim.get("verdict", ""),
                "claim": line.get("claim") or "",
                "turn_ids": turn_ids,
                "quote": line.get("quote") or "",
            })
    return out


@router.get("/api/employer/applications/{application_id}/assessment")
def assessment(
    application_id: int,
    employer: TenantUser = Depends(current_employer),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Full review packet: scores, evidence-linked lines, transcript, and audit trail."""
    app_row = _owned_application(db, application_id, employer)
    job = db.get(JobPosting, app_row.job_id)
    cand = db.get(Candidate, app_row.candidate_id)

    sessions = db.scalars(
        select(InterviewSession)
        .where(InterviewSession.job_application_id == app_row.id)
        .order_by(InterviewSession.created_at)
    ).all()

    rounds = []
    for sess in sessions:
        stage = db.get(PipelineStage, sess.pipeline_stage_id) if sess.pipeline_stage_id else None
        row = db.scalar(select(InterviewAssessment).where(
            InterviewAssessment.session_id == sess.id))
        turns = db.scalars(
            select(InterviewTurn).where(InterviewTurn.session_id == sess.id)
            .order_by(InterviewTurn.seq)
        ).all()

        report = (row.report_json or {}) if row else {}
        rounds.append({
            "session_id": sess.id,
            "stage_name": stage.name if stage else "AI Interview",
            "status": sess.status,
            "started_at": sess.started_at,
            "duration_s": sess.duration_s,
            "panel": [{"key": k, "label": P.get(k).label}
                      for k in (sess.panel_json or []) if k in P.PERSONAS],
            "disclosure_accepted_at": sess.disclosure_accepted_at,
            "assessment": None if row is None else {
                "overall": row.overall,
                "recommendation": row.recommendation,
                "percentile": row.percentile,
                "percentile_n": row.percentile_n,
                "released_to_candidate": bool(row.released_to_candidate),
                "per_skill": row.per_skill_json or {},
                "summary": report.get("summary", ""),
                "dimensions": report.get("per_dimension") or [],
                "strengths": report.get("strengths") or [],
                "focus_areas": report.get("focus_areas") or [],
                "contradictions": report.get("contradictions") or [],
                "coverage": report.get("coverage") or {},
                "ai_disclosure": report.get("ai_disclosure", ""),
                "evidence": _report_lines(report),
                "difficulty_trajectory": row.difficulty_trajectory_json or [],
                "adaptivity": _adaptivity(turns),
            },
            "transcript": [
                {"id": t.id, "seq": t.seq, "speaker": t.speaker, "text": t.text,
                 "rule_fired": t.rule_fired, "difficulty": t.difficulty_at_turn,
                 "flags": [f.get("type") for f in (t.flags_json or [])]}
                for t in turns
            ],
        })

    audit = db.scalars(
        select(AuditLog)
        .where(AuditLog.tenant_id == employer.tenant_id,
               AuditLog.subject_type == "application",
               AuditLog.subject_id == app_row.id)
        .order_by(AuditLog.created_at.desc())
    ).all()

    stage = db.get(PipelineStage, app_row.current_stage_id) if app_row.current_stage_id else None
    return {
        "application": {
            "id": app_row.id, "status": app_row.status,
            "applied_at": app_row.applied_at,
            "decision_reason": app_row.decision_reason,
            "current_stage": stage.name if stage else None,
            "current_stage_seq": stage.seq if stage else None,
        },
        "job": {"id": job.id, "title": job.title} if job else None,
        "candidate": {
            "id": cand.id, "full_name": cand.full_name, "email": cand.email,
            "headline": (cand.profile_sections_json or {}).get("headline"),
            "years_experience": cand.years_experience,
        } if cand else None,
        "rounds": rounds,
        "audit": [{"action": a.action, "at": a.created_at, "payload": a.payload_json}
                  for a in audit],
    }


def _decide(db: Session, app_row: JobApplication, employer: TenantUser, *,
            action: str, reason: str, release: bool) -> dict[str, Any]:
    """Shared body for advance / reject / override.

    Two invariants live here, both from the compliance checklist:
      * a reason is mandatory and is stored — no silent decisions;
      * feedback is released to the candidate only as part of an explicit decision,
        which is what stops candidates tuning their answers between rounds.
    """
    if not reason.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Give a reason — it is stored on the record and shown to no one else.")

    stages = db.scalars(
        select(PipelineStage).where(PipelineStage.job_id == app_row.job_id)
        .order_by(PipelineStage.seq)
    ).all()

    moved_to = None
    if action == "advance":
        current_seq = 0
        if app_row.current_stage_id:
            cur = db.get(PipelineStage, app_row.current_stage_id)
            current_seq = cur.seq if cur else 0
        nxt = next((s for s in stages if s.seq > current_seq), None)
        if nxt is None:
            app_row.status = "offer"
            app_row.current_stage_id = None
        else:
            app_row.status = "in_progress"
            app_row.current_stage_id = nxt.id
            moved_to = nxt.name
    elif action == "reject":
        app_row.status = "rejected"
    elif action == "offer":
        app_row.status = "offer"

    app_row.decision_reason = reason.strip()
    app_row.last_activity_at = datetime.now(timezone.utc)

    released = 0
    if release:
        rows = db.scalars(
            select(InterviewAssessment)
            .join(InterviewSession, InterviewSession.id == InterviewAssessment.session_id)
            .where(InterviewSession.job_application_id == app_row.id)
        ).all()
        for row in rows:
            if not row.released_to_candidate:
                row.released_to_candidate = True
                row.released_at = datetime.now(timezone.utc)
                released += 1

    db.add(AuditLog(
        tenant_id=employer.tenant_id, actor_id=employer.id,
        action="application.{}".format(action),
        subject_type="application", subject_id=app_row.id,
        payload_json={"reason": reason.strip(), "released": released,
                      "moved_to": moved_to, "status": app_row.status},
    ))
    db.commit()
    return {"ok": True, "status": app_row.status, "moved_to": moved_to,
            "assessments_released": released}


@router.post("/api/employer/applications/{application_id}/advance")
def advance(
    application_id: int,
    payload: dict[str, Any],
    employer: TenantUser = Depends(current_employer),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    app_row = _owned_application(db, application_id, employer)
    return _decide(db, app_row, employer, action="advance",
                   reason=str(payload.get("reason") or ""),
                   release=bool(payload.get("release_feedback", True)))


@router.post("/api/employer/applications/{application_id}/reject")
def reject(
    application_id: int,
    payload: dict[str, Any],
    employer: TenantUser = Depends(current_employer),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Reject, with a reason. Feedback release defaults ON for a rejection.

    A candidate who is out of the process has nothing left to game, and being told why
    is the entire point of an evidence-linked assessment.
    """
    app_row = _owned_application(db, application_id, employer)
    return _decide(db, app_row, employer, action="reject",
                   reason=str(payload.get("reason") or ""),
                   release=bool(payload.get("release_feedback", True)))


@router.post("/api/employer/applications/{application_id}/release-feedback")
def release_feedback(
    application_id: int,
    employer: TenantUser = Depends(current_employer),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Release feedback without deciding — for a candidate still mid-process."""
    app_row = _owned_application(db, application_id, employer)
    rows = db.scalars(
        select(InterviewAssessment)
        .join(InterviewSession, InterviewSession.id == InterviewAssessment.session_id)
        .where(InterviewSession.job_application_id == app_row.id)
    ).all()
    if not rows:
        raise HTTPException(status.HTTP_409_CONFLICT, "There is no assessment to release yet.")

    released = 0
    for row in rows:
        if not row.released_to_candidate:
            row.released_to_candidate = True
            row.released_at = datetime.now(timezone.utc)
            released += 1

    db.add(AuditLog(
        tenant_id=employer.tenant_id, actor_id=employer.id,
        action="application.release_feedback", subject_type="application",
        subject_id=app_row.id, payload_json={"released": released},
    ))
    db.commit()
    return {"ok": True, "assessments_released": released}


# ===========================================================================
# Candidate — application detail, stage progress, and released feedback
# ===========================================================================
#: Statuses the candidate sees, and what each means in plain words. The wording is
#: deliberately not the internal enum: "in_progress" tells a person nothing.
STATUS_COPY = {
    "applied": ("Application received", "The hiring team has your application."),
    "in_progress": ("In progress", "You are moving through the process."),
    "offer": ("Offer stage", "The hiring team has moved you to the offer stage."),
    "rejected": ("Not moving forward", "The hiring team decided not to continue."),
}


@router.get("/api/candidate/me/applications/{application_id}")
def application_detail(
    application_id: int,
    cand: Candidate = Depends(current_candidate),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Everything the candidate is entitled to see about one application.

    Two things are deliberately withheld until the employer releases feedback: scores
    and evidence. What is NEVER withheld is where they stand — a candidate being left
    to guess whether they are still in the process is the thing this product exists to
    fix. Rejected without released feedback still shows the stage they reached and the
    fact that a decision was made.
    """
    app_row = db.scalar(select(JobApplication).where(
        JobApplication.id == application_id, JobApplication.candidate_id == cand.id))
    if app_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")

    job = db.get(JobPosting, app_row.job_id)
    tenant = db.get(Tenant, app_row.tenant_id)
    stages = db.scalars(
        select(PipelineStage).where(PipelineStage.job_id == app_row.job_id)
        .order_by(PipelineStage.seq)
    ).all()

    sessions = db.scalars(
        select(InterviewSession)
        .where(InterviewSession.job_application_id == app_row.id)
        .order_by(InterviewSession.created_at)
    ).all()
    by_stage = {s.pipeline_stage_id: s for s in sessions}

    current_seq = 0
    if app_row.current_stage_id:
        cur = db.get(PipelineStage, app_row.current_stage_id)
        current_seq = cur.seq if cur else 0

    timeline = []
    for stage in stages:
        sess = by_stage.get(stage.id)
        if sess is not None and sess.status == "ended":
            state = "done"
        elif sess is not None:
            state = "active"
        elif stage.seq == current_seq:
            state = "active"
        elif stage.seq < current_seq:
            state = "done"
        else:
            state = "upcoming"
        # A finished process has no upcoming stages. Rejected closes them; an offer
        # means the candidate cleared everything, so nothing is still ahead of them.
        if state == "upcoming":
            if app_row.status == "rejected":
                state = "closed"
            elif app_row.status == "offer":
                state = "done"

        timeline.append({
            "seq": stage.seq, "name": stage.name, "kind": stage.kind, "state": state,
            "session_id": sess.id if sess is not None else None,
            "session_status": sess.status if sess is not None else None,
            "panel": [{"key": k, "label": P.get(k).label}
                      for k in ((sess.panel_json if sess else None)
                                or (stage.interview_config_json or {}).get("panel") or [])
                      if k in P.PERSONAS],
        })

    # Released feedback only.
    feedback = []
    for sess in sessions:
        row = db.scalar(select(InterviewAssessment).where(
            InterviewAssessment.session_id == sess.id))
        if row is None or not row.released_to_candidate:
            continue
        stage = db.get(PipelineStage, sess.pipeline_stage_id) if sess.pipeline_stage_id else None
        turns = {
            t.id: t.text for t in db.scalars(
                select(InterviewTurn).where(InterviewTurn.session_id == sess.id)).all()
        }
        report = row.report_json or {}
        lines = []
        for line in _report_lines(report):
            # Resolve each citation to the words the candidate actually said. Quotes are
            # snapshotted into the report before the 60-day purge, so this still works
            # after the raw transcript is gone.
            quoted = line.get("quote") or " / ".join(
                turns[t] for t in line["turn_ids"] if t in turns)
            lines.append({**line, "quote": quoted})

        feedback.append({
            "session_id": sess.id,
            "stage_name": stage.name if stage else "AI Interview",
            "released_at": row.released_at,
            "overall": row.overall,
            "recommendation": row.recommendation,
            "percentile": row.percentile if (row.percentile_n or 0) >= 5 else None,
            "percentile_n": row.percentile_n,
            "summary": report.get("summary", ""),
            "dimensions": report.get("per_dimension") or [],
            "strengths": report.get("strengths") or [],
            "focus_areas": report.get("focus_areas") or [],
            "contradictions": report.get("contradictions") or [],
            # Layer 4 of the AI disclosure: printed on every report the candidate reads.
            "ai_disclosure": report.get("ai_disclosure", ""),
            "evidence": lines,
            "adaptivity": _adaptivity(list(db.scalars(
                select(InterviewTurn).where(InterviewTurn.session_id == sess.id)).all())),
            "per_skill": row.per_skill_json or {},
            "difficulty_trajectory": row.difficulty_trajectory_json or [],
        })

    label, blurb = STATUS_COPY.get(app_row.status, ("In review", ""))
    has_assessment = bool(sessions) and any(
        db.scalar(select(InterviewAssessment.id).where(
            InterviewAssessment.session_id == s.id)) for s in sessions)

    return {
        "id": app_row.id,
        "job": {"id": job.id, "title": job.title,
                "location": job.location, "country": job.country} if job else None,
        "company_name": tenant.name if tenant else "",
        "status": app_row.status,
        "status_label": label,
        "status_blurb": blurb,
        "applied_at": app_row.applied_at,
        "last_activity_at": app_row.last_activity_at,
        "timeline": timeline,
        "feedback": feedback,
        # Honest about the gap: an assessment exists but has not been released.
        "feedback_pending": has_assessment and not feedback,
        "dispute_available": bool(feedback),
    }
