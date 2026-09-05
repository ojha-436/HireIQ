/* ============================================================================
   views/practice.js — candidate PRACTICE mode: start, retake, and the
   evidence-linked report with the 7-day coaching plan.

   Practice sessions reuse the exact same consent gate and interview room as a
   hiring interview (#/candidate/interview/:id in views/interview.js) — the
   backend's session endpoints are session-id-keyed and mode-agnostic, so
   nothing about the room changes here. This file only owns what practice mode
   adds: starting a session with no job application, and reading its report by
   session id (a hiring report is read by application id instead).
   ============================================================================ */

import { Api } from '../api.js';
import { go } from '../router.js';
import { clear, empty, h, icon, relTime, skeletonRows, toast } from '../ui.js';

const BAND_CLASS = {
  'well above bar': 'band-high', 'above bar': 'band-high',
  'at bar': 'band-mid', 'below bar': 'band-low', 'well below bar': 'band-low',
};

// Mirrors services/skills.LEXICON's canonical names (backend/app/services/skills.py) —
// a curated subset so the picker stays scannable. Any name here resolves server-side;
// picking nothing at all falls back to the candidate's own profile skills.
const SKILL_OPTIONS = [
  'Python', 'Java', 'JavaScript', 'React', 'SQL', 'System Design', 'Kubernetes', 'AWS',
  'Machine Learning', 'Data Engineering', 'Product Sense', 'Stakeholder Management',
  'Security', 'Mentorship', 'API Design',
];

const head = (title, sub) => h('header', { class: 'col gap3', style: { marginBottom: 'var(--s8)' } }, [
  h('h1', { class: 'display', style: { fontSize: 'var(--fs-34)' }, text: title }),
  sub ? h('p', { class: 't2', style: { maxWidth: '62ch' }, text: sub }) : null,
]);

/* ------------------------------------------------------------------ start a session
   Exported so a job-detail page or the dashboard can offer "Practice this job"
   without duplicating the start-then-navigate sequence. */
export async function startPractice({ jobId, skillNames } = {}) {
  const body = {};
  if (jobId) body.job_id = jobId;
  if (skillNames?.length) body.skill_names = skillNames;
  const started = await Api.candidate.practice.start(body);
  go(`/candidate/interview/${started.session_id}`);
}

/* --------------------------------------------------------- skill-based start */
function skillBasedCard() {
  const picked = new Set();

  const startBtn = h('button', {
    class: 'btn btn-primary btn-block', type: 'button',
    text: 'Start practice interview',
    onClick: async () => {
      startBtn.disabled = true;
      startBtn.replaceChildren(h('span', { class: 'spin' }), 'Starting…');
      try {
        await startPractice({ skillNames: [...picked] });
      } catch (err) {
        toast(err.message, 'err');
        startBtn.disabled = false;
        startBtn.replaceChildren('Start practice interview');
      }
    },
  });

  const skillChips = h('div', { class: 'skills' }, SKILL_OPTIONS.map((sk) => h('button', {
    class: 'chip chip-toggle', type: 'button', 'aria-pressed': 'false', text: sk,
    onClick: (e) => {
      const on = picked.has(sk);
      if (on) picked.delete(sk); else picked.add(sk);
      e.currentTarget.classList.toggle('on', !on);
      e.currentTarget.setAttribute('aria-pressed', String(!on));
    },
  })));

  return h('div', { class: 'col gap4' }, [
    h('p', { class: 'hint', text: 'Pick the skills you want the panel to probe, or leave everything unpicked and it will draw from your profile.' }),
    skillChips,
    startBtn,
  ]);
}

/* ---------------------------------------------------------- role-based start */
function roleBasedCard() {
  const host = h('div', { class: 'col gap4' }, [skeletonRows(2, 56)]);

  (async () => {
    try {
      const body = await Api.candidate.browseJobs({ sort: 'match' });
      const jobs = body.jobs || [];
      clear(host).append(
        h('p', { class: 'hint', text: 'Practice against a real, open role — grounded in that role\'s own required skills.' }),
        jobs.length
          ? h('div', { class: 'col gap2' }, jobs.slice(0, 8).map((j) => h('button', {
              class: 'role-row', type: 'button',
              style: { width: '100%', textAlign: 'left', cursor: 'pointer',
                      border: 'none', font: 'inherit', appearance: 'none' },
              onClick: async (e) => {
                const row = e.currentTarget;
                row.disabled = true;
                try { await startPractice({ jobId: j.id }); }
                catch (err) { toast(err.message, 'err'); row.disabled = false; }
              },
            }, [
              h('div', { class: 'col gap1 grow' }, [
                h('h2', { style: { fontSize: 'var(--fs-14)' }, text: j.title }),
                h('span', { class: 'fs11 t3', text: j.company_name }),
              ]),
              j.match_pct !== null && j.match_pct !== undefined
                ? h('span', { class: 'chip', text: `${j.match_pct}% match` }) : null,
              h('span', { class: 't3', html: icon('chevronRight', 16) }),
            ])))
          : h('p', { class: 'hint', text: 'No open roles right now — check back later.' }),
        h('a', { class: 'btn btn-sm', href: '#/candidate/jobs', text: 'Browse all open roles' }),
      );
    } catch (err) {
      clear(host).append(h('p', { class: 'hint', text: err.message }));
    }
  })();

  return host;
}

