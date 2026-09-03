/* ============================================================================
   router.js — hash routing. Every view is deep-linkable and the back button
   is never broken (navigation is native history).
   ============================================================================ */

const routes = [];
let notFound = null;
let onNavigate = null;

/** define('/employer/jobs/:id', handler) */
export function define(pattern, handler) {
  const names = [];
  const regex = new RegExp(
    '^' + pattern.replace(/:[A-Za-z_]\w*/g, (m) => { names.push(m.slice(1)); return '([^/]+)'; }) + '$',
  );
  routes.push({ regex, names, handler });
}

export const setNotFound = (fn) => { notFound = fn; };
export const setNavigateHook = (fn) => { onNavigate = fn; };

export const path = () => (window.location.hash || '#/').slice(1).split('?')[0] || '/';

export function go(to, { replace = false } = {}) {
  const target = to.startsWith('#') ? to : `#${to}`;
  if (replace) window.location.replace(target);
  else window.location.hash = target;
}

export function resolve() {
  const current = path();
  for (const route of routes) {
    const match = current.match(route.regex);
    if (!match) continue;
    const params = {};
    route.names.forEach((name, i) => { params[name] = decodeURIComponent(match[i + 1]); });
    onNavigate?.(current);
    return route.handler(params);
  }
  onNavigate?.(current);
  return notFound?.();
}

export function start() {
  window.addEventListener('hashchange', () => {
    resolve();
    // Move focus to main on route change so screen readers announce the new view.
    document.getElementById('main')?.focus?.();
    window.scrollTo({ top: 0, behavior: 'instant' });
  });
  resolve();
}
