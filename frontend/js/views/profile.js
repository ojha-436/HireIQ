/* ============================================================================
   views/profile.js — the candidate's profile, with resume import.

   Why the profile matters beyond form-filling: it is what grounds the interview.
   `interview/context.py` builds the CANDIDATE block from these typed fields and
   mints a claim_id for each checkable statement, which is how rule R4 can tell
   an unsubstantiated résumé claim from an ordinary answer. A thin profile means
   a panel with nothing specific to challenge.

   Raw résumé text is never stored — only the derived skills and year count.
   ============================================================================ */

import { Api, API_BASE } from '../api.js';
import { Store } from '../store.js';
import { clear, empty, h, icon, skeletonRows, toast } from '../ui.js';

export function profileView() {
  const host = h('div', {}, [skeletonRows(3, 140)]);

  (async () => {
    try {
      clear(host).append(render(await Api.candidate.me()));
    } catch (err) {
      clear(host).append(h('div', { class: 'card' }, [empty({
        iconName: 'warning', title: 'Could not load your profile', body: err.message,
      })]));
    }
  })();

  return host;
}

function field({ id, label, value = '', type = 'text', hint, placeholder, ...attrs }) {
  const input = h('input', { class: 'input', id, type, value: value ?? '', placeholder, ...attrs });
  return {
    input,
    node: h('div', { class: 'field' }, [
      h('label', { class: 'label', for: id, text: label }),
      input,
      hint ? h('p', { class: 'hint', text: hint }) : null,
    ]),
  };
}

