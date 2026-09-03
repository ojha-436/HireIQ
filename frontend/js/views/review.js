/* ============================================================================
   views/review.js — the employer's assessment review screen.

   The whole product argument lives on this page: every score is arithmetic and
   every line of feedback names the transcript turn it came from. So the design
   makes citations the primary interaction — click a line, the transcript scrolls
   and highlights the exact words. A claim you cannot trace is a claim you should
   not act on.
   ============================================================================ */

import { Api } from '../api.js';
import { go } from '../router.js';
import { clear, empty, fmtDate, h, icon, modal, skeletonRows, toast } from '../ui.js';

const BAND_CLASS = {
  'well above bar': 'band-high', 'above bar': 'band-high',
  'at bar': 'band-mid', 'below bar': 'band-low', 'well below bar': 'band-low',
};
const REC_LABEL = {
  strong_yes: 'Strong yes', yes: 'Yes', lean_yes: 'Lean yes',
  lean_no: 'Lean no', no: 'No', strong_no: 'Strong no', no_decision: 'No decision',
};

/** One answer is scored on every dimension it touches, so evidence lines repeat the
    same quote. Group them: the quote is the unit a reviewer reads. */
function groupEvidence(lines) {
  const byQuote = new Map();
  for (const line of lines) {
    const key = line.quote || line.turn_ids.join(',');
    if (!byQuote.has(key)) {
      byQuote.set(key, { quote: line.quote, turn_ids: line.turn_ids, dims: [] });
    }
    const g = byQuote.get(key);
    if (!g.dims.some((d) => d.dimension === line.dimension)) {
      g.dims.push({ dimension: line.dimension, score: line.score });
    }
  }
  return [...byQuote.values()];
}

export function reviewView({ id }) {
  const host = h('div', { class: 'page' }, [skeletonRows(4, 120)]);

  (async () => {
    try {
      clear(host).append(render(await Api.employer.assessment(id), id));
    } catch (err) {
      clear(host).append(h('div', { class: 'card' }, [empty({
        iconName: 'warning', title: 'Could not load the assessment', body: err.message,
        action: h('a', { class: 'btn', href: '#/employer/jobs', text: 'Back to roles' }),
      })]));
    }
  })();

  return host;
}

