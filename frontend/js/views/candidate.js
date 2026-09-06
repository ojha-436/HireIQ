/* ============================================================================
   views/candidate.js — job board, role detail, my applications
   ============================================================================ */

import { Api } from '../api.js';
import { go } from '../router.js';
import {
  clear, debounce, empty, fmtDate, h, icon,
  relTime, skeletonRows, toast,
} from '../ui.js';
import { startPractice } from './practice.js';

function expLabel(job) {
  const lo = job.min_experience_years;
  const hi = job.max_experience_years;
  if (lo === null && hi === null) return '';
  if (lo !== null && hi !== null) return `${lo}-${hi} yrs`;
  if (lo !== null) return `${lo}+ yrs`;
  return `up to ${hi} yrs`;
}

const head = (title, sub) => h('header', { class: 'col gap3', style: { marginBottom: 'var(--s8)' } }, [
  h('h1', { class: 'display', style: { fontSize: 'var(--fs-34)' }, text: title }),
  sub ? h('p', { class: 't2', style: { maxWidth: '62ch' }, text: sub }) : null,
]);

/* ---------------------------------------------------------------- the board */
const EMPLOYMENT_LABEL = { full_time: 'Full time', part_time: 'Part time',
  contract: 'Contract', internship: 'Internship' };
const REMOTE_LABEL = { onsite: 'On site', hybrid: 'Hybrid', remote: 'Remote' };

