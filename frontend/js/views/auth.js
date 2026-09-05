/* ============================================================================
   views/auth.js — landing, sign-in and sign-up for both audiences.
   ============================================================================ */

import { Api, ApiError } from '../api.js';
import { go } from '../router.js';
import { Store } from '../store.js';
import { h, icon, themeToggle, toast } from '../ui.js';

/* ------------------------------------------------------------ field helper */
function field({ name, label, type = 'text', required = false, hint, autocomplete, placeholder }) {
  const input = h('input', {
    class: 'input', id: `f-${name}`, name, type,
    autocomplete, placeholder,
    'aria-describedby': hint ? `h-${name}` : null,
    required: required || null,
  });
  const error = h('p', { class: 'err', id: `e-${name}`, role: 'alert', hidden: true });

  // Password fields get a reveal toggle. Typing a long passphrase blind is the
  // single most common cause of a false "wrong password".
  let control = input;
  if (type === 'password') {
    const reveal = h('button', {
      class: 'reveal', type: 'button',
      'aria-label': 'Show password', 'aria-pressed': 'false',
      html: icon('eye', 17),
      onClick: () => {
        const shown = input.type === 'text';
        input.type = shown ? 'password' : 'text';
        reveal.setAttribute('aria-pressed', String(!shown));
        reveal.setAttribute('aria-label', shown ? 'Show password' : 'Hide password');
        reveal.innerHTML = icon(shown ? 'eye' : 'eyeOff', 17);
        input.focus();
      },
    });
    control = h('div', { class: 'input-wrap' }, [input, reveal]);
  }

  const wrap = h('div', { class: 'field' }, [
    h('label', { class: 'label', for: `f-${name}` }, [
      label,
      required ? h('span', { class: 'req', text: '*', 'aria-hidden': 'true' }) : null,
    ]),
    control,
    hint ? h('p', { class: 'hint', id: `h-${name}`, text: hint }) : null,
    error,
  ]);

  return {
    node: wrap,
    input,
    get value() { return input.value.trim(); },
    setError(message) {
      if (!message) {
        error.hidden = true;
        input.classList.remove('invalid');
        input.removeAttribute('aria-invalid');
        return;
      }
      error.hidden = false;
      error.replaceChildren(h('span', { html: icon('alert', 13) }), h('span', { text: message }));
      input.classList.add('invalid');
      input.setAttribute('aria-invalid', 'true');
    },
  };
}

/* --------------------------------------------------------------- the shell */
const COPY = {
  employer: {
    register: 'Northwind hires with a record.',
    claim: ['Every hire,', h('em', { text: 'on the record.' })],
    points: [
      ['layers', 'A panel, not a script', 'Five AI interviewers with distinct charters share one candidate context and hand the floor to each other under deterministic rules.'],
      ['file', 'Evidence or it is dropped', 'Every line of every assessment cites the transcript turn it came from. Uncited claims never reach the report.'],
      ['shield', 'Auditable by construction', 'Scores are arithmetic, not model opinion. Every decision carries a written reason in an append-only log.'],
    ],
  },
  candidate: {
    claim: ['Interviewed by a panel.', h('em', { text: ' Judged on evidence.' })],
    points: [
      ['mic', 'A real conversation', 'Speak naturally and interrupt when you need to. The panel adapts to what you actually said.'],
      ['users', 'More than one perspective', 'A technical interviewer may accept your answer while the product interviewer asks what it was worth. Both views count.'],
      ['file', 'Feedback you can check', 'Every point in your feedback links to the moment in the transcript it came from.'],
    ],
  },
};

function aside(role) {
  const copy = COPY[role];
  return h('aside', { class: 'auth-aside' }, [
    h('a', { class: 'brand', href: '#/' }, [
      h('span', { class: 'mark', 'aria-hidden': 'true' }, [h('i'), h('i'), h('i'), h('i'), h('i')]),
      'HireIQ',
    ]),
    h('h2', { class: 'auth-claim' }, copy.claim),
    h('div', { class: 'auth-points' },
      copy.points.map(([ic, title, body]) => h('div', { class: 'auth-point' }, [
        h('span', { html: icon(ic, 16) }),
        h('span', {}, [h('strong', { style: { color: 'var(--text)' }, text: title }), ' — ', body]),
      ]))),
  ]);
}

