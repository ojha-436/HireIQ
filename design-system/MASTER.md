# HireIQ — Design System (MASTER)

> Global source of truth. Page-level deviations live in `design-system/pages/<page>.md` and
> **override** this file. If no page file exists, these rules apply exclusively.

---

## 1. Concept

**A broadcast control room for hiring decisions.**

The employer monitors a live session in which a deterministic rule engine fires in real
time — that is a production gallery, not a card grid. The candidate is in a high-stakes
conversation and must feel focused, not surveilled.

### Reversed: theme is the person's, not the role's

**This document previously specified two tonal registers — dark for the employer, light
for the candidate — chosen by which portal you were in. That was wrong and has been
undone.** It is recorded here rather than deleted, because the reasoning is instructive.

The argument was that a recruiter is *monitoring* and a candidate should not *feel
surveilled*. It did not survive contact:

- **No enterprise hiring product does this.** Greenhouse, Lever, Ashby and Workday are
  all single-theme. A reviewer who knows the category reads a split as inconsistency,
  not intent.
- **Light versus dark does not change whether someone feels watched.** Disclosure, copy
  and control do that, and those are handled properly elsewhere.
- **It doubled the QA surface for nothing.** The one contrast bug that actually shipped
  (`--text-3` at 4.08:1) was in the light register specifically.
- **It left the user no way out** — a recruiter in a bright office at 2pm was given dark
  mode and no toggle. That is a worse experience than any theming concept is worth.

**Now:** one palette, three states — `system` (default), `light`, `dark` — chosen by the
person, persisted, and resolved to an explicit `data-theme` before first paint so there
is no flash of the wrong theme. See `frontend/js/theme.js`.

### What IS role-driven

**Density.** A monitoring console legitimately wants more on screen than an application
form does, so the employer shell carries `.dense` and the spacing tokens respond. That
difference is real; the colour temperature was not.

| | Employer console | Candidate portal |
|---|---|---|
| Theme | the user's choice | the user's choice |
| Density | `.dense` — tighter rows, tighter cards | roomier, one task per view |
| Job | monitor · decide · audit | focus · speak · be heard |

### The one deliberate exception

**The interview room and its consent screen force dark** via `.force-dark`, and the
reason is the **video, not the role**: a bright page behind a webcam tile lights the
candidate's face and washes out the feed. It applies to whoever is in the room, which is
what makes it a defensible exception rather than a return to the split.

**The one memorable thing:** the live monitor — five persona tallies, a streaming
transcript, a Panel Memory column, and a trace rail printing rule IDs as they fire.

## 2. Typography

| Role | Family | Why |
|---|---|---|
| Display | **Instrument Serif** | Editorial gravity. An assessment is a document of record, not a dashboard widget |
| UI / Body | **Archivo** (variable) | Grotesk with real character; excellent at small sizes and dense tables; tabular figures |
| Machine | **IBM Plex Mono** | Rule IDs, turn refs, timestamps, scores, transcript speaker tags, code |

The serif/mono tension **is** the concept: human judgement rendered from machine evidence.
Never use a system font stack as the primary face. Never Inter, Roboto, Space Grotesk.

```
--font-display: 'Instrument Serif', 'Iowan Old Style', Georgia, serif;
--font-ui:      'Archivo', 'Helvetica Neue', Arial, sans-serif;
--font-mono:    'IBM Plex Mono', ui-monospace, 'SF Mono', Menlo, monospace;
```

### Scale (1.200 minor third, 16px base)
`11 · 12 · 13 · 14 · 16 · 19 · 23 · 28 · 34 · 41 · 56`

- Body min **16px**; table/meta text may go to 13px, **never below 12px**
- Line-height: body `1.6`, dense tables `1.45`, display `1.05`
- **All numerals in data contexts use `font-variant-numeric: tabular-nums`** (`number-tabular`)
- Display faces get `letter-spacing: -0.02em`; mono gets `-0.01em`; body keeps default tracking

---

## 3. Colour

Semantic tokens only in components. **No raw hex outside `tokens.css`** (`color-semantic`).

### Persona hues — identity, not state

| Persona | Token | Control | Calm |
|---|---|---|---|
| Technical | `--p-tech` | `#6E9BF2` | `#2F5FC4` |
| Product | `--p-product` | `#E0A343` | `#9A6712` |
| Hiring Manager | `--p-hm` | `#EC7396` | `#B23A5E` |
| Customer | `--p-customer` | `#43C2A3` | `#127A61` |
| Behavioural | `--p-behav` | `#B491E8` | `#6B44B0` |

**Rule: hue = who, luminance + tally glow = who is speaking.** The floor state is never
communicated by colour alone — the active tile also carries a filled tally dot, a raised border,
and an `aria-live` label (`color-not-only`).

### Semantic
`--danger #F0603A` · `--success #4FBF87` · `--warn #E0A343` · `--info #6E9BF2`

Every semantic colour ships with an icon or text label. Danger is reserved for reject/delete and is
spatially separated from primary actions (`destructive-emphasis`).

