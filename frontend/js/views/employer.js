/* ============================================================================
   views/employer.js — roles list, role composer, role detail (pipeline + applicants)
   ============================================================================ */

import { Api } from '../api.js';
import { go } from '../router.js';
import {
  clear, difficultyGauge, empty, fmtDate, h, icon, initials,
  modal, personaChip, relTime, skeletonRows, toast,
} from '../ui.js';

const STATUS_LABEL = { draft: 'Draft', open: 'Open', paused: 'Paused', closed: 'Closed' };

const pageHead = (title, sub, actions) => h('header', { class: 'page-head row-between wrap gap5' }, [
  h('div', {}, [
    h('h1', { class: 'page-title display', text: title }),
    sub ? h('p', { class: 'page-sub', text: sub }) : null,
  ]),
  actions ? h('div', { class: 'row gap3' }, actions) : null,
]);

/* ------------------------------------------------------------- roles list */
export function jobsList() {
  const host = h('div', { class: 'page' }, [
    pageHead('Roles', 'Every open, paused and draft role in this workspace.', [
      h('a', { class: 'btn btn-primary', href: '#/employer/jobs/new' }, [
        h('span', { html: icon('plus', 15) }), 'New role',
      ]),
    ]),
    skeletonRows(3, 84),
  ]);

  (async () => {
    try {
      const jobs = await Api.employer.listJobs();
      const body = jobs.length
        ? h('div', { class: 'role-list stagger' }, jobs.map(jobRow))
        : h('div', { class: 'card' }, [empty({
            iconName: 'briefcase',
            title: 'No roles yet',
            body: 'Post a role to open a pipeline. The panel and difficulty are proposed from the job description, and you can change both.',
            action: h('a', { class: 'btn btn-primary', href: '#/employer/jobs/new' }, [
              h('span', { html: icon('plus', 15) }), 'Post your first role',
            ]),
          })]);
      host.replaceChild(body, host.lastChild);
    } catch (err) {
      host.replaceChild(errorPanel(err, () => go('/employer/jobs')), host.lastChild);
    }
  })();

  return host;
}

function jobRow(job) {
  const aiStage = job.stages.find((s) => s.kind === 'ai_interview');
  const panel = aiStage?.interview_config_json?.panel || [];

  return h('a', { class: 'role-row', href: `#/employer/jobs/${job.id}` }, [
    h('div', { class: 'col gap2 grow' }, [
      h('h2', { text: job.title }),
      h('div', { class: 'job-meta' }, [
        job.department && h('span', {}, [h('span', { html: icon('layers', 13) }), job.department]),
        job.location && h('span', {}, [h('span', { html: icon('pin', 13) }), job.location]),
        h('span', {}, [h('span', { html: icon('clock', 13) }), `Created ${fmtDate(job.created_at)}`]),
      ]),
      panel.length ? h('div', { class: 'row wrap gap2', style: { marginTop: '2px' } },
        panel.map(personaChip)) : null,
    ]),
    h('div', { class: 'stat' }, [
      h('b', { text: String(job.applicant_count) }),
      h('span', { text: job.applicant_count === 1 ? 'applicant' : 'applicants' }),
    ]),
    h('span', { class: `status status-${job.status}`, text: STATUS_LABEL[job.status] || job.status }),
    h('span', { class: 't3', html: icon('chevronRight', 18) }),
  ]);
}

/* ------------------------------------------------------- pipeline builder
   Question 2: the employer decides which stages are AI and which are human,
   and for AI stages which personas sit on the panel and how hard it starts. */
const PERSONAS = [
  ['tech', 'Technical'], ['product', 'Product'], ['hiring_manager', 'Hiring Manager'],
  ['customer', 'Customer'], ['behavioural', 'Behavioural'],
];
const PRESET_SIZE = { screen: 2, panel: 3, loop: 5 };
// Names we generated, and may therefore safely replace when a stage's type changes.
const DEFAULT_NAMES = new Set([
  'AI Panel Interview', 'Human Round', 'AI Screen', 'AI Panel',
  'AI Interview 1', 'AI Interview 2', 'AI Interview 3', 'AI Interview 4',
]);

