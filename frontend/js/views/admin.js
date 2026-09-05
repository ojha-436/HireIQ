/* ============================================================================
   views/admin.js — the platform-operator portal: login, shell, employers,
   candidates, health, KPIs, and the admin's own password change.

   A third audience, same isolation pattern as employer/candidate (store.js and
   api.js are already role-agnostic — see security.py for the server side: a
   candidate or employer token is structurally the wrong signature here).
   ============================================================================ */

import { Api } from '../api.js';
import { go } from '../router.js';
import { Store } from '../store.js';
import {
  clear, empty, fmtDate, h, icon, passwordChangeCard, relTime, skeletonRows, themeToggle, toast,
} from '../ui.js';

const pageHead = (title, sub) => h('header', { class: 'page-head' }, [
  h('h1', { class: 'page-title display', text: title }),
  sub ? h('p', { class: 'page-sub', text: sub }) : null,
]);

/* ------------------------------------------------------------------- login */
export function adminLogin() {
  const username = h('input', { class: 'input', id: 'a-user', autocomplete: 'username', required: true });
  const password = h('input', { class: 'input', id: 'a-pass', type: 'password', autocomplete: 'current-password', required: true });
  const err = h('p', { class: 'err', role: 'alert', hidden: true });
  const btn = h('button', { class: 'btn btn-primary btn-lg btn-block', type: 'submit', text: 'Sign in' });

  const form = h('form', { class: 'auth-form', novalidate: true, onSubmit: (e) => {
    e.preventDefault();
    err.hidden = true;
    btn.disabled = true;
    btn.replaceChildren(h('span', { class: 'spin' }), 'Signing in…');
    Api.admin.login({ username: username.value.trim(), password: password.value })
      .then(async (res) => {
        Store.signIn('admin', res.token);
        Store.setProfile('admin', await Api.admin.me());
        go('/admin');
      })
      .catch((error) => {
        err.hidden = false;
        err.textContent = error.message;
        btn.disabled = false;
        btn.replaceChildren('Sign in');
      });
  } }, [
    h('span', { class: 'ai-badge' }, [h('span', { html: icon('shield', 12) }), 'Platform admin']),
    h('h1', { class: 'display', style: { marginTop: 'var(--s4)' }, text: 'Admin sign in' }),
    h('p', { class: 'auth-switch', style: { marginBottom: 'var(--s8)' }, text: 'Restricted to platform operators.' }),
    h('div', { class: 'field' }, [h('label', { class: 'label', for: 'a-user', text: 'Username' }), username]),
    h('div', { class: 'field' }, [h('label', { class: 'label', for: 'a-pass', text: 'Password' }), password]),
    err,
    h('div', { style: { marginTop: 'var(--s6)' } }, [btn]),
  ]);

  return h('div', { class: 'auth-screen', style: { display: 'grid', placeItems: 'center', minHeight: '100vh' } }, [
    h('div', { class: 'card card-pad', style: { maxWidth: '380px', width: '100%' } }, [form]),
  ]);
}

/* ------------------------------------------------------------------- shell */
const NAV = [
  { href: '#/admin', label: 'Overview', match: '/admin', exact: true },
  { href: '#/admin/employers', label: 'Employers', match: '/admin/employers' },
  { href: '#/admin/candidates', label: 'Candidates', match: '/admin/candidates' },
  { href: '#/admin/health', label: 'Health & KPIs', match: '/admin/health' },
  { href: '#/admin/settings', label: 'Settings', match: '/admin/settings' },
];

export function adminShell(view) {
  const here = window.location.hash.replace('#', '') || '/admin';
  const profile = Store.profile('admin');

  const sidebar = h('nav', { class: 'sidebar', 'aria-label': 'Admin' }, [
    h('div', { class: 'sidebar-head' }, [
      h('a', { class: 'brand', href: '#/admin' }, [
        h('span', { class: 'mark', 'aria-hidden': 'true' }, [h('i'), h('i'), h('i'), h('i'), h('i')]),
        'HireIQ Admin',
      ]),
    ]),
    h('div', { class: 'nav' }, NAV.map((item) => h('a', {
      class: 'nav-item', href: item.href,
      'aria-current': (item.exact ? here === item.match : here.startsWith(item.match)) ? 'page' : null,
    }, item.label))),
    h('div', { class: 'sidebar-foot col gap3' }, [
      profile ? h('span', { class: 'fs12 t3', text: `Signed in as ${profile.username}` }) : null,
      h('div', { class: 'row-between' }, [
        h('button', {
          class: 'btn btn-ghost btn-sm', type: 'button',
          onClick: () => { Store.signOut('admin'); go('/admin/login'); },
        }, [h('span', { html: icon('logout', 14) }), 'Sign out']),
        themeToggle(),
      ]),
    ]),
  ]);

  const shell = h('div', { class: 'console' }, [
    sidebar,
    h('div', { class: 'main' }, [
      h('div', { class: 'mobile-bar' }, [
        h('button', {
          class: 'icon-btn', type: 'button', 'aria-label': 'Open navigation',
          html: icon('menu', 20),
          onClick: () => shell.classList.toggle('drawer-open'),
        }),
        h('span', { class: 'brand', style: { fontSize: 'var(--fs-14)' } }, ['HireIQ Admin']),
      ]),
      h('main', { class: 'grow page', id: 'main', tabindex: '-1' }, [view]),
    ]),
  ]);
  return shell;
}

