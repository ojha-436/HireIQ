/* ============================================================================
   views/application.js — one application, from the candidate's side.

   The design principle: WHERE YOU STAND is never withheld. Scores and evidence
   are gated until the employer releases them, but a candidate left guessing
   whether they are still in the process is the exact failure this product exists
   to fix — so the stage timeline and the decision are always visible, and when
   feedback is pending the page says so rather than showing an empty panel.
   ============================================================================ */

import { Api } from '../api.js';
import { clear, empty, fmtDate, h, icon, relTime, skeletonRows } from '../ui.js';

const STATE_ICON = { done: 'check', active: 'activity', upcoming: 'clock', closed: 'x' };
const BAND_CLASS = {
  'well above bar': 'band-high', 'above bar': 'band-high',
  'at bar': 'band-mid', 'below bar': 'band-low', 'well below bar': 'band-low',
};

export function applicationDetail({ id }) {
  const host = h('div', {}, [skeletonRows(3, 120)]);

  (async () => {
    try {
      clear(host).append(render(await Api.candidate.application(id)));
    } catch (err) {
      clear(host).append(h('div', { class: 'card' }, [empty({
        iconName: 'warning', title: 'Could not load this application', body: err.message,
        action: h('a', { class: 'btn', href: '#/candidate/applications', text: 'My applications' }),
      })]));
    }
  })();

  return host;
}

function render(d) {
  const rejected = d.status === 'rejected';

  return h('div', { class: 'col gap8' }, [
    h('a', { class: 'backlink', href: '#/candidate/applications' }, [
      h('span', { html: icon('arrowLeft', 15) }), 'My applications',
    ]),

    /* ---- header: where you stand, always ---- */
    h('div', { class: 'col gap4' }, [
      h('span', { class: 'fs13 t2 row gap2' }, [
        h('span', { html: icon('building', 14) }), d.company_name,
      ]),
      h('h1', { class: 'display', style: { fontSize: 'var(--fs-34)' },
        text: d.job?.title || 'Application' }),
      h('div', { class: 'row gap4 wrap' }, [
        h('span', { class: `status status-${rejected ? 'closed' : (d.status === 'offer' ? 'open' : 'applied')}`,
          text: d.status_label }),
        h('span', { class: 'fs12 t3', text: `Applied ${fmtDate(d.applied_at)}` }),
        h('span', { class: 'fs12 t3', text: `Updated ${relTime(d.last_activity_at)}` }),
      ]),
      d.status_blurb ? h('p', { class: 't2', style: { maxWidth: '62ch' }, text: d.status_blurb }) : null,
    ]),

    h('div', { class: 'split-main' }, [
      h('div', { class: 'col gap8' }, [

        /* ---- feedback, or an honest account of why there is none ---- */
        d.feedback.length
          ? h('section', { class: 'col gap5' }, [
              h('h2', { style: { fontSize: 'var(--fs-19)' }, text: 'Your feedback' }),
              ...d.feedback.map(feedbackCard),
            ])
          : h('section', { class: 'col gap4' }, [
              h('h2', { style: { fontSize: 'var(--fs-19)' }, text: 'Your feedback' }),
              h('div', { class: 'card' }, [empty({
                iconName: d.feedback_pending ? 'clock' : 'file',
                title: d.feedback_pending
                  ? 'Feedback is written and awaiting review'
                  : 'No feedback yet',
                body: d.feedback_pending
                  ? 'Your interview has been assessed. The hiring team releases feedback after they review it — that gap is deliberate, so nobody can tune their answers between rounds. You will see every point with the exact words it came from.'
                  : 'Once you complete an interview and the hiring team reviews it, your feedback appears here — every point linked to what you actually said.',
              })]),
            ]),
      ]),

      /* ---- the timeline ---- */
      h('aside', { class: 'col gap5 aside-sticky' }, [
        h('div', { class: 'card card-pad col gap4' }, [
          h('h3', { class: 'filter-title', text: 'Where you are' }),
          h('ol', { class: 'app-timeline' }, (d.timeline || []).map((st) => h('li', {
            class: `tl-step tl-${st.state}`,
          }, [
            h('span', { class: 'tl-dot', html: icon(STATE_ICON[st.state] || 'clock', 12) }),
            h('div', { class: 'col gap1', style: { minWidth: 0 } }, [
              h('strong', { class: 'fs13', text: st.name }),
              h('span', { class: 'fs11 t3', text: st.kind === 'ai_interview' ? 'AI panel' : 'With the hiring team' }),
              st.panel?.length
                ? h('div', { class: 'row wrap gap1', style: { marginTop: '2px' } },
                    st.panel.map((p) => h('span', { class: `persona p-${p.key}`, style: { height: '20px', fontSize: 'var(--fs-11)' }, text: p.label })))
                : null,
              st.state === 'active' && st.session_id && st.session_status !== 'ended'
                ? h('a', { class: 'btn btn-sm btn-primary', style: { marginTop: 'var(--s2)' },
                    href: `#/candidate/interview/${st.session_id}`, text: 'Join interview' })
                : null,
            ]),
          ]))),
        ]),

        h('div', { class: 'card card-pad col gap3' }, [
          h('span', { class: 'ai-badge' }, [h('span', { html: icon('activity', 12) }), 'AI interview']),
          h('p', { class: 'hint', text: 'The panel rounds in this process are conducted by AI. Your transcript is kept for 60 days; your assessment is kept and you can dispute any dimension of it.' }),
        ]),
      ]),
    ]),
  ]);
}

