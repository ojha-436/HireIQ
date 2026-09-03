/* ============================================================================
   views/monitor.js — the employer live monitor.

   This is the screen that makes the engine legible. Four columns:
     panel roster + difficulty · streaming transcript · Panel Memory · trace rail

   Panel Memory is PS11 requirement #3 rendered. Shared context is otherwise an
   article of faith; here you watch a fact stated to one interviewer appear in the
   next interviewer's briefing.

   Deliberately absent: any scenario label on the CANDIDATE's screen. The persona
   prompt works hard not to announce a role-play, so the candidate UI must not
   either. The employer sees it; the candidate just meets an unhappy customer.
   ============================================================================ */

import { API_BASE } from '../api.js';
import { Store } from '../store.js';
import { clear, empty, h, icon, PERSONA_LABEL, toast } from '../ui.js';

const RULE_TEXT = {
  R1: 'Contradiction — same interviewer clarifies',
  R2: 'Correct but no impact — product challenges',
  R3: 'Vague — press for specifics',
  R4: 'Unsubstantiated claim — hiring manager',
  R5: 'Rotating the floor',
  R6: 'Jargon — customer asks for plain language',
  R7: 'Role-play scenario',
  W0: 'Your whispered question',
  R0: 'No rule fired — continuing',
  tiebreak: 'Model tiebreak (clamped)',
};