export function jobBoard() {
  // One source of truth for the query; every control writes here and calls load().
  const state = { q: '', country: '', employment_type: '', remote_mode: '',
                  skills: [], min_experience: '', max_experience: '',
                  match_only: false, sort: 'recent' };

  const results = h('div', {});
  const filters = h('aside', { class: 'filters' });
  const summary = h('div', { class: 'row-between wrap gap4', style: { marginBottom: 'var(--s5)' } });

  const search = h('input', {
    class: 'input', type: 'search', id: 'job-search',
    placeholder: 'Search roles, skills, companies',
    'aria-label': 'Search open roles',
  });
  search.addEventListener('input', debounce((e) => { state.q = e.target.value.trim(); load(); }));

  const query = () => {
    const out = { sort: state.sort };
    for (const k of ['q', 'country', 'employment_type', 'remote_mode',
                     'min_experience', 'max_experience']) {
      if (state[k] !== '' && state[k] !== null) out[k] = state[k];
    }
    if (state.skills.length) out.skills = state.skills.join(',');
    if (state.match_only) out.match_only = 'true';
    return out;
  };

  async function load() {
    clear(results).append(skeletonRows(3, 150));
    try {
      const body = await Api.candidate.browseJobs(query());
      renderFilters(body);
      renderSummary(body);
      updateHero(body);
      clear(results).append(
        body.jobs.length
          ? h('div', { class: 'job-grid stagger' }, body.jobs.map(jobCard))
          : h('div', { class: 'card' }, [empty({
              iconName: 'search',
              title: 'No roles match these filters',
              body: activeCount()
                ? 'Try removing a filter — the experience range and skill filters are the most restrictive.'
                : 'Roles appear here the moment a company publishes one.',
              action: activeCount()
                ? h('button', { class: 'btn', type: 'button', text: 'Clear all filters',
                    onClick: reset })
                : null,
            })]),
      );
    } catch (err) {
      clear(results).append(h('div', { class: 'card' }, [empty({
        iconName: 'warning', title: 'Could not load roles', body: err.message,
        action: h('button', { class: 'btn', type: 'button', text: 'Try again', onClick: load }),
      })]));
    }
  }

  const activeCount = () => ['country', 'employment_type', 'remote_mode',
    'min_experience', 'max_experience'].filter((k) => state[k] !== '').length
    + state.skills.length + (state.match_only ? 1 : 0);

  function reset() {
    Object.assign(state, { country: '', employment_type: '', remote_mode: '', skills: [],
                           min_experience: '', max_experience: '', match_only: false });
    load();
  }

  /* ---- resume upload: what makes match scoring possible ---- */
  function resumeCard(body) {
    const input = h('input', {
      type: 'file', id: 'resume-file', accept: '.pdf,.txt,.md',
      style: { display: 'none' },
    });
    const status = h('p', { class: 'hint', role: 'status' });
    const pick = h('button', { class: 'btn btn-sm btn-block', type: 'button' }, [
      h('span', { html: icon('file', 14) }),
      body.profile_skills.length ? 'Replace resume' : 'Upload resume',
    ]);
    pick.onclick = () => input.click();

    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      pick.disabled = true;
      pick.replaceChildren(h('span', { class: 'spin' }), 'Reading…');
      status.textContent = '';
      try {
        const parsed = await Api.candidate.uploadResume(file);
        toast(`Found ${parsed.skills.length} skills${parsed.years_experience ? ` and ${parsed.years_experience} years` : ''}.`);
        // Prefill the experience filter from the resume — the single most useful default.
        if (parsed.years_experience) {
          state.min_experience = Math.floor(parsed.years_experience);
          state.max_experience = Math.floor(parsed.years_experience);
          state.sort = 'match';
        }
        load();
      } catch (err) {
        status.textContent = err.message;
        status.style.color = 'var(--danger)';
      } finally {
        pick.disabled = false;
        pick.replaceChildren(h('span', { html: icon('file', 14) }), 'Replace resume');
        input.value = '';
      }
    };

    return h('div', { class: 'filter-group' }, [
      h('h3', { class: 'filter-title', text: 'Match to my resume' }),
      body.profile_skills.length
        ? h('div', { class: 'col gap3' }, [
            h('p', { class: 'hint' }, [
              `${body.profile_skills.length} skills on file`,
              body.profile_years ? ` · ${body.profile_years} years` : '',
            ]),
            h('div', { class: 'skills' },
              body.profile_skills.slice(0, 8).map((sk) => h('span', { class: 'chip', text: sk }))),
          ])
        : h('p', { class: 'hint', text: 'Upload a PDF or text resume to see how well each role matches. Nothing but the extracted skills is stored.' }),
      pick, input, status,
      h('label', { class: 'check' }, [
        h('input', {
          type: 'checkbox', checked: state.match_only || null,
          disabled: !body.profile_skills.length || null,
          onChange: (e) => { state.match_only = e.target.checked; load(); },
        }),
        h('span', { class: 'fs13', text: 'Only roles I match' }),
      ]),
    ]);
  }

  function renderFilters(body) {
    const f = body.facets;
    const group = (title, children) =>
      h('div', { class: 'filter-group' }, [h('h3', { class: 'filter-title', text: title }), ...children]);

    const radioList = (key, options, labels) => options.map((opt) => h('label', { class: 'check' }, [
      h('input', {
        type: 'radio', name: key, checked: state[key] === opt || null,
        onChange: () => { state[key] = opt; load(); },
      }),
      h('span', { class: 'fs13', text: (labels && labels[opt]) || opt }),
    ]));

    clear(filters).append(
      h('div', { class: 'row-between', style: { marginBottom: 'var(--s2)' } }, [
        h('h2', { style: { fontSize: 'var(--fs-16)' }, text: 'Filters' }),
        activeCount()
          ? h('button', { class: 'btn btn-ghost btn-sm', type: 'button',
              text: `Clear (${activeCount()})`, onClick: reset })
          : null,
      ]),

      resumeCard(body),

      group('Experience', [
        h('p', { class: 'hint', text: 'Shows roles whose range includes your years.' }),
        h('div', { class: 'row gap2' }, [
          h('input', {
            class: 'input', type: 'number', min: '0', max: '50', placeholder: 'From',
            'aria-label': 'Minimum years of experience', value: state.min_experience,
            onChange: (e) => { state.min_experience = e.target.value; load(); },
          }),
          h('input', {
            class: 'input', type: 'number', min: '0', max: '50', placeholder: 'To',
            'aria-label': 'Maximum years of experience', value: state.max_experience,
            onChange: (e) => { state.max_experience = e.target.value; load(); },
          }),
        ]),
      ]),

      f.countries.length ? group('Country', radioList('country', f.countries)) : null,
      f.remote_modes.length ? group('Work mode', radioList('remote_mode', f.remote_modes, REMOTE_LABEL)) : null,
      f.employment_types.length
        ? group('Employment', radioList('employment_type', f.employment_types, EMPLOYMENT_LABEL)) : null,

      f.skills.length ? group('Skills', [
        h('div', { class: 'skills' }, f.skills.map((sk) => {
          const on = state.skills.includes(sk);
          return h('button', {
            class: `chip chip-toggle${on ? ' on' : ''}`, type: 'button',
            'aria-pressed': String(on), text: sk,
            onClick: () => {
              state.skills = on ? state.skills.filter((x) => x !== sk) : [...state.skills, sk];
              load();
            },
          });
        })),
      ]) : null,
    );
  }

  function renderSummary(body) {
    const sortSelect = h('select', { class: 'select', 'aria-label': 'Sort roles',
      style: { width: 'auto' },
      onChange: (e) => { state.sort = e.target.value; load(); } },
      [['recent', 'Most recent'], ['match', 'Best match'], ['experience', 'Least experience']]
        .map(([v, label]) => h('option', { value: v, selected: state.sort === v || null, text: label })));

    clear(summary).append(
      h('p', { class: 'fs13 t2' }, [
        h('strong', { style: { color: 'var(--text)' }, text: String(body.jobs.length) }),
        ` of ${body.facets.total_open} open role${body.facets.total_open === 1 ? '' : 's'}`,
      ]),
      sortSelect,
    );
  }

  const heroLede = h('p', { class: 'lede', text: 'Loading open roles…' });

  load();

  return h('div', {}, [
    h('nav', { class: 'crumbs', 'aria-label': 'Breadcrumb', style: { marginBottom: 'var(--s5)' } }, [
      h('a', { href: '#/candidate/dashboard', text: 'Home' }),
      h('span', { class: 'sep', text: '/' }),
      h('strong', { text: 'Open roles' }),
    ]),

    h('div', { class: 'board-hero' }, [
      h('span', { class: 'live-badge' }, [h('span', { class: 'dot', 'aria-hidden': 'true' }), 'Live roles']),
      h('h1', { class: 'display', text: 'Open roles' }),
      heroLede,
      h('div', { class: 'hero-actions' }, [
        h('a', { class: 'btn btn-primary btn-lg', href: '#/candidate/profile' }, [
          h('span', { html: icon('user', 15) }), 'Complete your profile',
        ]),
        h('a', { class: 'btn btn-lg', href: '#/candidate/practice' }, [
          h('span', { html: icon('mic', 15) }), 'Practice for a role',
        ]),
      ]),
    ]),

    h('div', { class: 'board' }, [
      filters,
      h('div', { class: 'board-main' }, [
        h('div', { style: { marginBottom: 'var(--s5)', maxWidth: '520px' } }, [search]),
        summary,
        results,
      ]),
    ]),
  ]);

  function updateHero(body) {
    const n = body.facets.total_open;
    clear(heroLede).append(
      h('strong', { text: `${n} live role${n === 1 ? '' : 's'}.` }),
      ' Every one runs an AI panel interview as part of its process, and shows your match % the moment your profile has skills on file.',
    );
  }
}

