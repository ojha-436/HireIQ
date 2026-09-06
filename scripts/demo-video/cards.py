"""Render the video's title cards as real HTML, then screenshot them.

Using the browser rather than ffmpeg's drawtext buys proper typography, gradients and
layout for free, and keeps the cards visually related to the product they introduce —
same accent, same display font. ffmpeg animates the stills afterwards.
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

import pathlib
import sys


from playwright.sync_api import sync_playwright  # noqa: E402

OUT = WORKDIR / "cards"
OUT.mkdir(parents=True, exist_ok=True)

SHELL = """
<link href="https://fonts.googleapis.com/css2?family=Baloo+2:wght@600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    width:1600px; height:900px; overflow:hidden;
    background:
      radial-gradient(1100px 700px at 18% 12%, rgba(45,212,125,.16), transparent 62%),
      radial-gradient(900px 620px at 86% 88%, rgba(45,212,125,.09), transparent 60%),
      #0a0e0c;
    color:#eef4f0; font-family:Inter,-apple-system,sans-serif;
    display:flex; align-items:center; justify-content:center;
  }}
  .wrap {{ width:1180px; }}
  .kicker {{
    font:600 15px/1 Inter; letter-spacing:.32em; text-transform:uppercase;
    color:#2dd47d; margin-bottom:26px; display:flex; align-items:center; gap:14px;
  }}
  .kicker::before {{ content:''; width:44px; height:2px; background:#2dd47d; display:block; }}
  h1 {{ font:700 {size}px/1.06 'Baloo 2',Inter,sans-serif; letter-spacing:-.018em; }}
  h1 em {{ font-style:normal; color:#2dd47d; }}
  p {{ font:400 25px/1.5 Inter; color:#9db0a6; margin-top:26px; max-width:900px; }}
  .num {{
    position:absolute; right:96px; bottom:76px;
    font:700 190px/1 'Baloo 2',Inter,sans-serif; color:rgba(45,212,125,.10);
  }}
  .rule {{ height:3px; width:112px; background:#2dd47d; margin-top:38px; border-radius:2px; }}
  .brand {{
    position:absolute; left:96px; bottom:76px;
    font:600 17px/1 Inter; letter-spacing:.16em; color:#5d6f66; text-transform:uppercase;
  }}
</style>
<div class="wrap">{body}</div>
{num}
<div class="brand">HireIQ</div>
"""


def card(name, kicker, title, sub="", number="", size=76):
    body = f'<div class="kicker">{kicker}</div><h1>{title}</h1>'
    if sub:
        body += f"<p>{sub}</p>"
    body += '<div class="rule"></div>'
    num = f'<div class="num">{number}</div>' if number else ""
    return name, SHELL.format(body=body, num=num, size=size)


CARDS = [
    card("00_open", "AI interview panel",
         "Hiring decisions,<br><em>evidenced.</em>",
         "HireIQ runs a multi-persona AI interview panel that adapts to what a candidate "
         "actually says — and shows its working.", size=88),
    card("01_post", "Step one", "Post the role",
         "The job description is parsed into required skills. Those skills become the "
         "only things the panel is allowed to probe.", "01"),
    card("02_match", "Step two", "Match, then apply",
         "A résumé becomes a structured profile. The candidate sees exactly which "
         "required skills they match — and which they do not.", "02"),
    card("03_interview", "Step three", "The panel interviews",
         "Several AI interviewers take turns. A moderator decides who speaks next, and "
         "why — every answer is scored before the next question is chosen.", "03"),
    card("04_score", "Step four", "Evidence, not vibes",
         "Every dimension is scored against quoted evidence from the transcript, so a "
         "recommendation can always be traced back to something the candidate said.",
         "04"),
    card("05_candidate", "Step five", "The candidate is told",
         "Feedback is released deliberately, and the candidate sees where they are in "
         "the pipeline — not silence.", "05"),
    card("06_admin", "Governance", "Oversight built in",
         "Workspace suspension, live health and KPIs. AI disclosure is enforced "
         "server-side — the interview cannot start without it.", "06"),
    card("07_close", "", "Adaptive interviews,<br><em>auditable outcomes.</em>",
         "hireiq-233250747481.asia-south1.run.app", size=82),
]

with sync_playwright() as pw:
    b = pw.chromium.launch()
    p = b.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=2)
    for name, html in CARDS:
        p.set_content(html)
        p.wait_for_timeout(700)          # let the webfont land
        p.screenshot(path=str(OUT / f"{name}.png"))
        print("  ", name)
    b.close()
print(f"cards in {OUT}")