/* -------------------------------------------------------------------------- hub */
export function practiceHub() {
  let tab = 'skill';
  const body = h('div', {});
  const renderTab = () => clear(body).append(tab === 'skill' ? skillBasedCard() : roleBasedCard());

  const tabs = h('div', { class: 'seg', role: 'group', 'aria-label': 'Practice mode' },
    [['skill', 'Skill-based'], ['role', 'Role-based']].map(([value, label]) => h('button', {
      class: `seg-btn${tab === value ? ' on' : ''}`, type: 'button', 'aria-pressed': String(tab === value),
      onClick: (e) => {
        if (tab === value) return;
        tab = value;
        renderTab();
        for (const b of e.currentTarget.parentElement.children) {
          b.classList.toggle('on', b === e.currentTarget);
          b.setAttribute('aria-pressed', String(b === e.currentTarget));
        }
      },
    }, label)));
  renderTab();

  const startCard = h('div', { class: 'card card-pad col gap4' }, [
    h('h2', { style: { fontSize: 'var(--fs-19)' }, text: 'Start a practice interview' }),
    tabs,
    body,
  ]);

  const list = h('div', {}, [skeletonRows(2, 96)]);
  (async () => {
    try {
      const rows = await Api.candidate.practice.listSessions();
      clear(list).append(
        rows.length
          ? h('div', { class: 'role-list stagger' }, rows.map(sessionRow))
          : h('div', { class: 'card' }, [empty({
              iconName: 'mic',
              title: 'No practice interviews yet',
              body: 'Start one above to get an evidence-linked report and a 7-day improvement plan.',
            })]),
      );
    } catch (err) {
      clear(list).append(h('div', { class: 'card' }, [empty({
        iconName: 'warning', title: 'Could not load your practice history', body: err.message,
      })]));
    }
  })();

  return h('div', {}, [
    head('Practice', 'Run the same AI panel used for real interviews, entirely for yourself. Retake as many times as you want — every attempt is tracked.'),
    h('div', { class: 'col gap8', style: { maxWidth: '720px' } }, [
      startCard,
      h('section', { class: 'col gap4' }, [
        h('h2', { style: { fontSize: 'var(--fs-19)' }, text: 'Your practice interviews' }),
        list,
      ]),
    ]),
  ]);
}

function sessionRow(s) {
  const ended = s.status === 'ended';
  return h('div', { class: 'role-row' }, [
    h('div', { class: 'col gap2 grow' }, [
      h('h2', { text: s.job_title || 'General practice' }),
      h('div', { class: 'row wrap gap2' },
        (s.panel || []).map((p) => h('span', { class: `persona p-${p.key}`, text: p.label }))),
      h('span', { class: 'fs11 t3', text: relTime(s.created_at) }),
    ]),
    ended && s.overall !== null
      ? h('div', { class: 'col', style: { alignItems: 'flex-end' } }, [
          h('span', { class: 'filter-title', text: 'Overall' }),
          h('strong', { class: 'display', style: { fontSize: 'var(--fs-23)' }, text: String(s.overall) }),
        ])
      : null,
    ended
      ? h('a', { class: 'btn btn-primary', href: `#/candidate/practice/${s.session_id}/report`, text: 'View report' })
      : h('a', { class: 'btn', href: `#/candidate/interview/${s.session_id}`,
          text: s.disclosure_accepted ? 'Rejoin' : 'Continue' }),
  ]);
}

/* ------------------------------------------------------------------------- report */
export function practiceReport({ id }) {
  const host = h('div', {}, [skeletonRows(3, 120)]);

  (async () => {
    try {
      clear(host).append(renderReport(await Api.candidate.practice.report(id)));
    } catch (err) {
      clear(host).append(h('div', { class: 'card' }, [empty({
        iconName: 'warning', title: 'Report unavailable', body: err.message,
        action: h('a', { class: 'btn', href: '#/candidate/practice', text: 'Back to practice' }),
      })]));
    }
  })();

  return host;
}

/** One answer is scored on every dimension it touches, so group repeats by quote. */
function groupByQuote(lines) {
  const byQuote = new Map();
  for (const line of lines) {
    const key = line.quote || line.turn_ids.join(',');
    if (!byQuote.has(key)) byQuote.set(key, { quote: line.quote, dims: [] });
    const group = byQuote.get(key);
    if (!group.dims.some((d) => d.dimension === line.dimension)) {
      group.dims.push({ dimension: line.dimension, score: line.score });
    }
  }
  return [...byQuote.values()];
}