function jobCard(job) {
  const applyBtn = h('button', {
    class: 'btn btn-primary btn-sm', type: 'button',
    disabled: job.already_applied || null,
    text: job.already_applied ? 'Applied' : 'Apply',
    onClick: async (e) => {
      e.preventDefault();
      applyBtn.disabled = true;
      applyBtn.replaceChildren(h('span', { class: 'spin' }), 'Applying…');
      try {
        await Api.candidate.apply(job.id);
        toast('Applied. Track it under My applications.');
        applyBtn.disabled = true;
        applyBtn.replaceChildren('Applied');
      } catch (err) {
        toast(err.message, 'err');
        applyBtn.disabled = false;
        applyBtn.replaceChildren('Apply');
      }
    },
  });

  return h('div', { class: 'job-card-v2' }, [
    h('div', { class: 'jc-head' }, [
      h('span', { class: 'jc-logo', 'aria-hidden': 'true', text: (job.company_name || '?').slice(0, 2).toUpperCase() }),
      h('div', { class: 'col gap1 grow', style: { minWidth: 0 } }, [
        h('a', { class: 'jc-title', href: `#/candidate/jobs/${job.id}` }, [
          h('h2', { class: 'display', text: job.title }),
        ]),
        h('span', { class: 'fs12 t3', text: job.company_name }),
      ]),
      h('span', { class: 'fs11 t3', style: { whiteSpace: 'nowrap' },
        text: job.published_at ? `Posted ${fmtDate(job.published_at)}` : 'Recently posted' }),
    ]),

    h('div', { class: 'row wrap gap2' }, [
      // Match is a claim about fit, so it always says what it is based on.
      job.match_pct !== null && job.match_pct !== undefined
        ? h('span', { class: 'chip', style: { color: 'var(--accent)',
            borderColor: 'color-mix(in srgb, var(--accent) 45%, transparent)' },
            text: `${job.match_pct}% match` })
        : null,
      job.remote_mode ? h('span', { class: 'chip', text: REMOTE_LABEL[job.remote_mode] || job.remote_mode }) : null,
      h('span', { class: 'chip', text: EMPLOYMENT_LABEL[job.employment_type] || job.employment_type }),
      job.location ? h('span', { class: 'chip', text: job.location }) : null,
      expLabel(job) ? h('span', { class: 'chip', text: expLabel(job) }) : null,
    ]),

    job.jd_text ? h('p', { class: 'snippet', text: job.jd_text.trim() }) : null,

    (job.required_skills_json || []).length
      ? h('div', { class: 'row wrap gap2' },
          job.required_skills_json.slice(0, 4).map((s) => h('span', { class: 'chip chip-mono', text: s })))
      : null,

    h('div', { class: 'jc-actions' }, [
      applyBtn,
      h('a', { class: 'btn btn-sm', href: `#/candidate/jobs/${job.id}`, text: 'View role' }),
    ]),
  ]);
}

