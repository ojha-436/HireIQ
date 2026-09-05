/* ============================================================================
   api.js — the single place that talks to the backend.

   Audience isolation is enforced by the server; this client simply never
   mixes the two tokens. A 401 clears that role's session and bounces to login.
   ============================================================================ */

import { Store } from './store.js';

export const API_BASE = (window.HIREIQ_API_BASE || '').replace(/\/$/, '');

export class ApiError extends Error {
  constructor(message, status, fields = {}) {
    super(message);
    this.status = status;
    this.fields = fields;
  }
}

/** FastAPI returns `detail` as either a string or a list of validation errors. */
function parseError(status, payload) {
  const detail = payload?.detail;

  if (typeof detail === 'string') return new ApiError(detail, status);

  if (Array.isArray(detail)) {
    const fields = {};
    for (const item of detail) {
      const name = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : 'form';
      fields[name] = humanise(name, item.msg);
    }
    const first = Object.values(fields)[0];
    return new ApiError(first || 'Please check the highlighted fields', status, fields);
  }

  if (status === 0) return new ApiError('Cannot reach the API. Is the backend running?', 0);
  return new ApiError(`Request failed (${status})`, status);
}

/** Error text must say the cause AND the fix — never bare "Invalid input". */
function humanise(field, msg = '') {
  const m = msg.toLowerCase();
  if (m.includes('email')) return 'Enter a valid email address, like you@company.com';
  if (m.includes('at least 8')) return 'Use at least 8 characters';
  if (m.includes('at least 2')) return 'This needs at least 2 characters';
  if (m.includes('field required')) return 'This field is required';
  return msg || 'Please check this field';
}

async function request(method, path, { role, body, query } = {}) {
  const url = new URL(API_BASE + path, window.location.origin);
  for (const [k, v] of Object.entries(query || {})) {
    if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v);
  }

  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  const token = role ? Store.token(role) : null;
  if (token) headers.Authorization = `Bearer ${token}`;

  let res;
  try {
    res = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw parseError(0, null);
  }

  if (res.status === 204) return null;

  let payload = null;
  try { payload = await res.json(); } catch { /* empty body */ }

  if (!res.ok) {
    if (res.status === 401 && role) {
      Store.signOut(role);
      window.location.hash = `#/${role}/login`;
    }
    throw parseError(res.status, payload);
  }
  return payload;
}

const get = (path, opts) => request('GET', path, opts);
const post = (path, opts) => request('POST', path, opts);
const patch = (path, opts) => request('PATCH', path, opts);
const put = (path, opts) => request('PUT', path, opts);

export const Api = {
  health: () => get('/api/health'),

  employer: {
    register: (body) => post('/api/employer/auth/register', { body }),
    login: (body) => post('/api/employer/auth/login', { body }),
    me: () => get('/api/employer/auth/me', { role: 'employer' }),

    listJobs: () => get('/api/employer/jobs/', { role: 'employer' }),
    getJob: (id) => get(`/api/employer/jobs/${id}`, { role: 'employer' }),
    createJob: (body) => post('/api/employer/jobs/', { role: 'employer', body }),
    updateJob: (id, body) => patch(`/api/employer/jobs/${id}`, { role: 'employer', body }),
    publishJob: (id) => post(`/api/employer/jobs/${id}/publish`, { role: 'employer' }),
    pauseJob: (id) => post(`/api/employer/jobs/${id}/pause`, { role: 'employer' }),
    closeJob: (id) => post(`/api/employer/jobs/${id}/close`, { role: 'employer' }),
    replacePipeline: (id, stages) => put(`/api/employer/jobs/${id}/pipeline`, { role: 'employer', body: { stages } }),
    applications: (id) => get(`/api/employer/jobs/${id}/applications`, { role: 'employer' }),
    startInterview: (applicationId) =>
      post(`/api/employer/applications/${applicationId}/start-interview`, { role: 'employer' }),
    panelMemory: (sessionId) =>
      get(`/api/employer/sessions/${sessionId}/panel-memory`, { role: 'employer' }),
    assessment: (applicationId) =>
      get(`/api/employer/applications/${applicationId}/assessment`, { role: 'employer' }),
    advance: (applicationId, body) =>
      post(`/api/employer/applications/${applicationId}/advance`, { role: 'employer', body }),
    reject: (applicationId, body) =>
      post(`/api/employer/applications/${applicationId}/reject`, { role: 'employer', body }),
    releaseFeedback: (applicationId) =>
      post(`/api/employer/applications/${applicationId}/release-feedback`, { role: 'employer' }),

    generateDescription: (body) =>
      post('/api/employer/jobs/generate-description', { role: 'employer', body }),
    changePassword: (body) => patch('/api/employer/auth/me/password', { role: 'employer', body }),
  },

  candidate: {
    register: (body) => post('/api/candidate/auth/register', { body }),
    login: (body) => post('/api/candidate/auth/login', { body }),
    me: () => get('/api/candidate/auth/me', { role: 'candidate' }),
    updateMe: (body) => patch('/api/candidate/auth/me', { role: 'candidate', body }),

    browseJobs: (query) => get('/api/candidate/jobs', { role: 'candidate', query }),
    uploadResume: async (file) => {
      const form = new FormData();
      form.append('file', file);
      const res = await fetch(`${API_BASE}/api/candidate/auth/me/resume`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${Store.token('candidate')}` },
        body: form,   // no Content-Type: the browser must set the multipart boundary
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) throw new ApiError(payload.detail || 'Upload failed', res.status);
      return payload;
    },
    jobDetail: (id) => get(`/api/candidate/jobs/${id}`, { role: 'candidate' }),
    apply: (id) => post(`/api/candidate/apply/${id}`, { role: 'candidate' }),
    myApplications: () => get('/api/candidate/me/applications', { role: 'candidate' }),
    application: (id) => get(`/api/candidate/me/applications/${id}`, { role: 'candidate' }),

    dashboard: () => get('/api/candidate/dashboard', { role: 'candidate' }),

    practice: {
      start: (body) => post('/api/candidate/practice/start', { role: 'candidate', body }),
      listSessions: () => get('/api/candidate/practice/sessions', { role: 'candidate' }),
      report: (sessionId) =>
        get(`/api/candidate/practice/sessions/${sessionId}/report`, { role: 'candidate' }),
      progress: () => get('/api/candidate/practice/progress', { role: 'candidate' }),
    },

    changePassword: (body) => patch('/api/candidate/auth/me/password', { role: 'candidate', body }),
  },

  admin: {
    login: (body) => post('/api/admin/auth/login', { body }),
    me: () => get('/api/admin/auth/me', { role: 'admin' }),
    changePassword: (body) => patch('/api/admin/auth/me/password', { role: 'admin', body }),

    listEmployers: () => get('/api/admin/employers', { role: 'admin' }),
    toggleEmployer: (id) => post(`/api/admin/employers/${id}/toggle-active`, { role: 'admin' }),
    listCandidates: () => get('/api/admin/candidates', { role: 'admin' }),
    toggleCandidate: (id) => post(`/api/admin/candidates/${id}/toggle-active`, { role: 'admin' }),

    health: () => get('/api/admin/health', { role: 'admin' }),
    kpis: () => get('/api/admin/kpis', { role: 'admin' }),
  },
};