export function pipelineBuilder(initial) {
  let stages = (initial && initial.length ? initial : [
    { name: 'AI Panel Interview', kind: 'ai_interview',
      interview_config_json: { panel: ['tech', 'product', 'hiring_manager'],
                               preset: 'panel', start_difficulty: 3 } },
    { name: 'Human Round', kind: 'human_interview', interview_config_json: {} },
  ]).map((st) => ({ ...st, interview_config_json: { ...(st.interview_config_json || {}) } }));

  const host = h('div', { class: 'pipeline' });

  const render = () => {
    clear(host).append(
      ...stages.map((stage, i) => stageCard(stage, i)),
      h('div', { class: 'row gap2 wrap' }, [
        h('button', { class: 'btn btn-sm', type: 'button',
          onClick: () => { addStage('ai_interview'); } }, [
          h('span', { html: icon('plus', 14) }), 'Add AI interview',
        ]),
        h('button', { class: 'btn btn-sm', type: 'button',
          onClick: () => { addStage('human_interview'); } }, [
          h('span', { html: icon('plus', 14) }), 'Add human round',
        ]),
      ]),
      h('p', { class: 'hint', text: 'Candidates move through these in order. AI stages run the panel; human rounds are run by your team.' }),
    );
  };

  function addStage(kind) {
    if (stages.length >= 6) { toast('Six stages is the practical limit.', 'err'); return; }
    stages.push(kind === 'ai_interview'
      ? { name: `AI Interview ${stages.filter((s2) => s2.kind === 'ai_interview').length + 1}`,
          kind, interview_config_json: { panel: ['tech', 'product'], preset: 'screen', start_difficulty: 3 } }
      : { name: 'Human Round', kind, interview_config_json: {} });
    render();
  }

  function stageCard(stage, i) {
    const cfg = stage.interview_config_json;
    const isAI = stage.kind === 'ai_interview';

    const nameInput = h('input', {
      class: 'input', value: stage.name, 'aria-label': `Stage ${i + 1} name`,
      onInput: (e) => { stage.name = e.target.value; },
    });

    const kindToggle = h('div', { class: 'seg', role: 'group', 'aria-label': `Stage ${i + 1} type` },
      [['ai_interview', 'AI panel', 'activity'], ['human_interview', 'Human round', 'user']]
        .map(([value, label, ic]) => h('button', {
          class: `seg-btn${stage.kind === value ? ' on' : ''}`, type: 'button',
          'aria-pressed': String(stage.kind === value),
          onClick: () => {
            if (stage.kind === value) return;
            // Rename only if the employer hasn't typed their own label — otherwise a
            // stage called "AI Panel" would sit there labelled "Human Round".
            if (DEFAULT_NAMES.has(stage.name.trim())) {
              stage.name = value === 'ai_interview' ? 'AI Panel Interview' : 'Human Round';
            }
            stage.kind = value;
            stage.interview_config_json = value === 'ai_interview'
              ? { panel: ['tech', 'product'], preset: 'screen', start_difficulty: 3 }
              : {};
            render();
          },
        }, [h('span', { html: icon(ic, 13) }), label])));

    return h('div', { class: 'stage' }, [
      h('span', { class: 'stage-seq', text: String(i + 1) }),
      h('div', { class: 'col gap4' }, [
        h('div', { class: 'row-between gap3 wrap' }, [
          h('div', { class: 'grow', style: { minWidth: '180px' } }, [nameInput]),
          h('div', { class: 'row gap2' }, [
            kindToggle,
            stages.length > 1 ? h('button', {
              class: 'icon-btn', type: 'button', 'aria-label': `Remove stage ${i + 1}`,
              html: icon('x', 16),
              onClick: () => { stages.splice(i, 1); render(); },
            }) : null,
          ]),
        ]),

        isAI ? h('div', { class: 'col gap4', style: { paddingTop: 'var(--s2)' } }, [
          h('div', { class: 'col gap2' }, [
            h('span', { class: 'filter-title', text: 'Panel' }),
            h('div', { class: 'row wrap gap2' }, PERSONAS.map(([key, label]) => {
              const on = (cfg.panel || []).includes(key);
              const cap = PRESET_SIZE[cfg.preset] || 3;
              const full = !on && (cfg.panel || []).length >= cap;
              return h('button', {
                class: `persona-toggle p-${key}${on ? ' on' : ''}`, type: 'button',
                'aria-pressed': String(on), disabled: full || null,
                title: full ? `The ${cfg.preset} format seats ${cap}` : '',
                onClick: () => {
                  cfg.panel = on ? cfg.panel.filter((k) => k !== key) : [...(cfg.panel || []), key];
                  render();
                },
              }, [label]);
            })),
            h('p', { class: 'hint', text: `${(cfg.panel || []).length} of ${PRESET_SIZE[cfg.preset] || 3} seats used. The product interviewer is what makes the impact challenge fire — dropping it disables that rule.` }),
          ]),

          h('div', { class: 'row gap5 wrap' }, [
            h('label', { class: 'col gap2' }, [
              h('span', { class: 'filter-title', text: 'Format' }),
              h('select', { class: 'select', style: { width: 'auto' },
                onChange: (e) => {
                  cfg.preset = e.target.value;
                  cfg.panel = (cfg.panel || []).slice(0, PRESET_SIZE[cfg.preset]);
                  render();
                } },
                [['screen', 'Screen · 12 min · 2 seats'], ['panel', 'Panel · 25 min · 3 seats'],
                 ['loop', 'Loop · 40 min · 5 seats']].map(([v, label]) =>
                  h('option', { value: v, selected: cfg.preset === v || null, text: label }))),
            ]),
            h('label', { class: 'col gap2' }, [
              h('span', { class: 'filter-title', text: 'Starting difficulty' }),
              h('div', { class: 'row gap3' }, [
                h('input', {
                  type: 'range', min: '1', max: '5', value: String(cfg.start_difficulty || 3),
                  'aria-label': 'Starting difficulty, 1 to 5',
                  onInput: (e) => { cfg.start_difficulty = Number(e.target.value); render(); },
                }),
                difficultyGauge(cfg.start_difficulty || 3),
              ]),
            ]),
          ]),
        ]) : h('p', { class: 'hint', text: 'A member of your team runs this round. No AI interviewer joins.' }),
      ]),
    ]);
  }

  render();

  return {
    node: host,
    /** Stages in wire order, with seq assigned from position. */
    value() {
      return stages.map((st, i) => ({
        seq: i + 1, name: st.name.trim() || `Stage ${i + 1}`, kind: st.kind,
        interview_config_json: st.kind === 'ai_interview'
          ? { panel: (st.interview_config_json.panel || []).length
                ? st.interview_config_json.panel : ['tech'],
              preset: st.interview_config_json.preset || 'panel',
              start_difficulty: st.interview_config_json.start_difficulty || 3 }
          : {},
      }));
    },
  };
}