/* --------------------------------------------------------------- settings */
export function adminSettings() {
  return h('div', {}, [
    pageHead('Settings'),
    h('div', { style: { maxWidth: '480px' } }, [
      passwordChangeCard({ onSubmit: (body) => Api.admin.changePassword(body) }),
    ]),
  ]);
}

/* --------------------------------------------------------------- KPI tiles */
function tile(label, value, sub) {
  return h('div', { class: 'card card-pad col gap1' }, [
    h('span', { class: 'filter-title', text: label }),
    h('strong', { class: 'display', style: { fontSize: 'var(--fs-34)' }, text: String(value) }),
    sub ? h('span', { class: 'fs11 t3', text: sub }) : null,
  ]);
}

/* -------------------------------------------------------------- overview */
export function overview() {
  const host = h('div', {}, [skeletonRows(3, 100)]);

  (async () => {
    try {
      const k = await Api.admin.kpis();
      clear(host).append(
        h('div', { class: 'row wrap gap4' }, [
          tile('Employers', k.employers.total, `${k.employers.active} active · ${k.employers.signups_7d} new/7d`),
          tile('Candidates', k.candidates.total, `${k.candidates.active} active · ${k.candidates.signups_7d} new/7d`),
          tile('Open roles', k.jobs.open, `${k.jobs.total} total`),
          tile('Applications', k.applications.total),
        ]),
        h('div', { class: 'row wrap gap4', style: { marginTop: 'var(--s5)' } }, [
          tile('Interviews', k.interviews.total, `${k.interviews.hiring} hiring · ${k.interviews.practice} practice`),
          tile('Live now', k.interviews.live),
          tile('Last 24h', k.interviews.last_24h),
          tile('Last 7d', k.interviews.last_7d),
        ]),
        h('div', { class: 'row wrap gap4', style: { marginTop: 'var(--s5)' } }, [
          tile('Assessments', k.assessments.total),
          tile('Average score',
            k.assessments.average_overall === null ? '—' : k.assessments.average_overall,
            k.assessments.average_overall === null ? 'no assessments yet' : 'out of 100'),
        ]),
        h('p', { class: 'fs11 t3', style: { marginTop: 'var(--s5)' }, text: `Generated ${relTime(k.generated_at)}` }),
      );
    } catch (err) {
      clear(host).append(h('div', { class: 'card' }, [empty({
        iconName: 'warning', title: 'Could not load KPIs', body: err.message,
      })]));
    }
  })();

  return h('div', {}, [pageHead('Overview', 'Real aggregate counts — nothing here is modelled or estimated.'), host]);
}

/* ---------------------------------------------------------------- health */
function statusRow(label, ok, detail) {
  return h('div', { class: 'row-between', style: { padding: 'var(--s3) 0', borderBottom: '1px solid var(--line)' } }, [
    h('span', { class: 'fs13', text: label }),
    h('span', { class: `chip ${ok ? 'band-high' : 'band-low'}`, text: detail }),
  ]);
}

export function healthKpiView() {
  const host = h('div', {}, [skeletonRows(2, 60)]);

  (async () => {
    try {
      const h_ = await Api.admin.health();
      clear(host).append(
        h('div', { class: 'card card-pad col', style: { maxWidth: '560px' } }, [
          statusRow('Database', h_.database.connected, h_.database.connected ? `connected (${h_.database.engine})` : 'unreachable'),
          statusRow('Gemini (AI interviewers + scoring)', h_.gemini_configured, h_.gemini_configured ? h_.gemini_live_model : 'not configured — offline fallback active'),
          statusRow('Agora RTC (voice/video)', h_.agora_rtc_configured, h_.agora_rtc_configured ? 'configured' : 'not configured'),
          statusRow('Agora ConvoAI (voice agents)', h_.agora_convoai_configured, h_.agora_convoai_configured ? 'configured' : 'not configured'),
          h('div', { class: 'row-between', style: { padding: 'var(--s3) 0' } }, [
            h('span', { class: 'fs13', text: 'Live interview sessions right now' }),
            h('strong', { text: String(h_.live_interview_sessions) }),
          ]),
        ]),
        h('p', { class: 'fs11 t3', style: { marginTop: 'var(--s4)' }, text: `Checked ${relTime(h_.checked_at)} · transcript TTL ${h_.turn_ttl_days} days` }),
      );
    } catch (err) {
      clear(host).append(h('div', { class: 'card' }, [empty({
        iconName: 'warning', title: 'Could not load health status', body: err.message,
      })]));
    }
  })();

  return h('div', {}, [pageHead('Health & KPIs', 'Live checks and configuration state — every value here is read at request time.'), host]);
}