function render(data, applicationId) {
  const rounds = data.rounds || [];
  const latest = [...rounds].reverse().find((r) => r.assessment) || rounds[0];
  const turnNodes = new Map();

  /* ---- transcript, with anchors the evidence lines jump to ---- */
  const transcript = h('div', { class: 'rv-transcript' },
    (latest?.transcript || []).map((t) => {
      const me = t.speaker === 'candidate';
      const node = h('div', { class: `rv-turn ${me ? 'me' : `p-${t.speaker}`}`, id: `turn-${t.id}` }, [
        h('div', { class: 'row gap2 wrap' }, [
          h('span', { class: 'fs11 mono', text: (me ? 'CANDIDATE' : t.speaker).toUpperCase() }),
          t.rule_fired ? h('span', { class: 'chip chip-mono', text: t.rule_fired }) : null,
          t.difficulty ? h('span', { class: 'chip chip-mono', text: `L${t.difficulty}` }) : null,
          ...(t.flags || []).map((f) => h('span', { class: `chip chip-mono flag-${f}`, text: f })),
        ]),
        h('p', { class: 'fs13', text: t.text || '—' }),
      ]);
      turnNodes.set(t.id, node);
      return node;
    }));

  function jumpTo(turnIds) {
    let first = null;
    turnNodes.forEach((node) => node.classList.remove('cited'));
    for (const tid of turnIds) {
      const node = turnNodes.get(tid);
      if (!node) continue;
      node.classList.add('cited');
      first = first || node;
    }
    if (first) first.scrollIntoView({ behavior: 'smooth', block: 'center' });
    else toast('That quote predates the 60-day transcript window.', 'err');
  }

  /* ---- decision actions ---- */
  const decide = (kind) => {
    const reasonBox = h('textarea', {
      class: 'textarea', id: 'decision-reason', style: { minHeight: '96px' },
      placeholder: kind === 'reject'
        ? 'Why is this candidate not moving forward? Stored on the record.'
        : 'Why are they moving forward, and what should the next round probe?',
    });
    const release = h('input', { type: 'checkbox', checked: true });

    modal({
      title: kind === 'reject' ? 'Reject this candidate?' : 'Advance to the next stage?',
      danger: kind === 'reject',
      confirmLabel: kind === 'reject' ? 'Reject' : 'Advance',
      body: h('div', { class: 'col gap4' }, [
        h('div', { class: 'field' }, [
          h('label', { class: 'label', for: 'decision-reason' }, [
            'Reason', h('span', { class: 'req', text: '*' }),
          ]),
          reasonBox,
          h('p', { class: 'hint', text: 'Required, immutable, and attributed to you in the audit log. It is not shown to the candidate.' }),
        ]),
        h('label', { class: 'check' }, [
          release,
          h('span', { class: 'fs13', text: 'Release the evidence-linked feedback to the candidate' }),
        ]),
        h('p', { class: 'hint', text: kind === 'reject'
          ? 'For a rejection this is normally on: they have nothing left to game, and being told why is the point.'
          : 'Leave off if you would rather they not see scores before the next round.' }),
      ]),
      onConfirm: async () => {
        const reason = reasonBox.value.trim();
        if (!reason) { toast('A reason is required.', 'err'); throw new Error('no reason'); }
        const body = { reason, release_feedback: release.checked };
        const res = kind === 'reject'
          ? await Api.employer.reject(applicationId, body)
          : await Api.employer.advance(applicationId, body);
        toast(kind === 'reject'
          ? 'Rejected. Reason recorded.'
          : `Advanced${res.moved_to ? ` to ${res.moved_to}` : ''}.`);
        go(`/employer/review/${applicationId}`);
        location.reload();
      },
    });
  };

  const decided = ['rejected', 'offer'].includes(data.application.status);
  const a = latest?.assessment;

  return h('div', { class: 'col gap8' }, [
    /* ---- header ---- */
    h('div', {}, [
      h('a', { class: 'backlink', href: `#/employer/jobs/${data.job?.id || ''}` }, [
        h('span', { html: icon('arrowLeft', 15) }), 'Back to role',
      ]),
      h('div', { class: 'row-between wrap gap5', style: { marginTop: 'var(--s2)' } }, [
        h('div', { class: 'col gap2' }, [
          h('h1', { class: 'page-title display', style: { fontSize: 'var(--fs-34)' },
            text: data.candidate?.full_name || 'Candidate' }),
          h('div', { class: 'job-meta' }, [
            h('span', { text: data.job?.title || '' }),
            data.candidate?.headline ? h('span', { text: data.candidate.headline }) : null,
            h('span', { text: `Applied ${fmtDate(data.application.applied_at)}` }),
            h('span', { class: `status status-${data.application.status === 'rejected' ? 'closed' : 'applied'}`,
              text: data.application.status.replace('_', ' ') }),
          ]),
        ]),
        decided
          ? h('div', { class: 'col gap2', style: { alignItems: 'flex-end' } }, [
              h('span', { class: 'fs12 t3', text: 'Decision recorded' }),
              h('p', { class: 'fs13 t2', style: { maxWidth: '40ch', textAlign: 'right' },
                text: data.application.decision_reason || '' }),
            ])
          : h('div', { class: 'row gap3 wrap' }, [
              h('button', { class: 'btn btn-primary', type: 'button', text: 'Advance',
                onClick: () => decide('advance') }),
              h('button', { class: 'btn btn-danger', type: 'button', text: 'Reject',
                onClick: () => decide('reject') }),
            ]),
      ]),
    ]),

    !a ? h('div', { class: 'card' }, [empty({
      iconName: 'inbox', title: 'No assessment yet',
      body: 'This application has no completed AI interview, so there is nothing to review.',
    })]) : h('div', { class: 'review' }, [

      /* ---- left: scores + evidence ---- */
      h('div', { class: 'col gap6', style: { minWidth: 0 } }, [
        h('div', { class: 'card card-pad col gap5' }, [
          h('div', { class: 'row gap8 wrap' }, [
            h('div', { class: 'col' }, [
              h('span', { class: 'filter-title', text: 'Overall' }),
              h('strong', { class: 'display', style: { fontSize: 'var(--fs-41)' },
                text: String(a.overall) }),
              h('span', { class: 'fs11 t3', text: 'arithmetic mean of dimension means' }),
            ]),
            h('div', { class: 'col' }, [
              h('span', { class: 'filter-title', text: 'Recommendation' }),
              h('strong', { class: 'fs19', style: { fontSize: 'var(--fs-19)' },
                text: REC_LABEL[a.recommendation] || a.recommendation }),
              h('span', { class: 'fs11 t3', text: 'advisory — you decide' }),
            ]),
            a.adaptivity && a.adaptivity.generated_pct !== null ? h('div', { class: 'col' }, [
              h('span', { class: 'filter-title', text: 'Adaptive' }),
              h('strong', { class: 'fs19', style: { fontSize: 'var(--fs-19)' },
                text: `${a.adaptivity.generated_pct}%` }),
              h('span', { class: 'fs11 t3',
                text: `${a.adaptivity.generated + a.adaptivity.scenario} of ${a.adaptivity.total} questions came from her answers` }),
            ]) : null,
            h('div', { class: 'col' }, [
              h('span', { class: 'filter-title', text: 'Percentile' }),
              h('strong', { class: 'fs19', style: { fontSize: 'var(--fs-19)' },
                text: a.percentile === null || a.percentile === undefined ? '—' : `${a.percentile}th` }),
              h('span', { class: 'fs11 t3',
                text: a.percentile_n >= 5 ? `n=${a.percentile_n}` : `hidden until n≥5 (n=${a.percentile_n})` }),
            ]),
          ]),
          a.summary ? h('p', { class: 'fs14 t2', text: a.summary }) : null,
        ]),

        h('section', { class: 'col gap4' }, [
          h('h2', { style: { fontSize: 'var(--fs-16)' }, text: 'Dimensions' }),
          h('div', { class: 'col gap3' }, (a.dimensions || []).map((d) => h('div', { class: 'rv-dim' }, [
            h('div', { class: 'row-between gap3' }, [
              h('strong', { class: 'fs13', text: d.dimension }),
              h('span', { class: `chip ${BAND_CLASS[d.band] || ''}`, text: `${d.score} · ${d.band}` }),
            ]),
            h('p', { class: 'fs12 t2', text: d.verdict || '' }),
            h('span', { class: 'fs11 t3', text: `probed ${d.times_probed || 0}×` }),
          ]))),
        ]),

        a.contradictions?.length ? h('section', { class: 'col gap4' }, [
          h('h2', { style: { fontSize: 'var(--fs-16)' }, text: 'Contradictions' }),
          h('div', { class: 'col gap2' }, a.contradictions.map((cx) => h('div', { class: 'mon-thread' }, [
            h('span', { class: 'chip chip-mono flag-contradiction', text: 'contradiction' }),
            h('span', { class: 'fs12 t2', text: cx.note || cx.claim || String(cx) }),
          ]))),
        ]) : null,

        h('section', { class: 'col gap4' }, [
          h('div', { class: 'row-between' }, [
            h('h2', { style: { fontSize: 'var(--fs-16)' }, text: 'Evidence' }),
            h('span', { class: 'chip chip-mono', text: String((a.evidence || []).length) }),
          ]),
          h('p', { class: 'hint', text: 'Grouped by answer. Click one to jump to the words it came from. Uncited lines are dropped server-side and never appear here.' }),
          h('div', { class: 'col gap2' }, groupEvidence(a.evidence || []).map((g) => h('button', {
            class: 'rv-evidence', type: 'button',
            onClick: () => jumpTo(g.turn_ids),
          }, [
            h('div', { class: 'row gap2 wrap' }, [
              ...g.dims.map((d) => h('span', { class: 'chip chip-mono',
                text: `${d.dimension} ${d.score}` })),
              h('span', { class: 't3', html: icon('arrowRight', 12) }),
            ]),
            h('p', { class: 'rv-quote fs13', text: `“${g.quote}”` }),
          ]))),
        ]),
      ]),

      /* ---- right: transcript ---- */
      h('aside', { class: 'card col gap3', style: { padding: 'var(--s5)', minWidth: 0 } }, [
        h('div', { class: 'row-between' }, [
          h('h2', { class: 'filter-title', text: 'Transcript' }),
          h('span', { class: 'chip chip-mono', text: `${(latest.transcript || []).length} turns` }),
        ]),
        h('p', { class: 'hint', text: `${latest.stage_name} · consent recorded ${fmtDate(latest.disclosure_accepted_at)}` }),
        transcript,
      ]),
    ]),

    /* ---- audit ---- */
    data.audit?.length ? h('section', { class: 'col gap3' }, [
      h('h2', { style: { fontSize: 'var(--fs-16)' }, text: 'Audit trail' }),
      h('div', { class: 'col gap2' }, data.audit.map((e) => h('div', { class: 'row gap3 fs12' }, [
        h('span', { class: 'chip chip-mono', text: e.action }),
        h('span', { class: 't3', text: fmtDate(e.at) }),
        h('span', { class: 't2 grow truncate', text: e.payload?.reason || '' }),
      ]))),
    ]) : null,
  ]);
}
