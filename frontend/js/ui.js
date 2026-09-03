/* ============================================================================
   ui.js — DOM primitives, icon set, and the shared component helpers.

   Everything renders through h(), which sets textContent for strings. User
   content (job descriptions, candidate names) never touches innerHTML.
   ============================================================================ */

/* -------------------------------------------------- icons: Lucide geometry
   One family, 1.5px stroke, 24px grid. No emoji anywhere in this product. */
const PATHS = {
  briefcase: '<rect width="20" height="14" x="2" y="7" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  chart: '<path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" x2="9" y1="12" y2="12"/>',
  plus: '<path d="M5 12h14"/><path d="M12 5v14"/>',
  chevronRight: '<path d="m9 18 6-6-6-6"/>',
  arrowRight: '<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',
  arrowLeft: '<path d="M19 12H5"/><path d="m12 19-7-7 7-7"/>',
  check: '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="m9 11 3 3L22 4"/>',
  alert: '<circle cx="12" cy="12" r="10"/><path d="M12 8v4"/><path d="M12 16h.01"/>',
  warning: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  pin: '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
  clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  building: '<path d="M3 21h18"/><path d="M5 21V7l8-4v18"/><path d="M19 21V11l-6-4"/><path d="M9 9v.01"/><path d="M9 12v.01"/><path d="M9 15v.01"/>',
  file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M16 13H8"/><path d="M16 17H8"/><path d="M10 9H8"/>',
  inbox: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11Z"/>',
  menu: '<path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h16"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  shield: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1Z"/><path d="m9 12 2 2 4-4"/>',
  mic: '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/>',
  activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  layers: '<path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m6.08 9.5-3.49 1.59a1 1 0 0 0 0 1.81l8.6 3.91a2 2 0 0 0 1.65 0l8.58-3.9a1 1 0 0 0 0-1.83L17.9 9.5"/>',
  pencil: '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497Z"/><path d="m15 5 4 4"/>',
  user: '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  eye: '<path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0"/><circle cx="12" cy="12" r="3"/>',
  eyeOff: '<path d="M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.8 10.8 0 0 1-1.444 2.49"/><path d="M14.084 14.158a3 3 0 0 1-4.242-4.242"/><path d="M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143"/><path d="m2 2 20 20"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  monitor: '<rect width="20" height="14" x="2" y="3" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/>',
  globe: '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
};

