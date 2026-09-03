"""Static guards for the two responsive bugs that took a browser to find.

A full Playwright pass is the real check (see plan.md §8); these catch the exact
regressions cheaply, in the suite, with the reason attached.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
CSS_DIR = ROOT / "frontend" / "css"
CSS = "\n".join(p.read_text() for p in sorted(CSS_DIR.glob("*.css")))
VIEWS = sorted((ROOT / "frontend" / "js" / "views").glob("*.js"))


def _media_bodies(query: str) -> str:
    """Every rule under `@media (query)`, concatenated.

    There is more than one block per breakpoint, so a regex that stops at the first
    one silently checks the wrong rules — which is how these guards first passed
    against `.auth` instead of `.split-main`.
    """
    out = []
    needle = "@media (" + query + ")"
    idx = 0
    while (start := CSS.find(needle, idx)) != -1:
        brace = CSS.find("{", start)
        depth, i = 0, brace
        while i < len(CSS):
            if CSS[i] == "{":
                depth += 1
            elif CSS[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        out.append(CSS[brace + 1:i])
        idx = i
    return "\n".join(out)


def _decl(selector: str, prop: str) -> str | None:
    """The value of `prop` in the LAST rule matching `selector` — i.e. the one that wins."""
    hits = re.findall(
        re.escape(selector) + r"\s*\{([^}]*)\}", CSS, re.S)
    for body in reversed(hits):
        m = re.search(prop + r"\s*:\s*([^;]+);", body)
        if m:
            return m.group(1).strip()
    return None


# =================================================== the grid min-width bug
def test_grid_children_can_be_narrower_than_their_content():
    """`minmax(0,1fr)` is not enough. A grid CHILD without min-width:0 is sized by its
    content, so a wide table inflated the track to 518px inside a 350px container."""
    assert _decl(".split-main > *", "min-width") == "0"


def test_two_column_layouts_are_not_inline_styled():
    """Inline `grid-template-columns` cannot be overridden by a media query, which is
    why three candidate views overflowed a 390px viewport by ~90px."""
    offenders = [
        p.name for p in VIEWS
        if re.search(r"gridTemplateColumns:\s*\'minmax\(0,\s*[\d.]+fr\)\s*minmax\(\d", p.read_text())
    ]
    assert offenders == [], f"inline two-column grids cannot collapse: {offenders}"


def test_two_column_layouts_collapse_on_small_screens():
    body = _media_bodies("max-width: 900px")
    assert body, "no 900px breakpoint at all"
    assert ".split-main" in body
    assert "1fr" in body


def test_sticky_asides_release_on_mobile():
    """A sticky sidebar on a phone eats the viewport it is meant to help with."""
    body = _media_bodies("max-width: 900px")
    assert ".aside-sticky" in body
    assert "position: static" in body


# ======================================================= the nav overflow bug
def test_candidate_nav_can_scroll_on_a_phone():
    """Brand + 4 nav items + avatar + sign-out measured 479px in a 390px bar, and
    nothing clipped it, so the whole document scrolled sideways."""
    body = _media_bodies("max-width: 700px")
    assert ".portal-nav" in body, "no narrow-screen rule for the candidate nav"
    assert "overflow-x: auto" in body


# ============================================================ standing rules
def test_data_tables_scroll_in_their_own_container():
    """Design system rule: wide content scrolls inside its container, never the page."""
    assert _decl(".table-wrap", "overflow-x") == "auto"
    assert _decl(".table-wrap", "max-width") == "100%"


def test_touch_targets_meet_the_floor():
    for selector, prop, floor in (
        (".chip-x", "height", 24),
        (".persona-toggle", "min-height", 34),
        (".check", "min-height", 36),
    ):
        value = _decl(selector, prop)
        assert value, f"{selector} has no {prop}"
        assert int(value.rstrip("px")) >= floor, f"{selector} is {value}, floor is {floor}px"


def test_each_touch_target_rule_is_declared_once():
    """Appending an override instead of editing the rule is how these guards ended up
    reading a stale 16px value while the browser rendered 24px."""
    for selector in (".chip-x {", ".persona-toggle {", ".check {", ".table-wrap {"):
        assert CSS.count(selector) == 1, f"{selector} is declared more than once"


# ============================================================ deploy guards
def test_the_spa_directory_resolves_in_both_layouts():
    """`backend/app/main.py` and `/app/app/main.py` put the frontend in different
    places. Hardcoding one 404s the whole SPA in the other — which is exactly what
    would have shipped."""
    from app.main import _find_frontend

    found = _find_frontend()
    assert found is not None, "the SPA directory was not found in this layout"
    assert (found / "index.html").is_file()


def test_the_image_ships_the_scenario_bank():
    """An empty `scenarios` table means R7 finds nothing and PS11 requirement #6
    silently does not happen in production."""
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "backend/scripts" in dockerfile, "scripts/ is not copied into the image"
    assert "seed_scenarios.py" in dockerfile, "the scenario bank is never seeded"


def test_the_image_does_not_bake_in_secrets():
    dockerfile = (ROOT / "Dockerfile").read_text()
    for leak in ("GEMINI_API_KEY=", "AGORA_CUSTOMER_SECRET=", "AGORA_APP_CERTIFICATE=",
                 "JWT_SECRET="):
        assert leak not in dockerfile, f"{leak} is set in the Dockerfile"
    assert ".env" not in dockerfile, "the .env file must never be copied into an image"


# ==================================================== theme control reachability
def test_the_theme_control_exists_before_sign_in():
    """The landing and auth screens are the FIRST thing anyone sees.

    Shipping the toggle only inside the two authed shells meant a new visitor could not
    set their theme until after signing in — and the theme was, until recently, forced
    on them by which portal they happened to open.
    """
    auth = (ROOT / "frontend" / "js" / "views" / "auth.js").read_text()
    assert "themeToggle" in auth, "no theme control on any pre-auth screen"
    assert "themeCorner" in auth
    # Landing and the shared auth screen wrapper must both mount it.
    assert auth.count("themeCorner()") >= 2, (
        "the corner slot is not used by both the landing page and the auth screens")


def test_the_theme_corner_is_positioned_consistently():
    """Same place on all five pre-auth screens is what makes it findable."""
    assert re.search(r"\.theme-corner\s*\{[^}]*position:\s*fixed", CSS, re.S)
    assert re.search(r"\.theme-corner\s*\{[^}]*top:", CSS, re.S)


def test_theme_is_applied_before_first_paint():
    """Deferring it to the module boot flashes the wrong theme on every load."""
    html = (ROOT / "frontend" / "index.html").read_text()
    head = html[:html.index("</head>")]
    assert "hireiq.theme" in head, "the theme is not resolved in <head>"
    assert "prefers-color-scheme" in head
    assert "dataset.theme" in head


def test_theme_is_not_keyed_to_the_portal():
    """Reversed decision (design-system/MASTER.md §1): theme belongs to the person.
    `.reg-control` / `.reg-calm` must no longer carry a palette."""
    tokens = (CSS_DIR / "tokens.css").read_text()
    for legacy in (".reg-control", ".reg-calm"):
        idx = tokens.index(legacy)
        body = tokens[idx:tokens.index("}", idx)]
        assert "--bg" not in body, f"{legacy} still sets a palette"


def test_the_interview_room_still_forces_dark():
    """The one exception, and it is about the VIDEO not the role: a bright page behind a
    webcam tile lights the candidate's face."""
    room = (ROOT / "frontend" / "js" / "interview" / "room.js").read_text()
    assert "force-dark" in room
    assert re.search(r"\.force-dark[^{]*\{[^}]*--bg", CSS, re.S), (
        ".force-dark does not actually apply the dark palette")