### Contrast contract
- Body text ≥ **4.5:1**, secondary ≥ **3:1**, non-text UI (borders, tally, chart marks) ≥ **3:1**
- Verified independently in both registers — never inferred from one

---

## 4. Space, Shape, Elevation

- **4px base rhythm.** Scale `4 8 12 16 20 24 32 40 56 72 96` (`spacing-scale`)
- **Radius stays small** — `4 / 6 / 10 / 999`. Enterprise instrument, not consumer pill
- **Hairlines over shadows.** Structure is expressed by 1px rules. Shadow appears only on genuinely
  floating layers: dropdown, modal, toast (`elevation-consistent`)
- Elevation scale: `0` flat → `1` hairline+tint → `2` dropdown → `3` modal
- z-index ladder: `0 base · 10 sticky · 20 dropdown · 40 drawer · 100 modal · 1000 toast`
- Max content width `1440px`; monitor view goes full-bleed with fixed rails

---

## 5. Motion

Tokens are global — every animation shares one rhythm (`motion-consistency`).

```
--dur-fast: 140ms   --dur: 200ms   --dur-slow: 320ms
--ease-out: cubic-bezier(.16,1,.3,1)    /* entering */
--ease-in:  cubic-bezier(.4,0,1,1)      /* exiting  */
```

- Micro-interactions **140–200ms**; exits ~65% of enter (`exit-faster-than-enter`)
- **Animate `transform` and `opacity` only.** Never width/height/top/left (`transform-performance`)
- Motion must express cause and effect. The floor handoff animates *because* the floor moved
- List/roster entrances stagger 40ms per item, capped at 6
- `prefers-reduced-motion: reduce` collapses all durations to `1ms` — mandatory, not optional
- Animations never block input; a transition in flight is interruptible

---

## 6. Components — house rules

| Component | Rule |
|---|---|
| Buttons | One primary CTA per view. Min height 40px (44px touch). Async → disabled + spinner, never a dead click |
| Inputs | **Visible label above**, always. Placeholder is an example, never the label. Helper text persists below |
| Errors | Below the field, with `role="alert"`. State cause **and** fix — never "Invalid input" |
| Tables | Sticky header, tabular figures, `aria-sort` on sortable columns, zebra via hairline not fill |
| Empty states | Icon + what this is + one action. Never a blank panel |
| Loading | Skeleton with reserved dimensions after 300ms. Never a bare spinner on a full page (`content-jumping`) |
| Modals | Focus trapped, `Esc` closes, scrim 55%, returns focus to trigger. Confirm before discarding unsaved input |
| Toasts | `aria-live="polite"`, never steals focus, auto-dismiss 4s, destructive actions offer Undo |
| Icons | **Inline SVG, Lucide geometry, 1.5px stroke, 20px grid.** No emoji as structural icons, ever |
| Charts | Legend visible, tooltip on interact, text summary for screen readers, `≥3:1` on data marks |

---

## 7. Layout & Responsive

Breakpoints `480 / 768 / 1024 / 1280 / 1536`. Mobile-first.

- Employer console: sidebar at `≥1024px`, top bar + drawer below (`adaptive-navigation`)
- Live monitor: 3 columns at `≥1280px` → 2 at `≥1024` (Panel Memory tabs behind trace) → stacked below
- Interview room: **desktop-first by design.** Below `768px` the candidate gets an explicit
  "join from a laptop for the best experience" state rather than a broken room — an honest
  constraint beats a degraded interview
- No horizontal scroll. Data tables scroll inside their own container, never the page
- `min-height: 100dvh`, never `100vh`

---

## 8. Accessibility floor (non-negotiable, CI-checked)

- [ ] Contrast 4.5:1 body / 3:1 secondary and non-text, **both registers**
- [ ] Visible focus ring on every interactive element — 2px accent + 2px offset. **Never `outline:none` without a replacement**
- [ ] Tab order matches visual order; skip-link to main
- [ ] Every icon-only control has `aria-label`; every input has a `<label for>`
- [ ] Sequential heading hierarchy, single `h1` per view
- [ ] Live regions: transcript `aria-live="polite"`, errors `role="alert"`
- [ ] Full keyboard path through the interview room, including end-interview
- [ ] `prefers-reduced-motion` honoured globally
- [ ] Touch targets ≥44×44px with ≥8px separation
- [ ] Colour never the sole carrier of meaning — floor, flags, scores all carry text or icon

---

## 9. Anti-patterns — explicitly banned

- Purple-gradient-on-white SaaS hero. Any decorative gradient mesh
- Inter / Roboto / system-ui as the primary face
- Emoji standing in for icons
- Card grids of equal-weight tiles with no hierarchy
- Drop shadows used as the primary structural device
- Pill-shaped buttons and 16px+ radii
- Placeholder-as-label
- Spinners where a skeleton belongs
- Colour-only status (a red dot with no label)
- Animating layout properties
- Fabricated data in an empty state — an empty chart says "no data yet", it does not invent a curve
- **Theming by role instead of by person.** Forcing a theme on someone because of which
  portal they are in is a designer's idea, not a user's need — and it removes their
  control. Density may vary by role; colour temperature may not. (See §1.)