/* --------------------------------------------------------------- role detail */
export function jobDetail({ id }) {
  const host = h('div', {}, [skeletonRows(3, 120)]);

  (async () => {
    try {
      const job = await Api.candidate.jobDetail(id);
      clear(host).append(renderJob(job));
    } catch (err) {
      clear(host).append(h('div', { class: 'card' }, [empty({
        iconName: 'warning', title: 'Role unavailable', body: err.message,
        action: h('a', { class: 'btn', href: '#/candidate/jobs', text: 'Back to open roles' }),
      })]));
    }
  })();

  return host;
}

function renderJob(job) {
  const practiceBtn = h('button', {
    class: 'btn btn-block', type: 'button', text: 'Practice this job',
    onClick: async () => {
      practiceBtn.disabled = true;
      practiceBtn.replaceChildren(h('span', { class: 'spin' }), 'Starting…');
      try {
        await startPractice({ jobId: job.id });
      } catch (err) {
        toast(err.message, 'err');
        practiceBtn.disabled = false;
        practiceBtn.replaceChildren('Practice this job');
      }
    },
  });

  const applyBtn = h('button', {
    class: 'btn btn-primary btn-lg', type: 'button',
    disabled: job.already_applied || null,
    text: job.already_applied ? 'Application submitted' : 'Apply to this role',
    onClick: async () => {
      applyBtn.disabled = true;
      applyBtn.replaceChildren(h('span', { class: 'spin' }), 'Submitting…');
      try {
        await Api.candidate.apply(job.id);
        toast('Applied. Track it under My applications.');
        go('/candidate/applications');
      } catch (err) {
        toast(err.message, 'err');
        applyBtn.disabled = false;
        applyBtn.replaceChildren('Apply to this role');
      }
    },
  });

  return h('div', { class: 'col gap8' }, [
    h('a', { class: 'backlink', href: '#/candidate/jobs' }, [
      h('span', { html: icon('arrowLeft', 15) }), 'Open roles',
    ]),

    h('div', { class: 'col gap4' }, [
      h('span', { class: 'fs13 t2 row gap2' }, [h('span', { html: icon('building', 14) }), job.company_name]),
      h('h1', { class: 'display', style: { fontSize: 'var(--fs-41)' }, text: job.title }),
      h('div', { class: 'job-meta' }, [
        job.location && h('span', {}, [h('span', { html: icon('pin', 13) }), job.location]),
        job.department && h('span', {}, [h('span', { html: icon('layers', 13) }), job.department]),
        h('span', {}, [h('span', { html: icon('clock', 13) }), job.employment_type.replace('_', ' ')]),
      ]),
    ]),

    h('div', { class: 'split-main' }, [
      h('div', { class: 'col gap8' }, [
        h('section', { class: 'col gap4' }, [
          h('h2', { style: { fontSize: 'var(--fs-19)' }, text: 'About this role' }),
          // JD is user content: split to paragraphs as text nodes, never innerHTML.
          h('div', { class: 'col gap4', style: { color: 'var(--text-2)', lineHeight: 'var(--lh-body)' } },
            (job.jd_text || 'No description provided.').split(/\n{2,}/).filter(Boolean)
              .map((para) => h('p', { text: para.trim() }))),
        ]),

        (job.required_skills_json || []).length ? h('section', { class: 'col gap4' }, [
          h('h2', { style: { fontSize: 'var(--fs-19)' }, text: 'What the panel will ask about' }),
          h('div', { class: 'skills' }, job.required_skills_json.map((s) => h('span', { class: 'chip', text: s }))),
        ]) : null,
      ]),

      h('aside', { class: 'col gap5 aside-sticky' }, [
        h('div', { class: 'card card-pad col gap4' }, [
          applyBtn,
          job.already_applied
            ? h('p', { class: 'hint', text: 'You have already applied. Progress shows under My applications.' })
            : h('p', { class: 'hint', text: 'Your profile is sent with the application. You can update it any time before the interview.' }),
          h('hr', { class: 'hr' }),
          practiceBtn,
          h('p', { class: 'hint', text: job.missing_skills?.length
            ? `Run the same AI panel against this role's skills first — including ${job.missing_skills.slice(0, 2).join(' and ')}, where your profile has a gap. Nothing is shared with the employer.`
            : 'Run the same AI panel against this role’s skills before you apply. Nothing is shared with the employer.' }),
        ]),

        job.stage_names.length ? h('div', { class: 'card card-pad col gap4' }, [
          h('h3', { style: { fontSize: 'var(--fs-13)', letterSpacing: '.04em', textTransform: 'uppercase', color: 'var(--text-3)' }, text: 'Hiring process' }),
          h('ol', { class: 'col gap3' }, job.stage_names.map((name, i) => h('li', { class: 'row gap3' }, [
            h('span', { class: 'stage-seq', style: { width: '26px', height: '26px', fontSize: 'var(--fs-12)' }, text: String(i + 1) }),
            h('span', { class: 'fs13', text: name }),
          ]))),
        ]) : null,

        /* AI disclosure — layer 1 of 4. Shown before the candidate ever applies. */
        h('div', { class: 'card card-pad col gap3' }, [
          h('span', { class: 'ai-badge' }, [h('span', { html: icon('activity', 12) }), 'AI interview']),
          h('p', { class: 'hint' }, [
            'The panel interview in this process is conducted by AI, not people. You will be asked to confirm you understand that before the session starts, ',
            'and a member of your panel will say it out loud in the first minute. Your transcript is kept for 60 days; your assessment is kept and can be disputed.',
          ]),
        ]),
      ]),
    ]),
  ]);
}