/** Which portal am I signing into? Two near-identical forms with separate account
 *  tables is exactly the setup that produces a confident "invalid credentials". */
function portalChip(role) {
  const isEmployer = role === 'employer';
  return h('span', {
    class: `portal-chip ${isEmployer ? 'is-employer' : 'is-candidate'}`,
  }, [
    h('span', { html: icon(isEmployer ? 'building' : 'user', 13) }),
    isEmployer ? 'Employer portal' : 'Candidate portal',
  ]);
}

/** The theme control on pre-auth screens.
 *
 * These pages have no shell to hang it in, and they are the FIRST thing anyone sees —
 * so leaving it out meant a new visitor could not set their theme until after signing
 * in. Fixed to the viewport corner so it lands in the same place on the landing page
 * and on every auth screen. */
function themeCorner() {
  return h('div', { class: 'theme-corner' }, [themeToggle()]);
}

function screen(role, formNode) {
  return h('div', { class: 'auth' }, [
    themeCorner(),
    aside(role),
    h('main', { class: 'auth-main' }, [formNode]),
  ]);
}

/* ------------------------------------------------------------------ submit */
async function submit(btn, fields, run) {
  Object.values(fields).forEach((f) => f.setError(null));
  const original = btn.textContent;
  btn.disabled = true;
  btn.replaceChildren(h('span', { class: 'spin', 'aria-hidden': 'true' }), 'Working…');

  try {
    await run();
  } catch (err) {
    if (err instanceof ApiError && Object.keys(err.fields).length) {
      let firstBad = null;
      for (const [name, message] of Object.entries(err.fields)) {
        if (fields[name]) { fields[name].setError(message); firstBad = firstBad || fields[name]; }
      }
      firstBad?.input.focus();            // focus the first invalid field
      if (!firstBad) toast(err.message, 'err');
    } else {
      toast(err.message || 'Something went wrong', 'err');
      // Shown on every failed sign-in regardless of whether the address exists,
      // so it helps without confirming which accounts are real.
      const cross = document.getElementById('cross-portal-hint');
      if (cross) cross.hidden = false;
    }
  } finally {
    btn.disabled = false;
    btn.replaceChildren(original);
  }
}

/* ------------------------------------------------------------------ views */
export function landing() {
  return h('div', { class: 'split-choice' }, [
    themeCorner(),
    h('div', { class: 'split-inner' }, [
      h('a', { class: 'brand', href: '#/', style: { marginBottom: 'var(--s8)' } }, [
        h('span', { class: 'mark', 'aria-hidden': 'true' }, [h('i'), h('i'), h('i'), h('i'), h('i')]),
        'HireIQ',
      ]),
      h('h1', { class: 'display', style: { fontSize: 'clamp(2.125rem, 5vw, 3.5rem)', maxWidth: '18ch' } }, [
        'An interview panel that ', h('em', { style: { fontStyle: 'italic', color: 'var(--text-2)' }, text: 'actually listens.' }),
      ]),
      h('p', { class: 't2', style: { marginTop: 'var(--s5)', maxWidth: '60ch', fontSize: 'var(--fs-16)' },
        text: 'Five AI interviewers with different jobs, one shared memory of what the candidate said, and an assessment where every line cites the transcript.' }),

      h('div', { class: 'choice-grid stagger' }, [
        h('a', { class: 'choice', href: '#/employer/login' }, [
          h('span', { html: icon('building', 22), style: { color: 'var(--text-3)' } }),
          h('h2', { class: 'display', text: 'I am hiring' }),
          h('p', { text: 'Post a role, configure the panel, watch the interview live, and decide from evidence.' }),
          h('span', { class: 'go' }, ['Employer console', h('span', { html: icon('arrowRight', 15) })]),
        ]),
        h('a', { class: 'choice', href: '#/candidate/login' }, [
          h('span', { html: icon('user', 22), style: { color: 'var(--text-3)' } }),
          h('h2', { class: 'display', text: 'I am looking' }),
          h('p', { text: 'Find open roles, interview with the panel, and get feedback you can trace to what you said.' }),
          h('span', { class: 'go' }, ['Candidate portal', h('span', { html: icon('arrowRight', 15) })]),
        ]),
      ]),

      h('div', { class: 'row gap4', style: { marginTop: 'var(--s10)', alignItems: 'flex-start', maxWidth: '72ch' } }, [
        h('span', { class: 'ai-badge', style: { flex: 'none' } }, [
          h('span', { html: icon('activity', 12) }), 'AI interview',
        ]),
        h('p', { class: 'fs12 t3', style: { lineHeight: 'var(--lh-dense)' },
          text: 'Interviews on this platform are conducted by AI. You are told before the session starts, the badge stays visible throughout, the first interviewer says so out loud, and it is printed on every report.' }),
      ]),
    ]),
  ]);
}