function renderReport(r) {
  return h('div', { class: 'col gap8' }, [
    h('a', { class: 'backlink', href: '#/candidate/practice' }, [
      h('span', { html: icon('arrowLeft', 15) }), 'Practice',
    ]),

    h('div', { class: 'col gap4' }, [
      h('span', { class: 'ai-badge' }, [h('span', { html: icon('activity', 12) }), 'AI interview']),
      h('h1', { class: 'display', style: { fontSize: 'var(--fs-34)' },
        text: r.job_title ? `Practice — ${r.job_title}` : 'Practice report' }),
      h('div', { class: 'row gap6 wrap', style: { alignItems: 'flex-end' } }, [
        h('div', { class: 'col' }, [
          h('span', { class: 'filter-title', text: 'Overall' }),
          h('strong', { class: 'display', style: { fontSize: 'var(--fs-41)' }, text: String(r.overall) }),
        ]),
        h('a', { class: 'btn btn-primary', href: '#/candidate/practice', text: 'Retake' }),
      ]),
      r.summary ? h('p', { class: 't2', text: r.summary }) : null,
    ]),

    h('div', { class: 'split-main' }, [
      h('div', { class: 'col gap8' }, [
        r.dimensions?.length ? h('section', { class: 'col gap3' }, [
          h('h2', { style: { fontSize: 'var(--fs-19)' }, text: 'By dimension' }),
          Object.keys(r.weights_applied || {}).length
            ? h('p', { class: 'hint', text: 'Some dimensions were weighted more heavily for this session.' })
            : null,
          ...r.dimensions.map((d) => h('div', { class: 'rv-dim' }, [
            h('div', { class: 'row-between gap3' }, [
              h('strong', { class: 'fs13', text: d.dimension }),
              h('span', { class: `chip ${BAND_CLASS[d.band] || ''}`, text: `${d.score} · ${d.band}` }),
            ]),
            d.verdict ? h('p', { class: 'fs12 t2', text: d.verdict }) : null,
          ])),
        ]) : null,

        r.evidence?.length ? h('section', { class: 'col gap3' }, [
          h('h2', { style: { fontSize: 'var(--fs-19)' }, text: 'What this is based on' }),
          h('p', { class: 'hint', text: 'Each answer you gave, and the dimensions it was scored on.' }),
          ...groupByQuote(r.evidence).map((g) => h('div', { class: 'fb-evidence' }, [
            h('p', { class: 'rv-quote fs13', text: `“${g.quote}”` }),
            h('div', { class: 'row gap2 wrap' }, g.dims.map((d) =>
              h('span', { class: 'chip chip-mono', text: `${d.dimension} ${d.score}` }))),
          ])),
        ]) : null,
      ]),

      h('aside', { class: 'col gap5 aside-sticky' }, [
        r.coaching ? coachingCard(r.coaching) : null,

        r.focus_areas?.length ? h('div', { class: 'card card-pad col gap2' }, [
          h('h3', { class: 'filter-title', text: 'Where to focus' }),
          h('ul', { class: 'col gap2' }, r.focus_areas.map((f) => h('li', { class: 'fs13 row gap2' }, [
            h('span', { html: icon('arrowRight', 13), style: { color: 'var(--text-3)' } }),
            f.claim || f.note || '',
          ]))),
        ]) : null,

        r.adaptivity?.total ? h('div', { class: 'card card-pad col gap2' }, [
          h('h3', { class: 'filter-title', text: 'How adaptive this session was' }),
          h('p', { class: 'fs13 t2',
            text: `${r.adaptivity.generated_pct}% of questions were generated from what you said, not pulled from a bank.` }),
        ]) : null,

        r.ai_disclosure ? h('p', { class: 'hint', text: r.ai_disclosure }) : null,
      ]),
    ]),
  ]);
}

function coachingCard(coaching) {
  return h('div', { class: 'card card-pad col gap4' }, [
    h('h3', { class: 'filter-title', text: '7-day improvement plan' }),
    h('p', { class: 'fs13 t2', text: coaching.observed }),
    h('ol', { class: 'col gap3' }, coaching.plan.map((step) => h('li', { class: 'row gap3' }, [
      h('span', { class: 'stage-seq', style: { width: '26px', height: '26px', fontSize: 'var(--fs-12)' },
        text: String(step.day) }),
      h('div', { class: 'col gap1' }, [
        h('strong', { class: 'fs13', text: step.focus }),
        h('span', { class: 'fs12 t2', text: step.task }),
      ]),
    ]))),
    h('p', { class: 'hint', text: coaching.next_recommendation }),
  ]);
}
