/* ============================================================================
   views/dashboard.js — candidate home: Interview Readiness.

   `readiness` is null, not zero, until at least one practice interview has been
   scored — deliberately mirroring the "no fabricated benchmark" rule the hiring
   side already applies to percentiles (ARCHITECTURE.md §9). A candidate who has
   never practised sees an honest invitation to start, not a made-up number.
   ============================================================================ */

import { Api } from '../api.js';
import { clear, empty, h, icon, relTime, skeletonRows } from '../ui.js';

const head = (title, sub) => h('header', { class: 'col gap3', style: { marginBottom: 'var(--s8)' } }, [
  h('h1', { class: 'display', style: { fontSize: 'var(--fs-34)' }, text: title }),
  sub ? h('p', { class: 't2', style: { maxWidth: '62ch' }, text: sub }) : null,
]);

/* SVG, not a canvas chart library: one ring, drawn as a stroked circle whose
   dash-offset encodes the score. Same "raw markup via icon-style string" pattern
   ui.js already uses for its icon set. */
function readinessRingSvg(score) {
  const size = 168, stroke = 14, r = (size - stroke) / 2, c = 2 * Math.PI * r;
  const pct = score === null ? 0 : Math.max(0, Math.min(100, score));
  const offset = c * (1 - pct / 100);
  const fill = score === null ? 'var(--line-strong)' : 'var(--accent)';
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true">
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--line)" stroke-width="${stroke}"/>
    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${fill}" stroke-width="${stroke}"
      stroke-linecap="round" stroke-dasharray="${c.toFixed(2)}" stroke-dashoffset="${offset.toFixed(2)}"
      transform="rotate(-90 ${size / 2} ${size / 2})"/>
  </svg>`;
}

function readinessRing(score) {
  return h('div', { class: 'readiness-ring' }, [
    h('div', { html: readinessRingSvg(score) }),
    h('div', { class: 'readiness-ring-label col' }, [
      h('strong', { class: 'display', style: { fontSize: 'var(--fs-41)' },
        text: score === null ? '—' : String(score) }),
      h('span', { class: 'fs11 t3', text: score === null ? 'not yet scored' : '/ 100' }),
    ]),
  ]);
}

function dimensionBar(name, score) {
  // Dimensions are scored 0-5; render as a percentage fill for a scannable row.
  const pct = Math.max(0, Math.min(100, (score / 5) * 100));
  return h('div', { class: 'col gap1' }, [
    h('div', { class: 'row-between fs12' }, [
      h('span', { class: 't2', text: name.replace(/_/g, ' ') }),
      h('span', { class: 'mono t3', text: `${score.toFixed(1)}/5` }),
    ]),
    h('div', { class: 'meter', role: 'img', 'aria-label': `${name} ${score.toFixed(1)} of 5` }, [
      h('i', { style: { width: `${pct}%` } }),
    ]),
  ]);
}

export function dashboardView() {
  const host = h('div', {}, [skeletonRows(3, 140)]);

  (async () => {
    try {
      const [dash, apps, jobs] = await Promise.all([
        Api.candidate.dashboard(),
        Api.candidate.myApplications().catch(() => []),
        Api.candidate.browseJobs({ sort: 'match' }).catch(() => ({ jobs: [] })),
      ]);
      clear(host).append(render(dash, apps, jobs.jobs || []));
    } catch (err) {
      clear(host).append(h('div', { class: 'card' }, [empty({
        iconName: 'warning', title: 'Could not load your dashboard', body: err.message,
      })]));
    }
  })();

  return h('div', {}, [
    head('Dashboard', 'Prepare, practise, and see exactly where to improve before you interview for real.'),
    host,
  ]);
}

function render(dash, apps, jobs) {
  const dims = Object.entries(dash.dimensions || {}).sort((a, b) => a[1] - b[1]);
  const recommendedJobs = jobs.filter((j) => !j.already_applied).slice(0, 3);

  return h('div', { class: 'col gap8' }, [
    h('div', { class: 'split-main' }, [
      h('div', { class: 'col gap8' }, [
        h('div', { class: 'card card-pad row gap6 wrap', style: { alignItems: 'center' } }, [
          readinessRing(dash.readiness),
          h('div', { class: 'col gap3 grow' }, [
            h('h2', { style: { fontSize: 'var(--fs-19)' }, text: 'Interview Readiness' }),
            h('p', { class: 't2 fs13', text: dash.has_practice_history
              ? `Based on your last ${dash.recent_practice_sessions.length} practice interview${dash.recent_practice_sessions.length === 1 ? '' : 's'}.`
              : 'Complete a practice interview to see this score — it is never estimated.' }),
            h('a', { class: 'btn btn-primary', href: '#/candidate/practice',
              text: dash.has_practice_history ? dash.recommended_action : 'Start a practice interview' }),
          ]),
        ]),

        dims.length ? h('section', { class: 'card card-pad col gap4' }, [
          h('h2', { style: { fontSize: 'var(--fs-16)' }, text: 'By dimension' }),
          h('div', { class: 'col gap3' }, dims.map(([name, score]) => dimensionBar(name, score))),
        ]) : null,

        dash.recent_practice_sessions?.length ? h('section', { class: 'col gap3' }, [
          h('h2', { style: { fontSize: 'var(--fs-19)' }, text: 'Recent practice' }),
          h('div', { class: 'role-list' }, dash.recent_practice_sessions.map((s) => h('a', {
            class: 'role-row', href: `#/candidate/practice/${s.session_id}/report`,
          }, [
            h('div', { class: 'col gap1 grow' }, [
              h('h2', { text: s.job_title || 'General practice' }),
              h('span', { class: 'fs11 t3', text: relTime(s.created_at) }),
            ]),
            h('strong', { class: 'display', style: { fontSize: 'var(--fs-19)' }, text: String(s.overall) }),
            h('span', { class: 't3', html: icon('chevronRight', 18) }),
          ]))),
        ]) : null,
      ]),

      h('aside', { class: 'col gap5' }, [
        h('div', { class: 'card card-pad col gap3' }, [
          h('h3', { class: 'filter-title', text: 'Recommended roles' }),
          recommendedJobs.length
            ? h('div', { class: 'col gap3' }, recommendedJobs.map((j) => h('a', {
                class: 'row-between fs13', href: `#/candidate/jobs/${j.id}`,
              }, [
                h('span', { class: 'col gap1' }, [
                  h('strong', { text: j.title }),
                  h('span', { class: 't3 fs11', text: j.company_name }),
                ]),
                j.match_pct !== null && j.match_pct !== undefined
                  ? h('span', { class: 'chip', text: `${j.match_pct}%` }) : null,
              ])))
            : h('p', { class: 'hint', text: 'Upload a resume to see matched roles here.' }),
          h('a', { class: 'btn btn-sm btn-block', href: '#/candidate/jobs', text: 'Browse all roles' }),
        ]),

        h('div', { class: 'card card-pad col gap3' }, [
          h('h3', { class: 'filter-title', text: 'Applications' }),
          apps.length
            ? h('div', { class: 'col gap2' }, apps.slice(0, 3).map((a) => h('a', {
                class: 'row-between fs13', href: `#/candidate/applications/${a.id}`,
              }, [
                h('strong', { text: a.job_title }),
                h('span', { class: `status status-${a.status === 'rejected' ? 'closed' : 'applied'}`,
                  text: a.status === 'rejected' ? 'Closed' : 'In progress' }),
              ])))
            : h('p', { class: 'hint', text: 'No applications yet.' }),
          h('a', { class: 'btn btn-sm btn-block', href: '#/candidate/applications', text: 'View all' }),
        ]),
      ]),
    ]),
  ]);
}
