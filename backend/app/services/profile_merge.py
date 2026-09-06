"""Folding a parsed résumé into a candidate profile, reversibly.

The first version of this merge was purely additive: a field already holding a value
was never touched, and skills were unioned. That made a *replacement* résumé a no-op —
the candidate uploaded a new CV, saw their old headline, old summary and old employers
unchanged, and reasonably concluded the feature was broken. Worse, the two résumés'
skills accumulated, so the match score against a job could only ever rise, and the
profile drifted into a blend of two different careers that belonged to neither.

The fix is provenance. Every field this module writes is recorded as résumé-derived, so
the next upload knows exactly which values it owns and may replace, and which the
candidate typed by hand and it must leave alone. Nothing the candidate wrote is ever
overwritten; everything the previous résumé contributed is.

Provenance lives inside `profile_sections_json` under a reserved key rather than in a
new column, so this needs no migration and no change to the profile contract. Readers
that do not know about it (the profile UI, the interview grounding assembler) simply
ignore the key.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

#: Reserved key inside profile_sections_json. Underscore-prefixed so it cannot collide
#: with a profile section, and stripped from anything the candidate is shown.
PROVENANCE_KEY = "_resume_derived"

#: The list-shaped sections, and the fields that identify one entry within them.
LIST_SECTIONS: Dict[str, Tuple[str, ...]] = {
    "experience": ("title", "org"),
    "education": ("degree", "org"),
    "projects": ("title",),
}
SCALAR_SECTIONS = ("headline", "summary")

CAPS = {"experience": 8, "education": 5, "projects": 6}


def entry_key(item: Dict[str, Any], fields: Tuple[str, ...]) -> str:
    """A stable identity for one experience/education/project entry."""
    return "|".join(str(item.get(f, "") or "").strip().lower() for f in fields)


def _blank_provenance() -> Dict[str, Any]:
    return {
        "headline": False,
        "summary": False,
        "skills": [],
        "experience": [],
        "education": [],
        "projects": [],
        "filename": "",
    }


def read_provenance(sections: Dict[str, Any]) -> Dict[str, Any]:
    prov = _blank_provenance()
    stored = (sections or {}).get(PROVENANCE_KEY)
    if isinstance(stored, dict):
        for k, default in prov.items():
            v = stored.get(k, default)
            if isinstance(default, list) and isinstance(v, list):
                prov[k] = [str(x) for x in v]
            elif isinstance(default, bool):
                prov[k] = bool(v)
            elif isinstance(default, str):
                prov[k] = str(v or "")
    return prov


def public_sections(sections: Dict[str, Any]) -> Dict[str, Any]:
    """The profile as the candidate's own data — provenance bookkeeping removed."""
    return {k: v for k, v in (sections or {}).items() if k != PROVENANCE_KEY}