/* ---------------------------------------------------------- role composer */
export function jobNew() {
  const title = input({ name: 'title', label: 'Role title', required: true, placeholder: 'Senior Backend Engineer' });
  const dept = input({ name: 'department', label: 'Team', placeholder: 'Platform' });
  const loc = input({ name: 'location', label: 'Location', placeholder: 'Bengaluru · Hybrid' });

  const jd = h('textarea', {
    class: 'textarea', id: 'f-jd', name: 'jd_text',
    placeholder: 'Paste the job description. Skills are extracted from it, and the interview panel is proposed from those skills.',
  });

  // Live skill preview — shows the employer what the interview will be grounded in.
  const preview = h('div', { class: 'card card-pad col gap4', style: { position: 'sticky', top: 'calc(60px + var(--s6))' } });
  const renderPreview = () => {
    const found = detectSkills(jd.value);
    clear(preview).append(
      h('div', { class: 'row-between' }, [
        h('h3', { style: { fontSize: 'var(--fs-13)', letterSpacing: '.04em', textTransform: 'uppercase', color: 'var(--text-3)' }, text: 'Detected from the JD' }),
        h('span', { class: 'chip chip-mono', text: `${found.length}` }),
      ]),
      found.length
        ? h('div', { class: 'skills' }, found.map((s) => h('span', { class: 'chip', text: s })))
        : h('p', { class: 'hint', text: 'Skills appear here as you paste. The server re-extracts on save — this preview is indicative.' }),
      h('hr', { class: 'hr' }),
      h('p', { class: 'hint' }, [
        'A default pipeline is created for you: an ',
        h('strong', { style: { color: 'var(--text-2)' }, text: 'AI Panel Interview' }),
        ' stage followed by a human round. You can change the panel and starting difficulty after saving.',
      ]),
    );
  };
  jd.addEventListener('input', renderPreview);

  const country = input({ name: 'country', label: 'Country', placeholder: 'India' });
  const mode = h('select', { class: 'select', id: 'f-mode', name: 'remote_mode' },
    [['onsite', 'On site'], ['hybrid', 'Hybrid'], ['remote', 'Remote']].map(([v, l]) =>
      h('option', { value: v, text: l })));
  const expMin = h('input', { class: 'input', type: 'number', min: '0', max: '50',
    id: 'f-expmin', placeholder: 'From', 'aria-label': 'Minimum years of experience' });
  const expMax = h('input', { class: 'input', type: 'number', min: '0', max: '50',
    id: 'f-expmax', placeholder: 'To', 'aria-label': 'Maximum years of experience' });

  const pipeline = pipelineBuilder(null);
  const btn = h('button', { class: 'btn btn-primary', type: 'submit', text: 'Create role' });

  const form = h('form', { class: 'col gap6', novalidate: true, onSubmit: async (e) => {
    e.preventDefault();
    if (!title.value) { title.setError('Give the role a title so candidates can find it'); title.input.focus(); return; }

    btn.disabled = true;
    btn.replaceChildren(h('span', { class: 'spin' }), 'Creating…');
    try {
      const job = await Api.employer.createJob({
        title: title.value, department: dept.value || null,
        location: loc.value || null, country: country.value || null,
        remote_mode: mode.value, jd_text: jd.value,
        min_experience_years: expMin.value === '' ? null : Number(expMin.value),
        max_experience_years: expMax.value === '' ? null : Number(expMax.value),
        stages: pipeline.value(),
      });
      toast('Role created as a draft.');
      go(`/employer/jobs/${job.id}`);
    } catch (err) {
      toast(err.message, 'err');
      btn.disabled = false;
      btn.replaceChildren('Create role');
    }
  } }, [
    h('div', { class: 'card card-pad col gap5' }, [
      title.node,
      h('div', { class: 'row gap4 wrap' }, [
        h('div', { class: 'grow' }, [dept.node]),
        h('div', { class: 'grow' }, [loc.node]),
        h('div', { class: 'grow' }, [country.node]),
      ]),
      h('div', { class: 'row gap4 wrap' }, [
        h('div', { class: 'field grow' }, [
          h('label', { class: 'label', for: 'f-mode', text: 'Work mode' }), mode,
        ]),
        h('div', { class: 'field grow' }, [
          h('span', { class: 'label', text: 'Experience (years)' }),
          h('div', { class: 'row gap2' }, [expMin, expMax]),
          h('p', { class: 'hint', text: 'Leave blank to take whatever the JD states.' }),
        ]),
      ]),
    ]),
    h('div', { class: 'card card-pad col gap3' }, [
      h('label', { class: 'label', for: 'f-jd', text: 'Job description' }),
      jd,
      h('p', { class: 'hint', text: 'This text grounds the interview. The panel asks about what is written here — vague JDs produce vague interviews.' }),
    ]),
    h('div', { class: 'card card-pad col gap5' }, [
      h('div', { class: 'col gap2' }, [
        h('h2', { style: { fontSize: 'var(--fs-16)' }, text: 'Hiring pipeline' }),
        h('p', { class: 'hint', text: 'Choose which stages the AI panel runs and which your team runs. You cannot change this once candidates are in the pipeline.' }),
      ]),
      pipeline.node,
    ]),
    h('div', { class: 'row gap3' }, [
      btn,
      h('a', { class: 'btn btn-ghost', href: '#/employer/jobs', text: 'Cancel' }),
    ]),
  ]);

  renderPreview();

  return h('div', { class: 'page' }, [
    pageHead('New role', 'Two things matter here: the title candidates search for, and the description the panel interviews against.'),
    h('div', { class: 'split-main tight' }, [
      form, preview,
    ]),
  ]);
}

