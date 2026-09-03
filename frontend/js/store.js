/* ============================================================================
   store.js — session state. Two audiences are stored under two keys so an
   employer and a candidate can be signed in side by side in the same browser
   (which is exactly what the demo needs).
   ============================================================================ */

const KEY = 'hireiq.session.v1';

function read() {
  try { return JSON.parse(localStorage.getItem(KEY) || '{}'); }
  catch { return {}; }
}

function write(data) {
  try { localStorage.setItem(KEY, JSON.stringify(data)); } catch { /* private mode */ }
}

export const Store = {
  /** @param {'employer'|'candidate'} role */
  token(role) { return read()[role]?.token || null; },
  profile(role) { return read()[role]?.profile || null; },
  isAuthed(role) { return Boolean(this.token(role)); },

  signIn(role, token) {
    const data = read();
    data[role] = { ...(data[role] || {}), token };
    write(data);
  },

  setProfile(role, profile) {
    const data = read();
    data[role] = { ...(data[role] || {}), profile };
    write(data);
  },

  signOut(role) {
    const data = read();
    delete data[role];
    write(data);
  },
};
