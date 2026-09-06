/* ============================================================================
   app.js — shells, route table, boot.

   Theme belongs to the PERSON (see theme.js), not to the portal they are in. What the
   role sets is DENSITY: `.dense` on the employer console, because a monitoring screen
   legitimately wants more on it than an application form does.
   ============================================================================ */

import { Api } from './api.js';
import { define, go, path, setNotFound, start } from './router.js';
import { Store } from './store.js';
import { clear, empty, h, icon, initials, relTime, themeToggle } from './ui.js';
import './theme.js';   // applies the stored/system theme before first paint
import * as Auth from './views/auth.js';
import * as Employer from './views/employer.js';
import * as Candidate from './views/candidate.js';
import * as Interview from './views/interview.js';
import * as Monitor from './views/monitor.js';
import * as Review from './views/review.js';
import * as Application from './views/application.js';
import * as Profile from './views/profile.js';
import * as Dashboard from './views/dashboard.js';
import * as Practice from './views/practice.js';
import * as Admin from './views/admin.js';

const root = document.getElementById('root');

/** `register` is retained at call sites but now only chooses density. */
function setRegister(register) {
  document.body.classList.toggle('dense', register === 'reg-control');
}

function mount(node, register) {
  setRegister(register);
  clear(root).append(node);
  // Purposeful motion: the new view rises into place so a route change reads as
  // navigation rather than a repaint. Collapsed to 1ms under prefers-reduced-motion.
  root.classList.remove('view-enter');
  void root.offsetWidth;              // restart the animation
  root.classList.add('view-enter');
}

/* ------------------------------------------------------------ auth guards */
function requireRole(role, render) {
  return (params) => {
    if (!Store.isAuthed(role)) { go(`/${role}/login`, { replace: true }); return; }
    render(params);
  };
}

/* --------------------------------------------------- employer console shell */
const EMPLOYER_NAV = [
  { href: '#/employer/jobs', label: 'Roles', ico: 'briefcase', match: '/employer/jobs' },
  { href: '#/employer/candidates', label: 'Candidates', ico: 'users', match: '/employer/candidates' },
  { href: '#/employer/analytics', label: 'Analytics', ico: 'chart', match: '/employer/analytics' },
  { href: '#/employer/settings', label: 'Settings', ico: 'user', match: '/employer/settings' },
];

