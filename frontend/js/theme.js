/* ============================================================================
   theme.js — one theme, chosen by the person using the product.

   Three states, which is what people actually expect:
     system (default) · light · dark

   "system" is resolved here to an explicit `data-theme` rather than left to a CSS
   media query, so each palette is declared exactly once in tokens.css and there is a
   single source of truth for what is currently applied.

   The interview room is the one exception, and the reason is the video rather than the
   role: a bright page behind a webcam tile lights the candidate's face and washes out
   the feed. It opts in with `.force-dark` and applies to whoever is in the room.
   ============================================================================ */

const KEY = 'hireiq.theme';
const MODES = ['system', 'light', 'dark'];

const query = window.matchMedia('(prefers-color-scheme: dark)');
const listeners = new Set();

export function mode() {
  try {
    const stored = localStorage.getItem(KEY);
    return MODES.includes(stored) ? stored : 'system';
  } catch {
    return 'system';           // private mode, or storage blocked
  }
}

/** What is actually on screen right now: 'light' | 'dark'. */
export function resolved() {
  const m = mode();
  return m === 'system' ? (query.matches ? 'dark' : 'light') : m;
}

function paint() {
  const theme = resolved();
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  listeners.forEach((fn) => fn(mode(), theme));
}

export function setMode(next) {
  if (!MODES.includes(next)) return;
  try { localStorage.setItem(KEY, next); } catch { /* ignore */ }
  paint();
}

/** Advance system → light → dark → system. */
export function cycle() {
  setMode(MODES[(MODES.indexOf(mode()) + 1) % MODES.length]);
  return mode();
}

export function onChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// Follow the OS while the user is on "system" — including a change made mid-session.
query.addEventListener('change', () => { if (mode() === 'system') paint(); });

export function init() { paint(); }

init();
