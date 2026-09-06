
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
import pathlib, sys

from playwright.sync_api import sync_playwright
BASE = "https://hireiq-233250747481.asia-south1.run.app"
OUT = pathlib.Path(str(WORKDIR / "video"))
with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--hide-scrollbars"])
    c = b.new_context(viewport={"width":1600,"height":900},
                      record_video_dir=str(OUT/"s6"),
                      record_video_size={"width":1600,"height":900})
    p = c.new_page()
    p.goto(f"{BASE}/#/admin/login", wait_until="networkidle"); p.wait_for_timeout(1500)
    p.fill("#a-user","admin"); p.wait_for_timeout(400)
    p.fill("#a-pass","admin@123"); p.wait_for_timeout(700)
    p.click('button[type="submit"]'); p.wait_for_timeout(4000)
    p.mouse.wheel(0,300); p.wait_for_timeout(2500)
    p.goto(f"{BASE}/#/admin/health", wait_until="networkidle"); p.wait_for_timeout(3500)
    p.close(); c.close(); b.close()
src = next((OUT/"s6").glob("*.webm")); dst = OUT/"s6.webm"
if dst.exists(): dst.unlink()
src.rename(dst); print("saved", dst.name, dst.stat().st_size//1024, "KB")
