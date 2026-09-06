"""Re-shoot the evaluation segment on its own.

The combined take opened on the Roles list — which in a long-lived demo tenant is a
wall of closed test roles — while the narration was already talking about the
assessment. This goes straight to the review and stays there, moving slowly enough to
read the dimensions and the quoted evidence.
"""

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
import json, pathlib, sys, urllib.request

from playwright.sync_api import sync_playwright

BASE = "https://hireiq-233250747481.asia-south1.run.app"
OUT = pathlib.Path(str(WORKDIR / "video"))

def api(m, p, b=None, t=None):
    r = urllib.request.Request(BASE + p, method=m); r.add_header("Content-Type", "application/json")
    if t: r.add_header("Authorization", "Bearer " + t)
    d = json.dumps(b).encode() if b is not None else None
    with urllib.request.urlopen(r, d, timeout=120) as x:
        return json.loads((x.read() or b"null").decode("utf-8", "replace"))

et = api("POST", "/api/employer/auth/login",
         {"email": "riya@fluxpay.io", "password": "Northwind-Demo-2026"})["token"]
jobs = api("GET", "/api/employer/jobs/", t=et)
items = jobs["items"] if isinstance(jobs, dict) and "items" in jobs else jobs
jid = max(j["id"] for j in items if j["status"] == "open")
apps = api("GET", f"/api/employer/jobs/{jid}/applications", t=et)
aid = (apps["items"] if isinstance(apps, dict) and "items" in apps else apps)[0]["id"]
print("job", jid, "application", aid)

with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--hide-scrollbars"])
    c = b.new_context(viewport={"width": 1600, "height": 900},
                      record_video_dir=str(OUT / "s4b"),
                      record_video_size={"width": 1600, "height": 900})
    p = c.new_page()
    p.goto(f"{BASE}/#/employer/login", wait_until="networkidle"); p.wait_for_timeout(900)
    p.fill('input[name="email"]', "riya@fluxpay.io")
    p.fill('input[name="password"]', "Northwind-Demo-2026")
    p.click('button[type="submit"]'); p.wait_for_timeout(3000)
    # straight to the assessment — no Roles list, no job page
    p.goto(f"{BASE}/#/employer/review/{aid}", wait_until="networkidle")
    p.wait_for_timeout(7000)                    # headline: overall / recommendation / adaptive
    for _ in range(16):                         # dimensions -> evidence -> coverage -> transcript
        p.mouse.wheel(0, 230)
        p.wait_for_timeout(2400)
    p.wait_for_timeout(2500)
    p.close(); c.close(); b.close()

src = max((OUT / "s4b").glob("*.webm"), key=lambda f: f.stat().st_mtime)
dst = OUT / "s4.webm"
if dst.exists(): dst.unlink()
src.rename(dst)
print("saved s4.webm", dst.stat().st_size // 1024, "KB")