function render(me) {
  const sections = me.profile_sections_json || {};
  let skills = [...(sections.skills || [])];
  let experience = [...(sections.experience || [])];

  const name = field({ id: 'p-name', label: 'Full name', value: me.full_name, autocomplete: 'name' });
  const phone = field({ id: 'p-phone', label: 'Phone', value: me.phone, type: 'tel', autocomplete: 'tel' });
  const country = field({ id: 'p-country', label: 'Country', value: me.country, placeholder: 'India' });
  const years = field({
    id: 'p-years', label: 'Years of experience', value: me.years_experience ?? '',
    type: 'number', min: '0', max: '50', step: '0.5',
    hint: 'Used to filter roles whose range includes you.',
  });
  const headline = field({
    id: 'p-headline', label: 'Headline', value: sections.headline,
    placeholder: 'Backend engineer, 6 yrs — payments & streaming',
    hint: 'One line. The panel sees this.',
  });
  const summary = h('textarea', {
    class: 'textarea', id: 'p-summary', style: { minHeight: '120px' },
    placeholder: 'Two or three sentences on what you build and what you own.',
  });
  summary.value = sections.summary || '';

  /* ---- skills ---- */
  const skillWrap = h('div', { class: 'skills' });
  const skillInput = h('input', {
    class: 'input', id: 'p-skill', placeholder: 'Add a skill and press Enter',
    'aria-label': 'Add a skill',
  });
  const renderSkills = () => {
    clear(skillWrap).append(
      ...skills.map((sk) => h('span', { class: 'chip' }, [
        sk,
        h('button', {
          class: 'chip-x', type: 'button', 'aria-label': `Remove ${sk}`,
          html: icon('x', 12),
          onClick: () => { skills = skills.filter((x) => x !== sk); renderSkills(); },
        }),
      ])),
      skills.length ? null : h('p', { class: 'hint', text: 'No skills yet — import a resume below, or add them here.' }),
    );
  };
  skillInput.onkeydown = (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const v = skillInput.value.trim();
    if (v && !skills.includes(v)) { skills.push(v); renderSkills(); }
    skillInput.value = '';
  };
  renderSkills();

  /* ---- experience ---- */
  const expWrap = h('div', { class: 'col gap3' });
  const renderExp = () => {
    clear(expWrap).append(
      ...experience.map((role, i) => {
        const set = (k) => (e) => { experience[i] = { ...experience[i], [k]: e.target.value }; };
        return h('div', { class: 'exp-row' }, [
          h('div', { class: 'col gap2 grow', style: { minWidth: 0 } }, [
            h('div', { class: 'row gap2 wrap' }, [
              h('input', { class: 'input', placeholder: 'Title', 'aria-label': 'Job title',
                value: role.title || '', onInput: set('title') }),
              h('input', { class: 'input', placeholder: 'Company', 'aria-label': 'Company',
                value: role.org || role.company || '', onInput: set('org') }),
              h('input', { class: 'input', placeholder: 'Dates', 'aria-label': 'Dates',
                value: role.dates || '', onInput: set('dates'), style: { maxWidth: '150px' } }),
            ]),
            h('textarea', {
              class: 'textarea', style: { minHeight: '64px' },
              placeholder: 'What you owned, and what changed because of it.',
              'aria-label': 'What you did', onInput: set('detail'),
            }, [role.detail || '']),
          ]),
          h('button', {
            class: 'icon-btn', type: 'button', 'aria-label': 'Remove this role',
            html: icon('x', 16),
            onClick: () => { experience.splice(i, 1); renderExp(); },
          }),
        ]);
      }),
      experience.length ? null : h('p', { class: 'hint', text: 'Add the roles you want the panel to ask about.' }),
      h('button', { class: 'btn btn-sm', type: 'button',
        onClick: () => { experience.push({ title: '', org: '', dates: '', detail: '' }); renderExp(); },
      }, [h('span', { html: icon('plus', 14) }), 'Add a role']),
    );
  };
  renderExp();

  /* ---- resume import ---- */
  const fileInput = h('input', { type: 'file', id: 'p-resume', accept: '.pdf,.txt,.md',
    style: { display: 'none' } });
  const importNote = h('p', { class: 'hint', role: 'status' });
  const importBtn = h('button', { class: 'btn btn-primary', type: 'button' }, [
    h('span', { html: icon('file', 15) }), me.resume_meta_json?.filename ? 'Replace resume' : 'Import from resume',
  ]);
  importBtn.onclick = () => fileInput.click();

  fileInput.onchange = async () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    importBtn.disabled = true;
    importBtn.replaceChildren(h('span', { class: 'spin' }), 'Reading…');
    importNote.style.color = 'var(--text-3)';
    importNote.textContent = '';
    try {
      const parsed = await Api.candidate.uploadResume(file);
      // Merge rather than replace: anything typed by hand outranks a parse.
      for (const sk of parsed.skills) if (!skills.includes(sk)) skills.push(sk);
      renderSkills();
      if (parsed.years_experience && !years.input.value) {
        years.input.value = String(parsed.years_experience);
      }
      importNote.textContent = `Found ${parsed.skills.length} skills`
        + (parsed.years_experience ? ` and ${parsed.years_experience} years` : '')
        + ' — review below, then save.';
      toast('Resume imported.');
    } catch (err) {
      importNote.textContent = err.message;
      importNote.style.color = 'var(--danger)';
    } finally {
      importBtn.disabled = false;
      importBtn.replaceChildren(h('span', { html: icon('file', 15) }), 'Replace resume');
      fileInput.value = '';
    }
  };

  /* ---- save ---- */
  const saveBtn = h('button', { class: 'btn btn-primary', type: 'submit', text: 'Save profile' });
  const form = h('form', { class: 'col gap6', novalidate: true, onSubmit: async (e) => {
    e.preventDefault();
    saveBtn.disabled = true;
    saveBtn.replaceChildren(h('span', { class: 'spin' }), 'Saving…');
    try {
      const updated = await Api.candidate.updateMe({
        full_name: name.input.value.trim() || me.full_name,
        phone: phone.input.value.trim() || null,
        country: country.input.value.trim() || null,
        years_experience: years.input.value === '' ? null : Number(years.input.value),
        profile_sections_json: {
          ...sections,
          headline: headline.input.value.trim(),
          summary: summary.value.trim(),
          skills,
          experience: experience.filter((r) => (r.title || r.org || r.detail || '').trim()),
        },
      });
      Store.setProfile('candidate', updated);
      toast('Profile saved.');
    } catch (err) {
      toast(err.message, 'err');
    } finally {
      saveBtn.disabled = false;
      saveBtn.replaceChildren('Save profile');
    }
  } }, [
    h('div', { class: 'card card-pad col gap5' }, [
      h('h2', { style: { fontSize: 'var(--fs-16)' }, text: 'About you' }),
      h('div', { class: 'row gap4 wrap' }, [
        h('div', { class: 'grow' }, [name.node]),
        h('div', { class: 'grow' }, [phone.node]),
      ]),
      h('div', { class: 'row gap4 wrap' }, [
        h('div', { class: 'grow' }, [country.node]),
        h('div', { class: 'grow' }, [years.node]),
      ]),
      headline.node,
      h('div', { class: 'field' }, [
        h('label', { class: 'label', for: 'p-summary', text: 'Summary' }), summary,
      ]),
    ]),

    h('div', { class: 'card card-pad col gap4' }, [
      h('h2', { style: { fontSize: 'var(--fs-16)' }, text: 'Skills' }),
      h('p', { class: 'hint', text: 'These are matched against each role, and they are what the panel probes.' }),
      skillWrap,
      skillInput,
    ]),

    h('div', { class: 'card card-pad col gap4' }, [
      h('h2', { style: { fontSize: 'var(--fs-16)' }, text: 'Experience' }),
      h('p', { class: 'hint', text: 'The hiring manager can challenge a claim here that your answers do not back up — so keep it to what you can defend.' }),
      expWrap,
    ]),

    h('div', { class: 'row gap3' }, [saveBtn]),
  ]);

  return h('div', {}, [
    h('header', { class: 'col gap3', style: { marginBottom: 'var(--s8)' } }, [
      h('h1', { class: 'display', style: { fontSize: 'var(--fs-34)' }, text: 'Your profile' }),
      h('p', { class: 't2', style: { maxWidth: '64ch' },
        text: 'This is what the interview panel is given before it asks you anything. A specific profile produces specific questions.' }),
    ]),

    h('div', { class: 'split-main' }, [
      form,
      h('aside', { class: 'col gap5 aside-sticky' }, [
        h('div', { class: 'card card-pad col gap4' }, [
          h('h3', { class: 'filter-title', text: 'Import from resume' }),
          h('p', { class: 'hint', text: 'PDF or plain text, up to 5 MB. We extract skills and years of experience and merge them into the form — nothing is overwritten, and the resume text itself is not stored.' }),
          importBtn, fileInput, importNote,
          me.resume_meta_json?.filename
            ? h('p', { class: 'fs11 t3', text: `Last imported: ${me.resume_meta_json.filename}` })
            : null,
        ]),
        h('div', { class: 'card card-pad col gap3' }, [
          h('h3', { class: 'filter-title', text: 'Why this matters' }),
          h('p', { class: 'hint', text: 'Interviewers are told your claimed skills and roles. That is how the hiring manager can ask you to substantiate something specific — and how the panel avoids asking about work you never did.' }),
        ]),
      ]),
    ]),
  ]);
}