export function icon(name, size = 16) {
  const d = PATHS[name];
  if (!d) return '';
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"
    aria-hidden="true" focusable="false">${d}</svg>`;
}

/* ------------------------------------------------------------------ h() */
const SVG_PROPS = new Set(['html']);

export function h(tag, props = {}, children = []) {
  const el = document.createElement(tag);

  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;

    if (key === 'class') el.className = value;
    else if (key === 'text') el.textContent = String(value);
    else if (key === 'html') el.innerHTML = value;          // trusted markup only (icons)
    else if (key === 'dataset') Object.assign(el.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === 'style' && typeof value === 'object') Object.assign(el.style, value);
    else el.setAttribute(key, value === true ? '' : String(value));
  }

  const kids = Array.isArray(children) ? children : [children];
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

export const frag = (...nodes) => {
  const f = document.createDocumentFragment();
  nodes.flat().filter(Boolean).forEach((n) => f.append(n));
  return f;
};

export const clear = (el) => {
  while (el.firstChild) el.removeChild(el.firstChild);
  // Element.append(null) inserts the literal text "null". Wrapping append here means
  // conditional children (`cond ? node : null`) are safe everywhere, which is how
  // every view in this codebase is written.
  if (!el._safeAppend) {
    const native = el.append.bind(el);
    el.append = (...nodes) => native(...nodes.flat(Infinity).filter(
      (n) => n !== null && n !== undefined && n !== false));
    el._safeAppend = true;
  }
  return el;
};

/* ---------------------------------------------------------------- toasts */
export function toast(message, kind = 'ok') {
  const host = document.getElementById('toasts');
  if (!host) return;
  const node = h('div', {
    class: `toast toast-${kind}`,
    role: 'status',
  }, [
    h('span', { html: icon(kind === 'err' ? 'alert' : 'check', 16) }),
    h('span', { class: 'grow', text: message }),
  ]);
  host.append(node);
  setTimeout(() => {
    node.classList.add('out');
    node.addEventListener('animationend', () => node.remove(), { once: true });
  }, 4000);
}

/* ----------------------------------------------------------------- modal
   Focus-trapped, Esc closes, returns focus to the trigger. */
export function modal({ title, body, confirmLabel = 'Confirm', cancelLabel = 'Cancel', danger = false, onConfirm }) {
  const opener = document.activeElement;

  const confirmBtn = h('button', {
    class: `btn ${danger ? 'btn-danger' : 'btn-primary'}`,
    type: 'button',
    text: confirmLabel,
    onClick: async () => {
      confirmBtn.disabled = true;
      try { await onConfirm?.(); close(); }
      finally { confirmBtn.disabled = false; }
    },
  });

  const panel = h('div', {
    class: 'modal', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'modal-title',
  }, [
    h('div', { class: 'card-pad col gap4' }, [
      h('h2', { id: 'modal-title', class: 'display', style: { fontSize: 'var(--fs-23)' }, text: title }),
      typeof body === 'string' ? h('p', { class: 't2 fs13', text: body }) : body,
      h('div', { class: 'row', style: { justifyContent: 'flex-end', marginTop: 'var(--s2)' } }, [
        h('button', { class: 'btn', type: 'button', text: cancelLabel, onClick: () => close() }),
        confirmBtn,
      ]),
    ]),
  ]);

  const scrim = h('div', { class: 'modal-scrim' }, [panel]);
  scrim.addEventListener('mousedown', (e) => { if (e.target === scrim) close(); });

  function onKey(e) {
    if (e.key === 'Escape') { close(); return; }
    if (e.key !== 'Tab') return;
    const focusables = panel.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  function close() {
    document.removeEventListener('keydown', onKey);
    scrim.remove();
    opener?.focus?.();
  }

  document.addEventListener('keydown', onKey);
  document.body.append(scrim);
  panel.querySelector('button')?.focus();
  return { close };
}

/* ------------------------------------------------------ empty & skeleton */
export function empty({ iconName = 'inbox', title, body, action }) {
  return h('div', { class: 'empty' }, [
    h('div', { class: 'empty-icon', html: icon(iconName, 22) }),
    h('h3', { text: title }),
    body && h('p', { text: body }),
    action || null,
  ]);
}

export function skeletonRows(count = 3, height = 76) {
  return h('div', { class: 'col gap3', 'aria-hidden': 'true' },
    Array.from({ length: count }, () =>
      h('div', { class: 'skel', style: { height: `${height}px`, borderRadius: 'var(--r-lg)' } })));
}

/* --------------------------------------------------------------- helpers */
export const initials = (name = '') =>
  name.trim().split(/\s+/).slice(0, 2).map((w) => w[0] || '').join('').toUpperCase() || '?';

export function fmtDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

export function relTime(iso) {
  if (!iso) return '—';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return days < 30 ? `${days}d ago` : fmtDate(iso);
}

export function debounce(fn, ms = 280) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

export const PERSONA_LABEL = {
  tech: 'Technical Interviewer',
  product: 'Product Manager',
  hiring_manager: 'Hiring Manager',
  customer: 'Customer',
  behavioural: 'Behavioural',
};

export const personaChip = (key) =>
  h('span', { class: `persona p-${key}`, text: PERSONA_LABEL[key] || key });

export function difficultyGauge(level) {
  return h('span', { class: 'diff' }, [
    h('span', { class: 'diff-bars', 'aria-hidden': 'true' },
      [1, 2, 3, 4, 5].map((n) => h('i', { class: n <= level ? 'on' : '' }))),
    h('span', { class: 'fs12 t2 mono', text: `L${level}` }),
  ]);
}


/* ------------------------------------------------------------ theme control
   Three states, cycled by one button. The label always names the CURRENT setting, not
   the next one — a control that says "Dark" while showing a light page is a riddle. */
export function themeToggle() {
  const btn = h('button', { class: 'icon-btn theme-toggle', type: 'button' });

  const render = async () => {
    const T = await import('./theme.js');
    const m = T.mode();
    const ic = { system: 'monitor', light: 'sun', dark: 'moon' }[m];
    btn.innerHTML = icon(ic, 17);
    btn.setAttribute('aria-label', `Theme: ${m}. Click to change.`);
    btn.title = `Theme: ${m}`;
  };

  btn.onclick = async () => {
    const T = await import('./theme.js');
    T.cycle();
    render();
  };
  render();
  return btn;
}