/* ------------------------------------------------------------ role detail */
export function jobDetail({ id }) {
  const host = h('div', { class: 'page' }, [skeletonRows(4, 96)]);

  (async () => {
    try {
      const [job, applicants] = await Promise.all([
        Api.employer.getJob(id),
        Api.employer.applications(id).catch(() => []),
      ]);
      clear(host).append(renderJob(job, applicants));
    } catch (err) {
      clear(host).append(errorPanel(err, () => go('/employer/jobs')));
    }
  })();

  return host;
}

function renderJob(job, applicants) {
  const aiStage = job.stages.find((s) => s.kind === 'ai_interview');
  const cfg = aiStage?.interview_config_json || {};

  const actions = [];
  if (job.status === 'draft' || job.status === 'paused') {
    actions.push(h('button', {
      class: 'btn btn-primary', type: 'button',
      onClick: (e) => publish(e.currentTarget, job.id),
      text: job.status === 'draft' ? 'Publish role' : 'Reopen role',
    }));
  }
  if (job.status === 'open') {
    actions.push(h('button', {
      class: 'btn', type: 'button', text: 'Pause',
      onClick: async (e) => { e.currentTarget.disabled = true; await Api.employer.pauseJob(job.id); toast('Role paused. It is off the public board.'); go(`/employer/jobs/${job.id}`); location.reload(); },
    }));
  }
  if (job.status !== 'closed') {
    actions.push(h('button', {
      class: 'btn btn-danger', type: 'button', text: 'Close',
      onClick: () => modal({
        title: 'Close this role?',
        body: 'The role comes off the public board and stops accepting applications. Candidates already in the pipeline are not affected.',
        confirmLabel: 'Close role', danger: true,
        onConfirm: async () => { await Api.employer.closeJob(job.id); toast('Role closed.'); location.reload(); },
      }),
    }));
  }

  return h('div', { class: 'col gap8' }, [
    h('div', {}, [
      h('a', { class: 'backlink', href: '#/employer/jobs', style: { marginBottom: 'var(--s2)' } }, [
        h('span', { html: icon('arrowLeft', 15) }), 'All roles',
      ]),
      h('div', { class: 'row-between wrap gap5' }, [
        h('div', { class: 'col gap3' }, [
          h('div', { class: 'row gap3 wrap' }, [
            h('h1', { class: 'page-title display', text: job.title }),
            h('span', { class: `status status-${job.status}`, text: STATUS_LABEL[job.status] }),
          ]),
          h('div', { class: 'job-meta' }, [
            job.department && h('span', {}, [h('span', { html: icon('layers', 13) }), job.department]),
            job.location && h('span', {}, [h('span', { html: icon('pin', 13) }), job.location]),
            h('span', {}, [h('span', { html: icon('users', 13) }), `${job.applicant_count} applicant${job.applicant_count === 1 ? '' : 's'}`]),
            job.published_at && h('span', {}, [h('span', { html: icon('clock', 13) }), `Published ${fmtDate(job.published_at)}`]),
          ]),
        ]),
        h('div', { class: 'row gap3 wrap' }, actions),
      ]),
    ]),

    h('div', { class: 'split-main tight' }, [
      /* ---- left: applicants ---- */
      h('section', { class: 'col gap4' }, [
        h('h2', { style: { fontSize: 'var(--fs-16)' }, text: 'Applicants' }),
        applicants.length
          ? h('div', { class: 'table-wrap' }, [
              h('table', {}, [
                h('thead', {}, [h('tr', {}, [
                  h('th', { text: 'Candidate' }), h('th', { text: 'Status' }),
                  h('th', { text: 'Applied' }), h('th', {}, [h('span', { class: 'sr-only', text: 'Actions' })]),
                ])]),
                h('tbody', {}, applicants.map((a) => h('tr', {}, [
                  h('td', {}, [h('div', { class: 'row gap3' }, [
                    h('span', { class: 'avatar', text: initials(a.full_name) }),
                    h('div', { class: 'col' }, [
                      h('strong', { text: a.full_name }),
                      h('span', { class: 'fs12 t3', text: a.headline || a.email }),
                    ]),
                  ])]),
                  h('td', {}, [h('span', { class: 'status status-applied', text: 'Applied' })]),
                  h('td', { class: 'fs13 t2', text: relTime(a.applied_at) }),
                  h('td', { style: { textAlign: 'right' } }, [
                    h('div', { class: 'row gap2', style: { justifyContent: 'flex-end' } }, [
                      h('a', { class: 'btn btn-sm', href: `#/employer/review/${a.id}`,
                        text: 'Review' }),
                      h('button', {
                        class: 'btn btn-sm btn-primary', type: 'button', text: 'Start interview',
                        onClick: (e) => startInterview(e.currentTarget, a.id),
                      }),
                    ]),
                  ]),
                ]))),
              ]),
            ])
          : h('div', { class: 'card' }, [empty({
              iconName: 'users',
              title: job.status === 'open' ? 'No applicants yet' : 'Publish to start receiving applications',
              body: job.status === 'open'
                ? 'This role is live on the candidate board. Applicants will appear here as they apply.'
                : 'This role is not on the public board yet, so candidates cannot find or apply to it.',
            })]),
      ]),

      /* ---- right: pipeline + grounding ---- */
      h('aside', { class: 'col gap6' }, [
        h('section', { class: 'col gap4' }, [
          h('h2', { style: { fontSize: 'var(--fs-16)' }, text: 'Pipeline' }),
          h('div', { class: 'pipeline' }, job.stages.map((stage) => h('div', { class: 'stage' }, [
            h('span', { class: 'stage-seq', text: String(stage.seq) }),
            h('div', { class: 'col gap3' }, [
              h('div', { class: 'row-between gap3' }, [
                h('strong', { text: stage.name }),
                h('span', { class: 'chip chip-mono', text: stage.kind === 'ai_interview' ? 'AI' : 'HUMAN' }),
              ]),
              stage.kind === 'ai_interview' ? h('div', { class: 'col gap3' }, [
                h('div', { class: 'row wrap gap2' },
                  (stage.interview_config_json?.panel || []).map(personaChip)),
                h('div', { class: 'row gap3' }, [
                  h('span', { class: 'fs12 t3', text: 'Starting difficulty' }),
                  difficultyGauge(stage.interview_config_json?.start_difficulty || 3),
                ]),
              ]) : h('p', { class: 'hint', text: 'A member of your team runs this round.' }),
            ]),
          ]))),
        ]),

        h('section', { class: 'col gap4' }, [
          h('h2', { style: { fontSize: 'var(--fs-16)' }, text: 'Interview grounding' }),
          h('div', { class: 'card card-pad col gap4' }, [
            h('p', { class: 'hint', text: 'The panel interviews against these skills, extracted from the job description.' }),
            (job.required_skills_json || []).length
              ? h('div', { class: 'skills' }, job.required_skills_json.map((s) => h('span', { class: 'chip', text: s })))
              : h('p', { class: 'fs13 t3', text: 'No skills detected. Add detail to the job description so the panel has something specific to probe.' }),
          ]),
        ]),
      ]),
    ]),
  ]);
}

