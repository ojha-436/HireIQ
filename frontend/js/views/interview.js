/* ============================================================================
   views/interview.js — the consent gate and the room mount.

   Layer 1 of the AI disclosure lives here: an explicit, timestamped acceptance
   the candidate must give before the socket will open. The server enforces the
   same gate (WS closes 4409), so this screen is the courtesy, not the control.
   ============================================================================ */

import { Api, API_BASE } from '../api.js';
import { go } from '../router.js';
import { Store } from '../store.js';
import { clear, empty, h, icon, skeletonRows, toast } from '../ui.js';

async function getSession(sessionId) {
  const res = await fetch(`${API_BASE}/api/candidate/sessions/${sessionId}`, {
    headers: { Authorization: `Bearer ${Store.token('candidate')}` },
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Interview not found');
  return res.json();
}

/* ---------------------------------------------------------- pending list */
export function pendingInterviews() {
  const host = h('div', {}, [skeletonRows(2, 96)]);

  (async () => {
    try {
      const res = await fetch(`${API_BASE}/api/candidate/me/interviews/pending`, {
        headers: { Authorization: `Bearer ${Store.token('candidate')}` },
      });
      const rows = await res.json();
      clear(host).append(
        rows.length
          ? h('div', { class: 'role-list stagger' }, rows.map((s) => h('div', { class: 'role-row' }, [
              h('div', { class: 'col gap2 grow' }, [
                h('h2', { text: s.job_title || 'Interview' }),
                h('div', { class: 'row wrap gap2' },
                  (s.panel || []).map((p) => h('span', { class: `persona p-${p.key}`, text: p.label }))),
              ]),
              h('a', {
                class: 'btn btn-primary', href: `#/candidate/interview/${s.session_id}`,
                text: s.disclosure_accepted ? 'Rejoin' : 'Start interview',
              }),
            ])))
          : h('div', { class: 'card' }, [empty({
              iconName: 'mic',
              title: 'No interviews scheduled',
              body: 'When a company starts an AI panel interview on one of your applications, it appears here.',
            })]),
      );
    } catch (err) {
      clear(host).append(h('div', { class: 'card' }, [empty({
        iconName: 'warning', title: 'Could not load interviews', body: err.message,
      })]));
    }
  })();

  return h('div', {}, [
    h('header', { class: 'col gap3', style: { marginBottom: 'var(--s8)' } }, [
      h('h1', { class: 'display', style: { fontSize: 'var(--fs-34)' }, text: 'Interviews' }),
      h('p', { class: 't2', text: 'Panel interviews waiting for you.' }),
    ]),
    host,
  ]);
}

/* ------------------------------------------------------------ consent gate */
export function interviewGate({ id }) {
  // The consent screen leads into the room, so it shares the room's dark ground —
  // again for continuity with the video, not because of who is reading it.
  const host = h('div', { class: 'consent force-dark' }, [skeletonRows(1, 320)]);

  (async () => {
    let session;
    try {
      session = await getSession(id);
    } catch (err) {
      clear(host).append(h('div', { class: 'card card-pad consent-card' }, [empty({
        iconName: 'warning', title: 'Interview unavailable', body: err.message,
        action: h('a', { class: 'btn', href: '#/candidate/applications', text: 'Back to applications' }),
      })]));
      return;
    }

    if (session.disclosure_accepted) { mountRoom(host, id, session); return; }

    const agree = h('input', { type: 'checkbox', id: 'agree', style: { width: '18px', height: '18px' } });
    const startBtn = h('button', {
      class: 'btn btn-primary btn-lg btn-block', type: 'button', disabled: true,
      text: 'I understand — start the interview',
      onClick: async () => {
        startBtn.disabled = true;
        startBtn.replaceChildren(h('span', { class: 'spin' }), 'Starting…');
        try {
          await fetch(`${API_BASE}/api/candidate/sessions/${id}/consent`, {
            method: 'POST',
            headers: { Authorization: `Bearer ${Store.token('candidate')}` },
          });
          mountRoom(host, id, session);
        } catch (err) {
          toast(err.message || 'Could not start', 'err');
          startBtn.disabled = false;
          startBtn.replaceChildren('I understand — start the interview');
        }
      },
    });
    agree.onchange = () => { startBtn.disabled = !agree.checked; };

    clear(host).append(h('div', { class: 'card card-pad consent-card col gap6' }, [
      h('div', { class: 'col gap3' }, [
        h('span', { class: 'ai-badge' }, [h('span', { html: icon('activity', 12) }), 'AI interview']),
        h('h1', { class: 'display', style: { fontSize: 'var(--fs-28)' },
          text: session.job_title
            ? `${session.is_practice ? 'Practice' : 'Interview'} — ${session.job_title}`
            : (session.is_practice ? 'Practice interview' : 'Panel interview') }),
      ]),

      h('p', { class: 't2 fs13', text: session.disclosure_text }),

      h('ul', { class: 'consent-list' }, [
        [ 'users', `You will speak with ${(session.panel || []).length} AI interviewers, each with a different focus.` ],
        [ 'mic', 'Speak naturally. You can interrupt an interviewer at any time — they will stop.' ],
        [ 'clock', `The interview runs about ${session.minutes} minutes.` ],
        [ 'file', session.is_practice
          ? 'Your answers are transcribed. Your report and improvement plan are ready the moment the interview ends — nothing is shared with any employer.'
          : 'Your answers are transcribed. Feedback is released by the hiring team after review.' ],
      ].map(([ic, text]) => h('li', {}, [h('span', { html: icon(ic, 15) }), h('span', { text })]))),

      /* Typing is an equal way to answer, not a fallback for a broken microphone.
         A candidate who stammers, has a speech difference, or is answering in a second
         language gets the same panel, the same rules and the same assessment — so the
         choice belongs here, before they start, not hidden in the room. */
      h('div', { class: 'consent-answer-mode' }, [
        h('h3', { class: 'filter-title', text: 'How you answer' }),
        h('p', { class: 'hint', text: 'Both work the same way. The panel adapts to what you say either way, and your assessment does not record which you chose.' }),
        h('div', { class: 'row gap3 wrap' }, [
          h('span', { class: 'chip' }, [h('span', { html: icon('mic', 13) }), ' Speak out loud']),
          h('span', { class: 'chip' }, [h('span', { html: icon('file', 13) }), ' Type your answers']),
        ]),
        h('p', { class: 'hint', text: 'You can switch at any point during the interview.' }),
      ]),

      h('div', { class: 'row wrap gap2' },
        (session.panel || []).map((p) => h('span', { class: `persona p-${p.key}`, text: p.label }))),

      h('hr', { class: 'hr' }),

      h('label', { class: 'row gap3', for: 'agree', style: { cursor: 'pointer', alignItems: 'flex-start' } }, [
        agree,
        h('span', { class: 'fs13 t2', text:
          'I understand I am being interviewed by AI, not by a person, and I consent to my answers being transcribed and assessed.' }),
      ]),

      startBtn,
      h('a', { class: 'btn btn-ghost btn-block',
        href: session.is_practice ? '#/candidate/practice' : '#/candidate/applications',
        text: 'Not now' }),
    ]));
  })();

  return host;
}

async function mountRoom(host, sessionId, session) {
  clear(host);
  host.className = '';
  // Agora and the room are ~1.3 MB — only loaded once an interview actually starts.
  if (!window.AgoraRTC) {
    await new Promise((resolve) => {
      const s = document.createElement('script');
      s.src = './js/vendor/AgoraRTC_N-production.js';
      s.onload = resolve;
      s.onerror = resolve;   // Agora is optional; the interview runs without it
      document.head.append(s);
    });
  }
  const { InterviewRoom } = await import('../interview/room.js');
  const room = new InterviewRoom(host, {
    sessionId,
    token: Store.token('candidate'),
    session,
    onExit: () => go(session.is_practice
      ? `/candidate/practice/${sessionId}/report`
      : '/candidate/applications'),
  });
  await room.start();
}