export function employerLogin() {
  const email = field({ name: 'email', label: 'Work email', type: 'email', required: true, autocomplete: 'email', placeholder: 'you@company.com' });
  const password = field({ name: 'password', label: 'Password', type: 'password', required: true, autocomplete: 'current-password' });
  const btn = h('button', { class: 'btn btn-primary btn-lg btn-block', type: 'submit', text: 'Sign in' });

  const form = h('form', { class: 'auth-form', novalidate: true, onSubmit: (e) => {
    e.preventDefault();
    submit(btn, { email, password }, async () => {
      const res = await Api.employer.login({ email: email.value, password: password.value });
      Store.signIn('employer', res.token);
      Store.setProfile('employer', await Api.employer.me());
      go('/employer/jobs');
    });
  } }, [
    portalChip('employer'),
    h('h1', { class: 'display', text: 'Employer console' }),
    h('p', { class: 'auth-switch', style: { marginBottom: 'var(--s8)' }, text: 'Sign in to your hiring workspace.' }),
    email.node, password.node,
    h('div', { style: { marginTop: 'var(--s6)' } }, [btn]),
    h('p', { class: 'auth-switch', style: { marginTop: 'var(--s5)' } }, [
      'No workspace yet? ', h('a', { href: '#/employer/register', text: 'Create one' }),
    ]),
    h('p', { class: 'auth-switch', style: { marginTop: 'var(--s2)' } }, [
      'Looking for a job instead? ', h('a', { href: '#/candidate/login', text: 'Candidate sign in' }),
    ]),
    h('p', { class: 'cross-hint', id: 'cross-portal-hint', hidden: true }, [
      h('span', { html: icon('alert', 14) }),
      h('span', {}, ['Candidate accounts cannot sign in here — they use the ',
        h('a', { href: '#/candidate/login', text: 'candidate portal' }), '.']),
    ]),
  ]);

  return screen('employer', form);
}

export function employerRegister() {
  const company = field({ name: 'company_name', label: 'Company', required: true, autocomplete: 'organization', placeholder: 'Northwind Systems' });
  const name = field({ name: 'full_name', label: 'Your name', required: true, autocomplete: 'name' });
  const email = field({ name: 'email', label: 'Work email', type: 'email', required: true, autocomplete: 'email', placeholder: 'you@company.com' });
  const password = field({ name: 'password', label: 'Password', type: 'password', required: true, autocomplete: 'new-password', hint: 'At least 8 characters.' });
  const btn = h('button', { class: 'btn btn-primary btn-lg btn-block', type: 'submit', text: 'Create workspace' });

  const form = h('form', { class: 'auth-form', novalidate: true, onSubmit: (e) => {
    e.preventDefault();
    submit(btn, { company_name: company, full_name: name, email, password }, async () => {
      const res = await Api.employer.register({
        company_name: company.value, full_name: name.value,
        email: email.value, password: password.value,
      });
      Store.signIn('employer', res.token);
      Store.setProfile('employer', await Api.employer.me());
      toast('Workspace created. Post your first role.');
      go('/employer/jobs');
    });
  } }, [
    portalChip('employer'),
    h('h1', { class: 'display', text: 'Create a workspace' }),
    h('p', { class: 'auth-switch', style: { marginBottom: 'var(--s8)' }, text: 'You will be the first admin on this account.' }),
    company.node, name.node, email.node, password.node,
    h('div', { style: { marginTop: 'var(--s6)' } }, [btn]),
    h('p', { class: 'auth-switch', style: { marginTop: 'var(--s5)' } }, [
      'Already have one? ', h('a', { href: '#/employer/login', text: 'Sign in' }),
    ]),
  ]);

  return screen('employer', form);
}