/* ------------------------------------------------------------- employers */
export function employersList() {
  const host = h('div', {}, [skeletonRows(3, 64)]);

  const load = async () => {
    try {
      const rows = await Api.admin.listEmployers();
      clear(host).append(
        rows.length
          ? h('div', { class: 'table-wrap' }, [h('table', {}, [
              h('thead', {}, [h('tr', {}, [
                h('th', { text: 'Company' }), h('th', { text: 'Plan' }), h('th', { text: 'Jobs' }),
                h('th', { text: 'Users' }), h('th', { text: 'Created' }), h('th', { text: 'Status' }),
                h('th', {}, [h('span', { class: 'sr-only', text: 'Actions' })]),
              ])]),
              h('tbody', {}, rows.map((r) => h('tr', {}, [
                h('td', {}, [h('strong', { text: r.name }), r.domain ? h('div', { class: 'fs11 t3', text: r.domain }) : null]),
                h('td', { class: 'fs13 t2', text: r.plan }),
                h('td', { class: 'fs13 t2', text: String(r.job_count) }),
                h('td', { class: 'fs13 t2', text: String(r.user_count) }),
                h('td', { class: 'fs13 t2', text: fmtDate(r.created_at) }),
                h('td', {}, [h('span', { class: `status status-${r.active ? 'open' : 'closed'}`, text: r.active ? 'Active' : 'Suspended' })]),
                h('td', { style: { textAlign: 'right' } }, [
                  h('button', {
                    class: `btn btn-sm ${r.active ? 'btn-danger' : ''}`, type: 'button',
                    text: r.active ? 'Suspend' : 'Reactivate',
                    onClick: async (e) => {
                      e.currentTarget.disabled = true;
                      try { await Api.admin.toggleEmployer(r.id); toast(`${r.name} ${r.active ? 'suspended' : 'reactivated'}.`); load(); }
                      catch (err) { toast(err.message, 'err'); e.currentTarget.disabled = false; }
                    },
                  }),
                ]),
              ]))),
            ])])
          : h('div', { class: 'card' }, [empty({ iconName: 'building', title: 'No employers yet' })]),
      );
    } catch (err) {
      clear(host).append(h('div', { class: 'card' }, [empty({ iconName: 'warning', title: 'Could not load employers', body: err.message })]));
    }
  };
  load();

  return h('div', {}, [pageHead('Employers', 'Every workspace on the platform. Suspending one blocks sign-in immediately.'), host]);
}

/* ------------------------------------------------------------- candidates */
export function candidatesList() {
  const host = h('div', {}, [skeletonRows(3, 64)]);

  const load = async () => {
    try {
      const rows = await Api.admin.listCandidates();
      clear(host).append(
        rows.length
          ? h('div', { class: 'table-wrap' }, [h('table', {}, [
              h('thead', {}, [h('tr', {}, [
                h('th', { text: 'Candidate' }), h('th', { text: 'Applications' }),
                h('th', { text: 'Joined' }), h('th', { text: 'Status' }),
                h('th', {}, [h('span', { class: 'sr-only', text: 'Actions' })]),
              ])]),
              h('tbody', {}, rows.map((r) => h('tr', {}, [
                h('td', {}, [h('strong', { text: r.full_name }), h('div', { class: 'fs11 t3', text: r.email })]),
                h('td', { class: 'fs13 t2', text: String(r.application_count) }),
                h('td', { class: 'fs13 t2', text: fmtDate(r.created_at) }),
                h('td', {}, [h('span', { class: `status status-${r.is_active ? 'open' : 'closed'}`, text: r.is_active ? 'Active' : 'Suspended' })]),
                h('td', { style: { textAlign: 'right' } }, [
                  h('button', {
                    class: `btn btn-sm ${r.is_active ? 'btn-danger' : ''}`, type: 'button',
                    text: r.is_active ? 'Suspend' : 'Reactivate',
                    onClick: async (e) => {
                      e.currentTarget.disabled = true;
                      try { await Api.admin.toggleCandidate(r.id); toast(`${r.full_name} ${r.is_active ? 'suspended' : 'reactivated'}.`); load(); }
                      catch (err) { toast(err.message, 'err'); e.currentTarget.disabled = false; }
                    },
                  }),
                ]),
              ]))),
            ])])
          : h('div', { class: 'card' }, [empty({ iconName: 'users', title: 'No candidates yet' })]),
      );
    } catch (err) {
      clear(host).append(h('div', { class: 'card' }, [empty({ iconName: 'warning', title: 'Could not load candidates', body: err.message })]));
    }
  };
  load();

  return h('div', {}, [pageHead('Candidates', 'Every candidate account on the platform. Suspending one blocks sign-in immediately.'), host]);
}
