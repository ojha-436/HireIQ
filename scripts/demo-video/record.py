"""Screen-record the HireIQ product tour against the live service.

One Playwright context per segment, so each phase lands in its own clean video file and
the edit can cut between employer and candidate without a long idle gap in the middle.
Everything here drives the real UI at the real URL — nothing is mocked or faked for the
camera except the microphone device, which Chromium has to be told to synthesise.
"""

from __future__ import annotations

# --- paths: resolved from this file, so the scripts work in any checkout ---
import os as _os
import pathlib as _pl
import sys as _sys

REPO = _pl.Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO / "backend"
#: Everything the shoot produces. Override with HIREIQ_VIDEO_DIR; gitignored by default.
WORKDIR = _pl.Path(_os.environ.get("HIREIQ_VIDEO_DIR", REPO / ".demo-video"))
WORKDIR.mkdir(parents=True, exist_ok=True)
if str(BACKEND_DIR) not in _sys.path:
    _sys.path.insert(0, str(BACKEND_DIR))
# --- end paths ---

import json
import pathlib
import sys
import time
import urllib.request

BACKEND = str(BACKEND_DIR)
sys.path.insert(0, BACKEND)
import os
os.chdir(BACKEND_DIR)

from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "https://hireiq-233250747481.asia-south1.run.app"
OUT = WORKDIR / "video"
OUT.mkdir(parents=True, exist_ok=True)
W, H = 1600, 900
SILENCE = pathlib.Path(str(OUT.parent / "silence.wav"))
CAMERA = pathlib.Path(str(OUT.parent / "cam.y4m"))

EMP = ("riya@fluxpay.io", "Northwind-Demo-2026")
CAND = ("priya.m@example.com", "Candidate-Demo-2026")

JOB_TITLE = "Staff Backend Engineer, Payments"
JD = ("FluxPay is hiring a Staff Backend Engineer for the payments platform.\n\n"
      "You will own the settlement ledger: the service that decides, exactly once, how "
      "much money moves and when. You will work in Python on Kafka streams backed by "
      "PostgreSQL, running on Kubernetes in AWS.\n\n"
      "We are looking for 6-10 years of experience, strong system design judgement, and "
      "someone who has operated a high-throughput service in production and can explain "
      "the trade-offs they chose and why.")

BRIEF = """You are Priya Menon, interviewing for Staff Backend Engineer at FluxPay.
- 7 years backend; Python, Kafka, PostgreSQL, AWS, Kubernetes, Redis.
- At Zeta Payments you owned the settlement ledger: 12,000 events/sec across 40
  merchants. You moved nightly batch reconciliation to Kafka streaming and cut
  settlement latency from 18 hours to under 4 minutes.
- Exactly-once via idempotency keys (merchant_id + external_txn_id, unique index in
  Postgres) plus a compacted Kafka topic keyed by merchant for the running balance.
- Consumer groups sharded by merchant_id; dead-letter queue for poison messages.
- A replay harness fed a week of production events into staging and reconciled row by
  row against the legacy ledger before cutover.
"""