function employerShell(view) {
  const profile = Store.profile('employer');
  const here = path();

  const sidebar = h('nav', { class: 'sidebar', 'aria-label': 'Main' }, [
    h('div', { class: 'sidebar-head' }, [
      h('a', { class: 'brand', href: '#/employer/jobs' }, [
        h('span', { class: 'mark', 'aria-hidden': 'true' }, [h('i'), h('i'), h('i'), h('i'), h('i')]),
        'HireIQ',
      ]),
      profile ? h('p', { class: 'fs12 t3 truncate', style: { marginTop: 'var(--s2)' }, text: profile.tenant_name }) : null,
    ]),

    h('div', { class: 'nav' }, [
      h('p', { class: 'nav-sep', text: 'Hiring' }),
      ...EMPLOYER_NAV.map((item) => h('a', {
        class: 'nav-item', href: item.href,
        'aria-current': here.startsWith(item.match) ? 'page' : null,
      }, [h('span', { html: icon(item.ico, 16) }), item.label])),
    ]),

    h('div', { class: 'sidebar-foot col gap3' }, [
      profile ? h('div', { class: 'row gap3' }, [
        h('span', { class: 'avatar', text: initials(profile.full_name) }),
        h('div', { class: 'col grow', style: { minWidth: 0 } }, [
          h('strong', { class: 'fs13 truncate', text: profile.full_name }),
          h('span', { class: 'fs11 t3 truncate', text: profile.email }),
        ]),
      ]) : null,
      h('div', { class: 'row-between' }, [
        h('button', {
          class: 'btn btn-ghost btn-sm', type: 'button',
          onClick: () => { Store.signOut('employer'); go('/employer/login'); },
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
        h('span', { class: 'brand', style: { fontSize: 'var(--fs-14)' } }, ['HireIQ']),
      ]),
      h('main', { class: 'grow', id: 'main', tabindex: '-1' }, [view]),
    ]),
  ]);

  return shell;
}

/* --------------------------------------------------- candidate portal shell */
const CANDIDATE_NAV = [
  { href: '#/candidate/dashboard', label: 'Dashboard', match: '/candidate/dashboard' },
  { href: '#/candidate/jobs', label: 'Open roles', match: '/candidate/jobs' },
  { href: '#/candidate/practice', label: 'Practice', match: '/candidate/practice' },
  { href: '#/candidate/applications', label: 'My applications', match: '/candidate/applications' },
  { href: '#/candidate/interviews', label: 'Interviews', match: '/candidate/interviews' },
  { href: '#/candidate/profile', label: 'Profile', match: '/candidate/profile' },
];

/* Built once and reused across every route change — candidateShell() is called on
   every navigation, and a fresh `document` click-listener per call would leak one
   more forever. Re-inserting the same node into a new parent just moves it (DOM
   nodes are singly-parented), so this is safe. */
let _notifBell = null;

function notificationText(n) {
  const p = n.payload || {};
  switch (n.kind) {
    case 'interview_ready':
      return `Your AI panel interview for ${p.job_title || 'a role'} is ready — join when you're ready.`;
    case 'feedback_released':
      return `Feedback is ready for ${p.job_title || 'your application'}.`;
    case 'application_decision':
      if (p.status === 'rejected') return `An update on your application to ${p.job_title || 'a role'}.`;
      if (p.status === 'offer') return `You've reached the offer stage for ${p.job_title || 'your application'}.`;
      if (p.moved_to) return `You moved to ${p.moved_to} for ${p.job_title || 'your application'}.`;
      return `Your application to ${p.job_title || 'a role'} was updated.`;
    default:
      return (n.kind || 'Update').replace(/_/g, ' ');
  }
}

function notificationHref(n) {
  const p = n.payload || {};
  if (n.kind === 'interview_ready' && p.session_id) return `#/candidate/interview/${p.session_id}`;
  if (p.application_id) return `#/candidate/applications/${p.application_id}`;
  return '#/candidate/applications';
}

function notificationBell() {
  if (_notifBell) return _notifBell;

  const badge = h('span', { class: 'notif-badge', hidden: true });
  const panel = h('div', { class: 'notif-panel', hidden: true, role: 'menu', 'aria-label': 'Notifications' });
  // Appended to <body>, not to the nav: .portal-bar/.sidebar use backdrop-filter for
  // the frosted-glass look, and any CSS filter silently clips absolutely-positioned
  // descendants to the filtered element's own box — the dropdown would exist, pass
  // every "is it visible" check, and never actually paint. Fixed-position + a body
  // append is the standard escape hatch.
  document.body.append(panel);

  function position() {
    const r = btn.getBoundingClientRect();
    panel.style.top = `${Math.round(r.bottom + 8)}px`;
    panel.style.right = `${Math.round(window.innerWidth - r.right)}px`;
  }

  async function load() {
    try {
      const data = await Api.candidate.notifications.list();
      badge.hidden = !data.unread_count;
      badge.textContent = data.unread_count > 9 ? '9+' : String(data.unread_count || '');
      clear(panel).append(
        h('div', { class: 'notif-head row-between' }, [
          h('strong', { class: 'fs13', text: 'Notifications' }),
          data.unread_count
            ? h('button', {
                class: 'btn btn-ghost btn-sm', type: 'button', text: 'Mark all read',
                onClick: async (e) => { e.stopPropagation(); await Api.candidate.notifications.markAllRead(); load(); },
              })
            : null,
        ]),
        data.notifications.length
          ? h('div', { class: 'notif-list' }, data.notifications.map((n) => h('a', {
              class: `notif-item${n.read ? '' : ' unread'}`, href: notificationHref(n),
              onClick: async () => {
                panel.hidden = true;
                if (!n.read) { await Api.candidate.notifications.markRead(n.id); load(); }
              },
            }, [
              h('p', { class: 'fs13', text: notificationText(n) }),
              h('span', { class: 'fs11 t3', text: relTime(n.created_at) }),
            ])))
          : h('p', { class: 'hint', style: { padding: 'var(--s5)' }, text: 'Nothing yet.' }),
      );
    } catch { /* a broken bell must not break the whole shell */ }
  }

  const btn = h('button', {
    class: 'icon-btn', type: 'button', 'aria-label': 'Notifications', 'aria-haspopup': 'true',
    onClick: async (e) => {
      e.stopPropagation();
      const willOpen = panel.hidden;
      if (willOpen) position();
      panel.hidden = !willOpen;
      if (willOpen) await load();
    },
  }, [h('span', { html: icon('bell', 17) }), badge]);

  const wrap = h('div', { class: 'notif-wrap' }, [btn]);
  wrap.refresh = load;
  document.addEventListener('click', (e) => {
    if (!panel.contains(e.target) && !btn.contains(e.target)) panel.hidden = true;
  });
  window.addEventListener('resize', () => { if (!panel.hidden) position(); });
  // The nav bar is sticky, not scroll-fixed content, so the button's own position
  // barely moves — but "barely" isn't "never" (mobile drawer, zoom), and a dropdown
  // that drifts from its anchor reads as broken.
  window.addEventListener('scroll', () => { if (!panel.hidden) position(); }, true);

  load();
  _notifBell = wrap;
  return wrap;
}

function candidateShell(view) {
  const profile = Store.profile('candidate');
  const here = path();
  const bell = notificationBell();
  bell.refresh();

  return h('div', { class: 'portal' }, [
    h('header', { class: 'portal-bar' }, [
      h('a', { class: 'brand', href: '#/candidate/dashboard' }, [
        h('span', { class: 'mark', 'aria-hidden': 'true' }, [h('i'), h('i'), h('i'), h('i'), h('i')]),
        'HireIQ',
      ]),
      h('nav', { class: 'portal-nav', 'aria-label': 'Main' }, [
        ...CANDIDATE_NAV.map((item) => h('a', {
          href: item.href, 'aria-current': here.startsWith(item.match) ? 'page' : null, text: item.label,
        })),
        h('span', { style: { width: 'var(--s3)' } }),
        bell,
        profile ? h('span', { class: 'avatar', title: profile.full_name, text: initials(profile.full_name) }) : null,
        themeToggle(),
        h('button', {
          class: 'icon-btn', type: 'button', 'aria-label': 'Sign out',
          html: icon('logout', 17),
          onClick: () => { Store.signOut('candidate'); go('/candidate/login'); },
        }),
      ]),
    ]),
    h('main', { class: 'portal-main', id: 'main', tabindex: '-1' }, [view]),
  ]);
}

/* ------------------------------------------------------------------ routes */
define('/', () => {
  setRegister('reg-control');
  mount(Auth.landing(), 'reg-control');
});

define('/employer/login', () => mount(Auth.employerLogin(), 'reg-control'));
define('/employer/register', () => mount(Auth.employerRegister(), 'reg-control'));
define('/candidate/login', () => mount(Auth.candidateLogin(), 'reg-calm'));
define('/candidate/register', () => mount(Auth.candidateRegister(), 'reg-calm'));

define('/employer/jobs', requireRole('employer', () =>
  mount(employerShell(Employer.jobsList()), 'reg-control')));
define('/employer/jobs/new', requireRole('employer', () =>
  mount(employerShell(Employer.jobNew()), 'reg-control')));
define('/employer/jobs/:id', requireRole('employer', (p) =>
  mount(employerShell(Employer.jobDetail(p)), 'reg-control')));
define('/employer/jobs/:id/edit', requireRole('employer', (p) =>
  mount(employerShell(Employer.jobEdit(p)), 'reg-control')));
define('/employer/settings', requireRole('employer', () =>
  mount(employerShell(Employer.settingsView()), 'reg-control')));
define('/employer/monitor/:id', requireRole('employer', (p) =>
  mount(employerShell(Monitor.monitorView(p)), 'reg-control')));
define('/employer/review/:id', requireRole('employer', (p) =>
  mount(employerShell(Review.reviewView(p)), 'reg-control')));

/* Phase-2 destinations exist in the nav; they say so honestly rather than 404. */
define('/employer/candidates', requireRole('employer', () =>
  mount(employerShell(comingSoon('Candidates',
    'A cross-role view of every candidate in your pipelines. Lands with the interview engine in Phase 3.')), 'reg-control')));
define('/employer/analytics', requireRole('employer', () =>
  mount(employerShell(comingSoon('Analytics',
    'Funnel conversion and score distribution per role. Needs completed interviews before it can show anything real — so it ships after Phase 5.')), 'reg-control')));

define('/candidate/dashboard', requireRole('candidate', () =>
  mount(candidateShell(Dashboard.dashboardView()), 'reg-calm')));
define('/candidate/practice', requireRole('candidate', () =>
  mount(candidateShell(Practice.practiceHub()), 'reg-calm')));
define('/candidate/practice/:id/report', requireRole('candidate', (p) =>
  mount(candidateShell(Practice.practiceReport(p)), 'reg-calm')));

define('/candidate/jobs', requireRole('candidate', () =>
  mount(candidateShell(Candidate.jobBoard()), 'reg-calm')));
define('/candidate/jobs/:id', requireRole('candidate', (p) =>
  mount(candidateShell(Candidate.jobDetail(p)), 'reg-calm')));
define('/candidate/applications', requireRole('candidate', () =>
  mount(candidateShell(Candidate.applications()), 'reg-calm')));
define('/candidate/interviews', requireRole('candidate', () =>
  mount(candidateShell(Interview.pendingInterviews()), 'reg-calm')));
define('/candidate/applications/:id', requireRole('candidate', (p) =>
  mount(candidateShell(Application.applicationDetail(p)), 'reg-calm')));
define('/candidate/profile', requireRole('candidate', () =>
  mount(candidateShell(Profile.profileView()), 'reg-calm')));

/* The room is full-bleed and runs in the control register — no portal chrome. */
define('/candidate/interview/:id', requireRole('candidate', (p) =>
  mount(Interview.interviewGate(p), 'reg-control')));

/* ------------------------------------------------------------- admin portal */
define('/admin/login', () => mount(Admin.adminLogin(), 'reg-control'));
define('/admin', requireRole('admin', () =>
  mount(Admin.adminShell(Admin.overview()), 'reg-control')));
define('/admin/employers', requireRole('admin', () =>
  mount(Admin.adminShell(Admin.employersList()), 'reg-control')));
define('/admin/candidates', requireRole('admin', () =>
  mount(Admin.adminShell(Admin.candidatesList()), 'reg-control')));
define('/admin/health', requireRole('admin', () =>
  mount(Admin.adminShell(Admin.healthKpiView()), 'reg-control')));
define('/admin/settings', requireRole('admin', () =>
  mount(Admin.adminShell(Admin.adminSettings()), 'reg-control')));

setNotFound(() => {
  setRegister('reg-control');
  mount(h('div', { class: 'page' }, [
    h('div', { class: 'card' }, [empty({
      iconName: 'search',
      title: 'Page not found',
      body: `Nothing lives at ${path()}.`,
      action: h('a', { class: 'btn btn-primary', href: '#/', text: 'Go home' }),
    })]),
  ]), 'reg-control');
});

function comingSoon(title, body) {
  return h('div', { class: 'page' }, [
    h('div', { class: 'card' }, [empty({ iconName: 'layers', title, body })]),
  ]);
}

/* -------------------------------------------------------------------- boot */
(async function boot() {
  // Refresh cached profiles so the shell never renders a stale name.
  const refresh = [];
  if (Store.isAuthed('employer')) {
    refresh.push(Api.employer.me().then((p) => Store.setProfile('employer', p)).catch(() => {}));
  }
  if (Store.isAuthed('candidate')) {
    refresh.push(Api.candidate.me().then((p) => Store.setProfile('candidate', p)).catch(() => {}));
  }
  if (Store.isAuthed('admin')) {
    refresh.push(Api.admin.me().then((p) => Store.setProfile('admin', p)).catch(() => {}));
  }
  await Promise.all(refresh);

  if (!window.location.hash) {
    if (Store.isAuthed('employer')) window.location.replace('#/employer/jobs');
    else if (Store.isAuthed('candidate')) window.location.replace('#/candidate/dashboard');
    else if (Store.isAuthed('admin')) window.location.replace('#/admin');
  }

  start();
})();