def merge_resume(sections: Dict[str, Any], parsed_profile: Dict[str, Any],
                 parsed_skills: List[str], filename: str = "",
                 prior_resume_skills: List[str] | None = None,
                 ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Fold a freshly parsed résumé into `sections`.

    Returns `(new_sections, report)`. The report says what actually changed, so the API
    can tell the candidate rather than silently succeeding — "nothing appeared to
    happen" was the original complaint, and a truthful summary is the cure.

    Rules, in one sentence each:
      * A field the candidate typed by hand is never touched.
      * A field the PREVIOUS résumé supplied is replaced by this one.
      * A field that is empty is filled.
    """
    sections = dict(sections or {})
    prev = read_provenance(sections)
    if PROVENANCE_KEY not in (sections or {}) and prior_resume_skills:
        # A profile built before provenance existed. `resume_meta_json` recorded exactly
        # which skills the last résumé contributed, so those can be reclaimed safely and
        # the match score corrects itself on this upload. Headline, summary and the list
        # sections are NOT reclaimed: there is no record of who wrote them, and guessing
        # wrong would delete something the candidate typed. They stay manual, and the
        # report says so, which is what lets the UI explain why they did not change.
        prev["skills"] = [str(x) for x in prior_resume_skills]
    prov = _blank_provenance()
    prov["filename"] = filename
    report: Dict[str, Any] = {
        "replaced": [], "filled": [], "kept_manual": [],
        "added": {"skills": 0, "experience": 0, "education": 0, "projects": 0},
        "removed": {"skills": 0, "experience": 0, "education": 0, "projects": 0},
        "skills_unreadable": False,
    }

    # -- headline / summary ------------------------------------------------
    for field in SCALAR_SECTIONS:
        incoming = str((parsed_profile or {}).get(field) or "").strip()
        current = str(sections.get(field) or "").strip()
        was_ours = bool(prev.get(field))
        if not incoming:
            # Nothing to say. Keep whatever is there, but if we owned it and this
            # résumé has no opinion, we still own it only if it survives unchanged.
            prov[field] = was_ours and bool(current)
            continue
        if not current:
            sections[field] = incoming
            prov[field] = True
            report["filled"].append(field)
        elif was_ours:
            if current != incoming:
                report["replaced"].append(field)
            sections[field] = incoming
            prov[field] = True
        else:
            report["kept_manual"].append(field)

    # -- skills ------------------------------------------------------------
    # Manual skills are whatever is on the profile that the last résumé did not put
    # there. Those stay; the previous résumé's skills go; this résumé's skills arrive.
    current_skills = [str(s).strip() for s in (sections.get("skills") or []) if str(s).strip()]
    prev_owned = {s.lower() for s in prev.get("skills", [])}
    manual_skills = [s for s in current_skills if s.lower() not in prev_owned]
    incoming_skills = [str(s).strip() for s in (parsed_skills or []) if str(s).strip()]

    if not incoming_skills and prev_owned:
        # Extraction is a fixed lexicon, so a CV from a field it does not cover yields
        # nothing. Treating that as "this person has no skills" would wipe the profile
        # and blank every match score on the strength of a parser gap — a silent data
        # loss the candidate never asked for. Keep what is there, say so, and let them
        # correct it by hand.
        report["skills_unreadable"] = True
        prov["skills"] = list(prev.get("skills", []))
        sections["skills"] = current_skills
        keep_previous_skills = True
    else:
        keep_previous_skills = False

    if not keep_previous_skills:
        merged_skills: List[str] = []
        seen: set = set()
        for s in [*manual_skills, *incoming_skills]:
            if s.lower() in seen:
                continue
            seen.add(s.lower())
            merged_skills.append(s)
        manual_lower = {s.lower() for s in manual_skills}
        prov["skills"] = [s for s in merged_skills if s.lower() not in manual_lower]
        report["removed"]["skills"] = len([s for s in current_skills if s.lower() not in
                                           {m.lower() for m in merged_skills}])
        report["added"]["skills"] = len([s for s in merged_skills if s.lower() not in
                                         {c.lower() for c in current_skills}])
        sections["skills"] = merged_skills

    # -- experience / education / projects ---------------------------------
    for name, fields in LIST_SECTIONS.items():
        current_items = [i for i in (sections.get(name) or []) if isinstance(i, dict)]
        owned = set(prev.get(name, []))
        kept = [i for i in current_items if entry_key(i, fields) not in owned]
        before_keys = {entry_key(i, fields) for i in current_items}

        out = list(kept)
        seen_keys = {entry_key(i, fields) for i in kept}
        new_keys: List[str] = []
        for item in ((parsed_profile or {}).get(name) or []):
            if not isinstance(item, dict):
                continue
            key = entry_key(item, fields)
            if not key.strip("|") or key in seen_keys:
                continue
            out.append(item)
            seen_keys.add(key)
            new_keys.append(key)
        sections[name] = out[: CAPS.get(name, 8)]
        # Only entries that actually survived the cap are still ours to replace later.
        surviving = {entry_key(i, fields) for i in sections[name]}
        prov[name] = [k for k in new_keys if k in surviving]
        # Counts describe the NET change, so re-uploading the same file truthfully
        # reports nothing added rather than one removed and one added back.
        report["added"][name] = len(surviving - before_keys)
        report["removed"][name] = len(before_keys - surviving)

    sections[PROVENANCE_KEY] = prov
    return sections, report


def apply_manual_edit(old_sections: Dict[str, Any],
                      incoming: Dict[str, Any]) -> Dict[str, Any]:
    """Save a hand-edited profile, keeping provenance honest.

    The profile UI PATCHes the whole sections object and knows nothing about
    provenance, so without this the reserved key would be dropped on every save and the
    next résumé upload would think it owned nothing. Anything the candidate changed
    here stops being résumé-derived: they have made it theirs, and a later upload must
    not overwrite it.
    """
    old = dict(old_sections or {})
    new = public_sections(incoming)
    prev = read_provenance(old)
    prov = _blank_provenance()
    prov["filename"] = prev.get("filename", "")

    for field in SCALAR_SECTIONS:
        unchanged = str(new.get(field) or "").strip() == str(old.get(field) or "").strip()
        prov[field] = bool(prev.get(field)) and unchanged

    kept_skills = {str(s).strip().lower() for s in (new.get("skills") or [])}
    prov["skills"] = [s for s in prev.get("skills", []) if s.lower() in kept_skills]

    for name, fields in LIST_SECTIONS.items():
        present = {entry_key(i, fields) for i in (new.get(name) or []) if isinstance(i, dict)}
        prov[name] = [k for k in prev.get(name, []) if k in present]

    new[PROVENANCE_KEY] = prov
    return new