/* ---------------------------------------------------------- my applications */
export function applications() {
  const host = h('div', {}, [skeletonRows(3, 88)]);

  (async () => {
    try {
      const apps = await Api.candidate.myApplications();
      clear(host).append(
        apps.length
          ? h('div', { class: 'role-list stagger' }, apps.map((a) => h('a', {
              class: 'role-row', href: `#/candidate/applications/${a.id}`,
            }, [
              h('div', { class: 'col gap2 grow' }, [
                h('h2', { text: a.job_title }),
                h('div', { class: 'job-meta' }, [
                  h('span', {}, [h('span', { html: icon('building', 13) }), a.company_name]),
                  h('span', {}, [h('span', { html: icon('clock', 13) }), `Applied ${relTime(a.applied_at)}`]),
                ]),
              ]),
              h('div', { class: 'col gap1', style: { alignItems: 'flex-end' } }, [
                h('span', { class: 'fs11 t3', style: { letterSpacing: '.05em', textTransform: 'uppercase' }, text: 'Current stage' }),
                h('strong', { class: 'fs13', text: a.current_stage_name || 'Under review' }),
              ]),
              h('span', { class: `status status-${a.status === 'rejected' ? 'closed' : 'applied'}`,
                text: a.status === 'rejected' ? 'Closed' : 'In progress' }),
              h('span', { class: 't3', html: icon('chevronRight', 18) }),
            ])))
          : h('div', { class: 'card' }, [empty({
              iconName: 'inbox',
              title: 'No applications yet',
              body: 'When you apply to a role it appears here, with the stage you are at and any feedback once it is released.',
              action: h('a', { class: 'btn btn-primary', href: '#/candidate/jobs', text: 'Browse open roles' }),
            })]),
      );
    } catch (err) {
      clear(host).append(h('div', { class: 'card' }, [empty({
        iconName: 'warning', title: 'Could not load your applications', body: err.message,
      })]));
    }
  })();

  return h('div', {}, [
    head('My applications', 'Where you are in every process you have entered.'),
    host,
  ]);
}