def api(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(req, data, timeout=120) as r:
        return json.loads(r.read() or "null")


def answer_for(question: str, already_said: list) -> str:
    from app.interview import gemini as GEM
    prior = ""
    if already_said:
        prior = ("\nYou have ALREADY said the following. Do not repeat these points and do "
                 "not start the same way twice — move the conversation forward:\n"
                 + "\n".join(f"- {a[:180]}" for a in already_said) + "\n")
    out = GEM.generate_text(
        BRIEF + prior + f'\nThe interviewer asked:\n"{question[:600]}"\n\n'
        "Answer as Priya in 55-85 words. Open by engaging with THIS question directly — "
        "never with a stock line about your background. Name the mechanism, the number, "
        "or the trade-off. Say plainly what YOU did versus what the team did. Plain prose.",
        temperature=0.5)
    return (out or "We enforced exactly-once with idempotency keys on merchant_id plus "
                   "external_txn_id, backed by a unique index in Postgres.").strip()


def ctx(browser, name):
    return browser.new_context(
        viewport={"width": W, "height": H},
        record_video_dir=str(OUT / name),
        record_video_size={"width": W, "height": H},
        permissions=["microphone"],
    )


def save(context, page, name):
    page.close()
    context.close()
    # Newest, not first: Playwright leaves one file per take in the directory, and
    # glob order is arbitrary — an earlier run's clip was silently published as this one.
    src = max((OUT / name).glob("*.webm"), key=lambda f: f.stat().st_mtime)
    dst = OUT / f"{name}.webm"
    if dst.exists():
        dst.unlink()
    src.rename(dst)
    print(f"   saved {dst.name} ({dst.stat().st_size//1024} KB)")


def login(page, role, creds):
    page.goto(f"{BASE}/#/{role}/login", wait_until="networkidle")
    page.wait_for_timeout(1200)
    page.fill('input[name="email"]', creds[0])
    page.wait_for_timeout(400)
    page.fill('input[name="password"]', creds[1])
    page.wait_for_timeout(600)
    page.click('button[type="submit"]')
    page.wait_for_timeout(3000)


def beat(page, ms=1400):
    page.wait_for_timeout(ms)


# ===========================================================================
def seg_employer_posts(browser) -> int:
    """Employer signs in and posts the role."""
    print("SEG 1 — employer posts the role")
    c = ctx(browser, "s1")
    p = c.new_page()
    login(p, "employer", EMP)
    beat(p, 2000)

    p.goto(f"{BASE}/#/employer/jobs/new", wait_until="networkidle")
    beat(p, 2000)
    p.fill('input[name="title"]', "")
    p.type('input[name="title"]', JOB_TITLE, delay=45)
    beat(p, 700)
    for field, value in (("department", "Payments Platform"), ("location", "Bengaluru")):
        try:
            p.fill(f'input[name="{field}"]', value)
            beat(p, 350)
        except Exception:
            pass
    try:
        p.fill('textarea[name="jd_text"]', "")
        p.type('textarea[name="jd_text"]', JD[:420], delay=6)
    except Exception:
        print("   ! jd_text textarea not found")
    beat(p, 1600)
    p.click('button[type="submit"]')
    beat(p, 4000)
    p.mouse.wheel(0, 320)
    beat(p, 2600)

    jobs = api("GET", "/api/employer/jobs/", token=TOKENS["emp"])
    items = jobs["items"] if isinstance(jobs, dict) and "items" in jobs else jobs
    jid = max(j["id"] for j in items if j["title"] == JOB_TITLE)
    save(c, p, "s1")
    return jid


def seg_candidate_applies(browser, jid):
    print("SEG 2 — candidate profile, match, apply")
    c = ctx(browser, "s2")
    p = c.new_page()
    login(p, "candidate", CAND)
    beat(p, 1800)
    p.goto(f"{BASE}/#/candidate/profile", wait_until="networkidle")
    beat(p, 2600)
    p.mouse.wheel(0, 380)
    beat(p, 2400)
    p.goto(f"{BASE}/#/candidate/jobs", wait_until="networkidle")
    beat(p, 3200)
    p.goto(f"{BASE}/#/candidate/jobs/{jid}", wait_until="networkidle")
    beat(p, 3000)
    p.mouse.wheel(0, 300)
    beat(p, 2000)
    for label in ("Apply", "Apply now", "Submit application"):
        try:
            p.get_by_role("button", name=label, exact=False).first.click(timeout=2500)
            break
        except Exception:
            continue
    beat(p, 3500)
    save(c, p, "s2")


def seg_interview(browser, sid):
    print("SEG 3 — the AI panel interview")
    c = ctx(browser, "s3")
    p = c.new_page()
    login(p, "candidate", CAND)
    beat(p, 1200)
    p.goto(f"{BASE}/#/candidate/interview/{sid}", wait_until="networkidle")
    beat(p, 2600)
    # AI disclosure gate: the start button stays disabled until consent is ticked.
    # Linger on it — an explicit, timestamped AI disclosure is a feature worth showing,
    # not a modal to click past.
    beat(p, 2500)
    p.mouse.wheel(0, 260)
    beat(p, 2600)
    try:
        p.locator("#agree").check(timeout=5000)
    except Exception:
        try:
            p.get_by_role("checkbox").first.check(timeout=5000)
        except Exception:
            print("   ! consent checkbox not found")
    beat(p, 1200)
    try:
        p.get_by_role("button", name="start the interview", exact=False).first.click(timeout=8000)
    except Exception:
        p.get_by_role("button", name="I understand", exact=False).first.click(timeout=8000)
    p.wait_for_selector("#type-input", timeout=90000)
    beat(p, 2500)
    # Fail fast rather than filming a dead room. When getUserMedia is refused the room
    # comes up "Not supported" with no panel, and the turn loop below would sit through
    # every timeout and produce a quarter of an hour of nothing.
    try:
        p.wait_for_function(
            "document.querySelectorAll('.transcript > *').length > 0", timeout=120000)
    except Exception:
        status = ""
        try:
            status = p.locator("#status").inner_text()
        except Exception:
            pass
        raise SystemExit(f"room never produced a turn (status={status!r}); aborting")

    seen = 0
    said = []
    for turn in range(4):
        # wait for a new interviewer turn to finish landing in the transcript
        deadline = time.time() + 90
        while time.time() < deadline:
            n = p.locator(".transcript .turn, .transcript > *").count()
            if n > seen:
                stable, last_n = 0, n
                while stable < 3 and time.time() < deadline:
                    p.wait_for_timeout(1000)
                    cur = p.locator(".transcript .turn, .transcript > *").count()
                    stable = stable + 1 if cur == last_n else 0
                    last_n = cur
                seen = last_n
                break
            p.wait_for_timeout(800)
        question = ""
        try:
            question = p.locator(".transcript > *").last.inner_text()
        except Exception:
            pass
        reply = answer_for(question, said)
        said.append(reply)
        print(f"   turn {turn+1}: typing {reply[:70]}…")
        p.fill("#type-input", "")
        p.type("#type-input", reply, delay=12)
        beat(p, 700)
        p.click("#send-btn")
        beat(p, 1500)

    beat(p, 6000)
    try:
        p.click("#end-btn", timeout=3000)
        beat(p, 2500)
        for label in ("End interview", "Yes", "Confirm"):
            try:
                p.get_by_role("button", name=label, exact=False).first.click(timeout=2000)
                break
            except Exception:
                continue
    except Exception:
        pass
    beat(p, 6000)
    save(c, p, "s3")


def seg_review(browser, jid, aid):
    print("SEG 4 — employer reviews the scored candidate")
    c = ctx(browser, "s4")
    p = c.new_page()
    login(p, "employer", EMP)
    beat(p, 1500)
    p.goto(f"{BASE}/#/employer/jobs/{jid}", wait_until="networkidle")
    beat(p, 3500)
    p.mouse.wheel(0, 350)
    beat(p, 2500)
    p.goto(f"{BASE}/#/employer/review/{aid}", wait_until="networkidle")
    beat(p, 5000)                      # the headline: overall, recommendation, adaptive %
    for _ in range(9):                 # dimensions, evidence, coverage, then the transcript
        p.mouse.wheel(0, 300)
        beat(p, 2600)
    save(c, p, "s4")


def seg_candidate_feedback(browser, aid):
    print("SEG 5 — the candidate's side of the outcome")
    c = ctx(browser, "s5")
    p = c.new_page()
    login(p, "candidate", CAND)
    beat(p, 2000)
    p.goto(f"{BASE}/#/candidate/applications/{aid}", wait_until="networkidle")
    beat(p, 4500)                      # pipeline timeline: AI panel done, founder next
    for _ in range(6):                 # then the released feedback itself
        p.mouse.wheel(0, 320)
        beat(p, 2600)
    save(c, p, "s5")


def seg_admin(browser):
    print("SEG 6 — admin portal")
    c = ctx(browser, "s6")
    p = c.new_page()
    p.goto(f"{BASE}/#/admin/login", wait_until="networkidle")
    beat(p, 1200)
    p.fill('input[name="username"]', "admin")
    p.fill('input[name="password"]', "admin@123")
    beat(p, 500)
    p.click('button[type="submit"]')
    beat(p, 3500)
    p.mouse.wheel(0, 300)
    beat(p, 2500)
    save(c, p, "s6")


TOKENS = {}

if __name__ == "__main__":
    TOKENS["emp"] = api("POST", "/api/employer/auth/login",
                        {"email": EMP[0], "password": EMP[1]})["token"]
    TOKENS["cand"] = api("POST", "/api/candidate/auth/login",
                         {"email": CAND[0], "password": CAND[1]})["token"]

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=[
            # The fake-UI flag has to stay: without it getUserMedia is refused outright
            # and the room comes up "Not supported" with no panel at all. What it also
            # does is accept the camera, whose default fake feed is a lurid green test
            # pattern — so point that at a plain dark frame instead.
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            "--use-file-for-fake-video-capture=" + str(CAMERA),
            # Chromium's default fake microphone emits a CONTINUOUS tone. The runtime
            # reads signal energy to decide when the candidate is speaking, so that tone
            # is heard as someone talking without pause — the panel correctly waits for
            # a gap that never comes, and never asks its first question. Feed it silence
            # instead, which is what a real microphone sounds like between answers.
            "--use-file-for-fake-audio-capture=" + str(SILENCE),
            "--autoplay-policy=no-user-gesture-required",
            "--hide-scrollbars",
        ])

        jid = seg_employer_posts(browser)
        print(f"   job id {jid}")

        # pipeline + publish are configured through the API so the tour stays on the
        # parts a viewer cares about; both are shown on the job page afterwards.
        api("PUT", f"/api/employer/jobs/{jid}/pipeline", {"stages": [
            {"seq": 1, "name": "AI Panel Interview", "kind": "ai_interview",
             "interview_config_json": {"preset": "panel",
                                       "panel": ["tech", "hiring_manager"]}},
            {"seq": 2, "name": "Founder Conversation", "kind": "human_interview"},
        ]}, token=TOKENS["emp"])
        api("POST", f"/api/employer/jobs/{jid}/publish", None, token=TOKENS["emp"])

        seg_candidate_applies(browser, jid)

        apps = api("GET", f"/api/employer/jobs/{jid}/applications", token=TOKENS["emp"])
        items = apps["items"] if isinstance(apps, dict) and "items" in apps else apps
        aid = items[0]["id"]
        sess = api("POST", f"/api/employer/applications/{aid}/start-interview",
                   None, token=TOKENS["emp"])
        sid = sess["session_id"]
        print(f"   application {aid}, session {sid}")

        seg_interview(browser, sid)

        for _ in range(20):
            a = api("GET", f"/api/employer/applications/{aid}/assessment",
                    token=TOKENS["emp"])
            if any(r.get("assessment") for r in (a.get("rounds") or [])):
                break
            time.sleep(6)
        api("POST", f"/api/employer/applications/{aid}/release-feedback", {},
            token=TOKENS["emp"])

        seg_review(browser, jid, aid)
        seg_candidate_feedback(browser, aid)
        seg_admin(browser)
        browser.close()

    print(f"\nDONE — job {jid}, application {aid}, session {sid}")
    print(f"clips in {OUT}")