async function startInterview(btn, applicationId) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.replaceChildren(h('span', { class: 'spin' }), 'Starting…');
  try {
    const s = await Api.employer.startInterview(applicationId);
    toast(`${s.stage_name} ready — opening the live monitor.`);
    // Straight to the monitor: the employer's next action is to watch it.
    go(`/employer/monitor/${s.session_id}`);
  } catch (err) {
    toast(err.message, 'err');
    btn.disabled = false;
    btn.replaceChildren(original);
  }
}


async function publish(btn, id) {
  btn.disabled = true;
  btn.replaceChildren(h('span', { class: 'spin' }), 'Publishing…');
  try {
    await Api.employer.publishJob(id);
    toast('Role is live on the candidate board.');
    location.reload();
  } catch (err) {
    toast(err.message, 'err');
    btn.disabled = false;
    btn.replaceChildren('Publish role');
  }
}

/* ------------------------------------------------------------- small bits */
function input({ name, label, required, placeholder, hint }) {
  const el = h('input', { class: 'input', id: `f-${name}`, name, placeholder, required: required || null });
  const err = h('p', { class: 'err', role: 'alert', hidden: true });
  return {
    node: h('div', { class: 'field' }, [
      h('label', { class: 'label', for: `f-${name}` }, [label, required ? h('span', { class: 'req', text: '*' }) : null]),
      el, hint ? h('p', { class: 'hint', text: hint }) : null, err,
    ]),
    input: el,
    get value() { return el.value.trim(); },
    setError(m) {
      err.hidden = !m;
      el.classList.toggle('invalid', Boolean(m));
      if (m) err.replaceChildren(h('span', { html: icon('alert', 13) }), h('span', { text: m }));
    },
  };
}