export function candidateLogin() {
  const email = field({ name: 'email', label: 'Email', type: 'email', required: true, autocomplete: 'email' });
  const password = field({ name: 'password', label: 'Password', type: 'password', required: true, autocomplete: 'current-password' });
  const btn = h('button', { class: 'btn btn-primary btn-lg btn-block', type: 'submit', text: 'Sign in' });

  const form = h('form', { class: 'auth-form', novalidate: true, onSubmit: (e) => {
    e.preventDefault();
    submit(btn, { email, password }, async () => {
      const res = await Api.candidate.login({ email: email.value, password: password.value });
      Store.signIn('candidate', res.token);
      Store.setProfile('candidate', await Api.candidate.me());
      go('/candidate/dashboard');
    });
  } }, [
    portalChip('candidate'),
    h('h1', { class: 'display', text: 'Welcome back' }),
    h('p', { class: 'auth-switch', style: { marginBottom: 'var(--s8)' }, text: 'Sign in to track your applications and interviews.' }),
    email.node, password.node,
    h('div', { style: { marginTop: 'var(--s6)' } }, [btn]),
    h('p', { class: 'auth-switch', style: { marginTop: 'var(--s5)' } }, [
      'New here? ', h('a', { href: '#/candidate/register', text: 'Create an account' }),
    ]),
    h('p', { class: 'auth-switch', style: { marginTop: 'var(--s2)' } }, [
      'Hiring instead? ', h('a', { href: '#/employer/login', text: 'Employer sign in' }),
    ]),
    h('p', { class: 'cross-hint', id: 'cross-portal-hint', hidden: true }, [
      h('span', { html: icon('alert', 14) }),
      h('span', {}, ['Employer accounts cannot sign in here — they use the ',
        h('a', { href: '#/employer/login', text: 'employer portal' }), '.']),
    ]),
  ]);

  return screen('candidate', form);
}

export function candidateRegister() {
  const name = field({ name: 'full_name', label: 'Full name', required: true, autocomplete: 'name' });
  const email = field({ name: 'email', label: 'Email', type: 'email', required: true, autocomplete: 'email' });
  const phone = field({ name: 'phone', label: 'Phone', type: 'tel', autocomplete: 'tel', hint: 'Optional.' });
  const password = field({ name: 'password', label: 'Password', type: 'password', required: true, autocomplete: 'new-password', hint: 'At least 8 characters.' });
  const btn = h('button', { class: 'btn btn-primary btn-lg btn-block', type: 'submit', text: 'Create account' });

  const form = h('form', { class: 'auth-form', novalidate: true, onSubmit: (e) => {
    e.preventDefault();
    submit(btn, { full_name: name, email, phone, password }, async () => {
      const res = await Api.candidate.register({
        full_name: name.value, email: email.value,
        phone: phone.value || null, password: password.value,
      });
      Store.signIn('candidate', res.token);
      Store.setProfile('candidate', await Api.candidate.me());
      toast('Account created. Try a practice interview or find a role to apply to.');
      go('/candidate/dashboard');
    });
  } }, [
    portalChip('candidate'),
    h('h1', { class: 'display', text: 'Create an account' }),
    h('p', { class: 'auth-switch', style: { marginBottom: 'var(--s8)' }, text: 'One profile, every role on the platform.' }),
    name.node, email.node, phone.node, password.node,
    h('div', { style: { marginTop: 'var(--s6)' } }, [btn]),
    h('p', { class: 'auth-switch', style: { marginTop: 'var(--s5)' } }, [
      'Already have an account? ', h('a', { href: '#/candidate/login', text: 'Sign in' }),
    ]),
  ]);

  return screen('candidate', form);
}