export function monitorView({ id }) {
  const roster = h('div', { class: 'col gap3' });
  const transcript = h('div', { class: 'transcript', 'aria-live': 'polite' });
  const memory = h('div', { class: 'col gap5' });
  const rail = h('div', { class: 'col gap2' });
  const statusChip = h('span', { class: 'status status-draft', text: 'Connecting' });

  let panel = [];
  let speaking = null;
  const turns = [];

  /* ---- whisper (W0) ---- */
  const whisperBox = h('input', {
    class: 'input', id: 'whisper', placeholder: 'Ask the panel something…',
    'aria-label': 'Whisper a question to the panel',
  });
  const whisperBtn = h('button', { class: 'btn btn-sm', type: 'button' }, [
    h('span', { html: icon('activity', 14) }), 'Whisper',
  ]);
  const whisperNote = h('p', { class: 'hint', role: 'status' });

  whisperBtn.onclick = async () => {
    const text = whisperBox.value.trim();
    if (!text) { whisperNote.textContent = 'Type the question first.'; return; }
    whisperBtn.disabled = true;
    try {
      const res = await fetch(`${API_BASE}/api/employer/whisper/${id}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${Store.token('employer')}`,
        },
        body: JSON.stringify({ text }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || 'Could not whisper');
      whisperBox.value = '';
      whisperNote.textContent = 'Queued — it will be asked at the next handoff.';
      whisperNote.style.color = 'var(--text-3)';
      toast('Question queued for the panel.');
    } catch (err) {
      whisperNote.textContent = err.message;
      whisperNote.style.color = 'var(--danger)';
    } finally {
      whisperBtn.disabled = false;
    }
  };
  whisperBox.onkeydown = (e) => { if (e.key === 'Enter') whisperBtn.click(); };

  /* ---- renderers ---- */
  function setLive() {
    if (statusChip.textContent === 'Live') return;
    statusChip.className = 'status status-open';
    statusChip.textContent = 'Live';
  }

  function renderRoster() {
    clear(roster).append(...panel.map((p) => {
      const on = p.key === speaking;
      return h('div', { class: `mon-tile p-${p.key}${on ? ' speaking' : ''}` }, [
        h('span', { class: 'tally', 'aria-hidden': 'true' }),
        h('div', { class: 'col grow', style: { minWidth: 0 } }, [
          h('strong', { class: 'fs13 truncate', text: p.label || PERSONA_LABEL[p.key] || p.key }),
          h('span', { class: 'fs11 t3', text: on ? 'Speaking' : 'Listening' }),
        ]),
      ]);
    }));
  }

  function renderTranscript() {
    clear(transcript).append(...turns.map((t) => {
      const me = t.speaker === 'candidate';
      const label = me ? 'Candidate'
        : (panel.find((p) => p.key === t.speaker)?.label || PERSONA_LABEL[t.speaker] || t.speaker);
      return h('div', { class: `line ${me ? 'me' : `p-${t.speaker}`}` }, [
        h('span', { class: 'line-who fs11 mono', text: label.toUpperCase() }),
        h('p', { class: 'fs13', text: t.text || '…' }),
      ]);
    }));
    transcript.scrollTop = transcript.scrollHeight;
  }

  function renderMemory(m) {
    const section = (title, body, count) => h('div', { class: 'col gap3' }, [
      h('div', { class: 'row-between' }, [
        h('h3', { class: 'filter-title', text: title }),
        count !== undefined ? h('span', { class: 'chip chip-mono', text: String(count) }) : null,
      ]),
      body,
    ]);

    clear(memory).append(
      m.scenario ? h('div', { class: 'mon-scenario' }, [
        h('span', { class: 'fs11 mono', style: { letterSpacing: '.06em' }, text: 'ROLE-PLAY LIVE' }),
        h('strong', { class: 'fs13', text: m.scenario.title }),
        h('span', { class: 'fs11 t3', text: `${PERSONA_LABEL[m.scenario.persona] || m.scenario.persona} · turn ${m.scenario.turns}` }),
      ]) : null,

      section('Difficulty', h('div', { class: 'row gap3' }, [
        h('span', { class: 'diff-bars', 'aria-hidden': 'true' },
          [1, 2, 3, 4, 5].map((n) => h('i', { class: n <= m.difficulty.level ? 'on' : '' }))),
        h('span', { class: 'fs12 mono t2', text: `L${m.difficulty.level} · rolling ${m.difficulty.rolling}` }),
      ])),

      section('Established facts',
        m.facts.length
          ? h('div', { class: 'col gap2' }, m.facts.map((f) => h('div', { class: 'mon-fact' }, [
              h('span', { class: 'fs11 mono t3', text: f.key }),
              h('strong', { class: 'fs13', text: String(f.value) }),
            ])))
          : h('p', { class: 'hint', text: 'Nothing numeric established yet.' }),
        m.facts.length),

      section('Open threads',
        m.open_threads.length
          ? h('div', { class: 'col gap2' }, m.open_threads.map((t) => h('div', { class: 'mon-thread' }, [
              h('span', { class: `chip chip-mono flag-${t.kind}`, text: t.kind }),
              h('span', { class: 'fs12 t2', text: t.note }),
            ])))
          : h('p', { class: 'hint', text: 'No unresolved concerns.' }),
        m.open_threads.length),

      section('Coverage',
        Object.keys(m.coverage).length
          ? h('div', { class: 'col gap2' }, Object.entries(m.coverage).map(([skill, level]) =>
              h('div', { class: 'row-between gap3' }, [
                h('span', { class: 'fs12 t2 truncate', text: skill }),
                h('span', { class: 'fs12 mono t3', text: String(level) }),
              ])))
          : h('p', { class: 'hint', text: 'No skill probed yet.' })),

      m.briefing ? section('Next interviewer’s briefing',
        h('pre', { class: 'mon-briefing', text: m.briefing })) : null,
    );
  }

  function pushTrace(ev) {
    const node = h('div', { class: 'trace-row' }, [
      h('span', { class: `trace-rule rule-${ev.rule || 'na'}`, text: ev.rule || '—' }),
      h('div', { class: 'col grow', style: { minWidth: 0 } }, [
        h('span', { class: 'fs12', text: RULE_TEXT[ev.rule] || ev.reason || '' }),
        h('span', { class: 'fs11 t3 truncate', text: `→ ${PERSONA_LABEL[ev.next] || ev.next} · ${ev.intent} · L${ev.difficulty}` }),
      ]),
    ]);
    rail.prepend(node);
    while (rail.childElementCount > 40) rail.lastElementChild.remove();
  }

  /* ---- the stream ---- */
  const source = new EventSource(
    `${API_BASE}/api/employer/monitor/${id}?token=${encodeURIComponent(Store.token('employer'))}`,
  );
  source.onmessage = (e) => {
    let ev; try { ev = JSON.parse(e.data); } catch { return; }
    switch (ev.type) {
      case 'hello':
      case 'session':
        panel = ev.panel || panel;
        statusChip.className = `status status-${ev.status === 'live' ? 'open' : 'draft'}`;
        statusChip.textContent = ev.status === 'live' ? 'Live' : (ev.status || 'Waiting');
        renderRoster();
        break;
      case 'floor':
        // The `hello` snapshot is taken before the candidate joins, so the first floor
        // grant is the real signal that this interview is under way.
        setLive();
        speaking = ev.persona; renderRoster(); break;
      case 'transcript': {
        const last = turns[turns.length - 1];
        if (last && last.speaker === ev.speaker && !last.final) {
          last.text = ev.text; last.final = ev.final;
        } else {
          turns.push({ speaker: ev.speaker, text: ev.text, final: ev.final });
        }
        renderTranscript();
        break;
      }
      case 'panel_memory': renderMemory(ev); break;
      case 'trace': pushTrace(ev); break;
      case 'ended':
        statusChip.className = 'status status-closed';
        statusChip.textContent = 'Ended';
        source.close();
        break;
      default: break;
    }
  };
  source.onerror = () => {
    statusChip.className = 'status status-paused';
    statusChip.textContent = 'Reconnecting';
  };

  renderMemory({ facts: [], open_threads: [], coverage: {},
                 difficulty: { level: 3, rolling: 3 }, turns_by_persona: {},
                 scenario: null, briefing: '' });

  return h('div', { class: 'page', style: { maxWidth: 'none' } }, [
    h('header', { class: 'row-between wrap gap5', style: { marginBottom: 'var(--s6)' } }, [
      h('div', { class: 'col gap2' }, [
        h('div', { class: 'row gap3 wrap' }, [
          h('h1', { class: 'page-title display', style: { fontSize: 'var(--fs-28)' }, text: 'Live monitor' }),
          statusChip,
        ]),
        h('p', { class: 'fs13 t3', text: 'Read-only. Whispering a question is the only way this screen can influence the interview, and every whisper is audited.' }),
      ]),
      h('a', { class: 'btn btn-ghost', href: '#/employer/jobs', text: 'Back to roles' }),
    ]),

    h('div', { class: 'monitor' }, [
      h('aside', { class: 'mon-col' }, [
        h('h2', { class: 'filter-title', text: 'Panel' }), roster,
        h('hr', { class: 'hr' }),
        h('div', { class: 'col gap3' }, [
          h('h2', { class: 'filter-title', text: 'Whisper a question (W0)' }),
          whisperBox,
          whisperBtn,
          whisperNote,
        ]),
      ]),
      h('section', { class: 'mon-col mon-transcript' }, [
        h('h2', { class: 'filter-title', text: 'Transcript' }), transcript,
      ]),
      h('aside', { class: 'mon-col' }, [
        h('h2', { class: 'filter-title', text: 'Panel memory' }), memory,
      ]),
      h('aside', { class: 'mon-col' }, [
        h('h2', { class: 'filter-title', text: 'Rule trace' }),
        rail,
        h('p', { class: 'hint', text: 'Newest first. Every handoff names the rule that caused it.' }),
      ]),
    ]),
  ]);
}