/* Client-side mirror of the server lexicon — preview only; the server is authoritative. */
const PREVIEW_LEXICON = {
  Python: ['python'], Java: ['java'], Go: ['golang'], TypeScript: ['typescript'],
  JavaScript: ['javascript'], React: ['react'], 'Node.js': ['node.js', 'nodejs'],
  FastAPI: ['fastapi'], Django: ['django'], SQL: ['sql', 'postgres', 'mysql'],
  Redis: ['redis'], Kafka: ['kafka'], Docker: ['docker'], Kubernetes: ['kubernetes', 'k8s'],
  AWS: ['aws'], GCP: ['gcp', 'google cloud'], Azure: ['azure'], Terraform: ['terraform'],
  'CI/CD': ['ci/cd'], 'System Design': ['system design', 'distributed systems', 'scalability'],
  Microservices: ['microservices'], 'API Design': ['rest api', 'api design', 'graphql'],
  'Machine Learning': ['machine learning', 'deep learning'],
  Observability: ['observability', 'monitoring'], Security: ['security'],
  'Product Sense': ['product sense', 'roadmap'], 'Stakeholder Management': ['stakeholder', 'cross-functional'],
  Mentorship: ['mentor'], 'Incident Response': ['on-call', 'incident'], WebRTC: ['webrtc'],
};

function detectSkills(text) {
  const hay = (text || '').toLowerCase();
  const out = [];
  for (const [name, forms] of Object.entries(PREVIEW_LEXICON)) {
    if (forms.some((f) => hay.includes(f))) out.push(name);
  }
  return out.slice(0, 12);
}

function errorPanel(err, retry) {
  return h('div', { class: 'card' }, [empty({
    iconName: 'warning',
    title: 'Could not load this',
    body: err?.message || 'Something went wrong.',
    action: h('button', { class: 'btn', type: 'button', text: 'Try again', onClick: retry }),
  })]);
}