/** Collapse evidence lines that quote the same answer into one block. */
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

/** Report entries are {claim, turn_ids, quote}; older rows may be plain strings. */
function claimText(entry) {
  if (typeof entry === 'string') return entry;
  return entry.claim || entry.note || entry.skill || '';
}

function feedbackCard(fb) {
  return h('div', { class: 'card card-pad col gap5' }, [
    h('div', { class: 'row-between wrap gap4' }, [
      h('div', { class: 'col gap1' }, [
        h('strong', { class: 'fs14', text: fb.stage_name }),
        h('span', { class: 'fs11 t3', text: `Released ${fmtDate(fb.released_at)}` }),
      ]),
      h('div', { class: 'row gap5' }, [
        h('div', { class: 'col', style: { alignItems: 'flex-end' } }, [
          h('span', { class: 'filter-title', text: 'Overall' }),
          h('strong', { class: 'display', style: { fontSize: 'var(--fs-28)' }, text: String(fb.overall) }),
        ]),
        fb.percentile !== null && fb.percentile !== undefined
          ? h('div', { class: 'col', style: { alignItems: 'flex-end' } }, [
              h('span', { class: 'filter-title', text: 'Percentile' }),
              h('strong', { class: 'fs19', style: { fontSize: 'var(--fs-19)' }, text: `${fb.percentile}th` }),
              h('span', { class: 'fs11 t3', text: `of ${fb.percentile_n}` }),
            ])
          : null,
      ]),
    ]),

    fb.summary ? h('p', { class: 't2', text: fb.summary }) : null,

    fb.dimensions?.length ? h('div', { class: 'col gap3' }, [
      h('h4', { class: 'filter-title', text: 'By dimension' }),
      ...fb.dimensions.map((d) => h('div', { class: 'rv-dim' }, [
        h('div', { class: 'row-between gap3' }, [
          h('strong', { class: 'fs13', text: d.dimension }),
          h('span', { class: `chip ${BAND_CLASS[d.band] || ''}`, text: `${d.score} · ${d.band}` }),
        ]),
        d.verdict ? h('p', { class: 'fs12 t2', text: d.verdict }) : null,
      ])),
    ]) : null,

    /* Every point traced to the candidate's own words — the whole promise.
       Grouped by quote: one answer is scored on every dimension it touches, so listing
       each pairing separately repeated the same sentence seven times. */
    fb.evidence?.length ? h('div', { class: 'col gap3' }, [
      h('h4', { class: 'filter-title', text: 'What this is based on' }),
      h('p', { class: 'hint', text: 'Each answer you gave, and the dimensions it was scored on. Nothing here is an impression.' }),
      ...groupByQuote(fb.evidence).map((g) => h('div', { class: 'fb-evidence' }, [
        h('p', { class: 'rv-quote fs13', text: `“${g.quote}”` }),
        h('div', { class: 'row gap2 wrap' }, g.dims.map((d) =>
          h('span', { class: 'chip chip-mono', text: `${d.dimension} ${d.score}` }))),
      ])),
    ]) : null,

    fb.focus_areas?.length ? h('div', { class: 'col gap2' }, [
      h('h4', { class: 'filter-title', text: 'Where to focus' }),
      h('ul', { class: 'col gap3' }, fb.focus_areas.map((f) => h('li', { class: 'col gap1' }, [
        h('div', { class: 'row gap2 fs13' }, [
          h('span', { html: icon('arrowRight', 13), style: { color: 'var(--text-3)' } }),
          h('strong', { text: claimText(f) }),
        ]),
        f.quote ? h('p', { class: 'fs12 t3', style: { paddingLeft: 'var(--s5)' },
          text: `Based on: “${String(f.quote).slice(0, 140)}”` }) : null,
      ]))),
    ]) : null,

    fb.ai_disclosure
      ? h('p', { class: 'hint', style: { borderTop: '1px solid var(--line)', paddingTop: 'var(--s3)' },
          text: fb.ai_disclosure })
      : null,
  ]);
}
