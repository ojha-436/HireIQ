/* ============================================================================
   room.js — the live interview room.

   The audio pipeline (bot-audio.js, mic-worklet.js) is the ported, tested path:
   hysteresis VAD in an AudioWorklet decides turn boundaries, and playback is
   flushed the instant the candidate starts speaking. This module owns the
   presentation and the socket, not the DSP.

   Protocol (server -> client): session | floor | transcript | trace | your_turn |
   interrupted | notice | takeover | ended | error, plus binary PCM16 @24kHz.
   ============================================================================ */

import { API_BASE } from '../api.js';
import { clear, h, icon, PERSONA_LABEL } from '../ui.js';
import { BotAudio, captureMic } from './bot-audio.js';

const WORKLET_URL = new URL('./mic-worklet.js', import.meta.url).href;

export class InterviewRoom {
  constructor(root, { sessionId, token, session, onExit }) {
    this.root = root;
    this.sessionId = sessionId;
    this.token = token;
    this.session = session;               // from GET /api/candidate/sessions/:id
    this.onExit = onExit || (() => {});

    this.ws = null;
    this.bot = new BotAudio(24000);
    // Floor and voice are different facts. The floor comes from the server; whether
    // sound is actually playing is only knowable here. Conflating them is what made
    // the room show a talking interviewer in silence.
    this.bot.onSpeakingChange = (audible) => this._setAudible(audible);
    this.audible = false;
    this.audioSeen = false;
    this.mic = null;
    this.agora = null;
    this.camTrack = null;

    this.muted = false;
    this.speaking = null;                 // persona key holding the floor
    this.turns = [];
    this.traces = [];
    this.difficulty = null;
    this.ended = false;
    this.deadline = null;
    this._timer = null;
  }

  // ------------------------------------------------------------------ lifecycle
  async start() {
    this._renderShell();
    this._status('Connecting…');
    try {
      await this.bot.resume();
      await this._joinMedia();
      this._openSocket();
    } catch (err) {
      this._status(err.message || 'Could not start the interview', true);
    }
  }

  async destroy() {
    clearInterval(this._timer);
    try { this.ws?.close(); } catch { /* already closed */ }
    try { await this.mic?.stop?.(); } catch { /* not started */ }
    try { await this.bot.close(); } catch { /* not open */ }
    if (this.agora) {
      try { this.camTrack?.close(); await this.agora.leave(); } catch { /* not joined */ }
    }
  }

  // ------------------------------------------------------------------ media
  async _joinMedia() {
    let info = null;
    try {
      info = await fetch(`${API_BASE}/api/candidate/sessions/${this.sessionId}/agora-token`, {
        headers: { Authorization: `Bearer ${this.token}` },
      }).then((r) => r.json());
    } catch { /* fall through to mic-only */ }

    // Microphone is required; camera and Agora are not. An interview without a
    // video tile still works — an interview without a mic does not.
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
        video: true,
      });
    } catch {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    }

    const videoTrack = stream.getVideoTracks()[0];
    if (videoTrack) {
      const self = this.root.querySelector('#self-video');
      if (self) { self.srcObject = new MediaStream([videoTrack]); self.play?.().catch(() => {}); }
    }

    const audioTrack = stream.getAudioTracks()[0];
    this.mic = await captureMic(
      audioTrack,
      (frame) => this._sendBinary(frame),
      WORKLET_URL,
      (voiceOn) => this._onVoice(voiceOn),
    );

    if (info?.enabled && info?.app_id && window.AgoraRTC) {
      try {
        this.agora = window.AgoraRTC.createClient({ mode: 'rtc', codec: 'vp8' });
        await this.agora.join(info.app_id, info.channel, info.token || null, info.uid);
        this.bot.attachAgora?.(this.agora);
      } catch {
        this.agora = null;   // media relay is optional; the interview proceeds regardless
      }
    }
  }

  _onVoice(voiceOn) {
    if (this.ended) return;
    if (voiceOn) {
      // Barge-in: kill playback locally the moment the candidate speaks, and tell
      // the server so the persona stops generating too. Symmetric, sub-200ms.
      this.bot.flush();
      this._setSpeaking(null);
      this._send({ type: 'speech_start' });
      this._setMicState(true);
    } else {
      this._send({ type: 'speech_end' });
      this._setMicState(false);
    }
  }

  // ------------------------------------------------------------------ socket
  _openSocket() {
    const base = API_BASE || `${location.protocol}//${location.host}`;
    const proto = base.startsWith('https') ? 'wss' : 'ws';
    const host = base.replace(/^https?:\/\//, '');
    this.ws = new WebSocket(
      `${proto}://${host}/api/interview/ws/${this.sessionId}?token=${encodeURIComponent(this.token)}`,
    );
    this.ws.binaryType = 'arraybuffer';

    this.ws.onopen = () => this._status('Connected');
    this.ws.onmessage = (e) => {
      if (typeof e.data !== 'string') { this.bot.enqueue(e.data); return; }
      let msg; try { msg = JSON.parse(e.data); } catch { return; }
      this._onEvent(msg);
    };
    this.ws.onerror = () => this._status('Connection problem', true);
    this.ws.onclose = (e) => {
      if (this.ended) return;
      const why = {
        4401: 'This interview is not yours to join.',
        4404: 'Interview not found.',
        4409: 'Accept the AI interview disclosure first.',
        4410: 'This interview has already finished.',
      }[e.code];
      this._status(why || 'Disconnected', true);
    };
  }

  _send(obj) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj));
  }

  _sendBinary(buf) {
    if (this.ws?.readyState === WebSocket.OPEN && !this.muted) this.ws.send(buf);
  }

  _onEvent(msg) {
    switch (msg.type) {
      case 'audio_health':
        // The server tells us whether that turn produced any audio at all.
        if (!msg.ok) this._voiceWarning();
        break;
      case 'session':
        this.deadline = Date.now() + (msg.seconds_remaining || 0) * 1000;
        this._renderPanel(msg.panel || []);
        this._startClock();
        this._status('');
        break;

      case 'floor':
        this._setSpeaking(msg.persona);
        break;

      case 'transcript':
        this._pushTurn(msg);
        break;

      case 'trace':
        this.traces.unshift(msg);
        if (typeof msg.difficulty === 'number') this._setDifficulty(msg.difficulty);
        this._renderTrace();
        break;

      case 'your_turn':
        this._setSpeaking(null);
        this._status('Your turn — just start speaking.');
        break;

      case 'interrupted':
        this.bot.flush();
        this._setSpeaking(null);
        break;

      case 'notice':
        if (msg.code === 'no_audio') { this._voiceWarning(msg.text); break; }
        this._status(msg.text || '');
        break;

      case 'takeover':
        this._status(msg.text || msg.detail || '');
        break;

      case 'ended':
        this.ended = true;
        this._renderEnded(msg);
        break;

      case 'error':
        this._status(msg.detail || 'Something went wrong', true);
        break;

      default:
        break;
    }
  }

  // ------------------------------------------------------------------ render
  _renderShell() {
    clear(this.root).append(
      // `force-dark` regardless of the viewer's theme: a bright page behind a webcam
      // tile lights the candidate's face and washes out the feed. This is about the
      // video, not about who is in the room.
      h('div', { class: 'room force-dark' }, [
        h('header', { class: 'room-bar' }, [
          h('div', { class: 'row gap4' }, [
            // Layer 2 of the AI disclosure: a persistent, non-dismissible badge.
            h('span', { class: 'ai-badge' }, [
              h('span', { html: icon('activity', 12) }), 'AI interview',
            ]),
            h('span', { class: 'fs13 t2 truncate', text: this.session?.job_title || '' }),
          ]),
          h('div', { class: 'row gap4' }, [
            h('span', { id: 'diff-slot' }),
            h('span', { id: 'clock', class: 'mono fs13 t2', text: '--:--' }),
            h('button', {
              class: 'btn btn-danger btn-sm', type: 'button', id: 'end-btn',
              text: 'End interview',
            }),
          ]),
        ]),

        h('div', { class: 'voice-warning', id: 'voice-warning', role: 'alert', hidden: true }),

        h('div', { class: 'room-body' }, [
          h('main', { class: 'room-stage' }, [
            h('div', { class: 'panel-grid', id: 'panel' }),
            h('div', { class: 'self-wrap' }, [
              h('video', { id: 'self-video', muted: true, playsinline: true, autoplay: true }),
              h('span', { class: 'self-label mono fs11', id: 'mic-state', text: 'MIC IDLE' }),
            ]),
          ]),

          h('aside', { class: 'room-side' }, [
            h('div', { class: 'side-head row-between' }, [
              h('h2', { style: { fontSize: 'var(--fs-13)', letterSpacing: '.05em', textTransform: 'uppercase', color: 'var(--text-3)' }, text: 'Transcript' }),
            ]),
            h('div', { class: 'transcript', id: 'transcript', 'aria-live': 'polite' }),
            h('div', { class: 'side-foot col gap3' }, [
              h('p', { class: 'hint', id: 'status', role: 'status' }),
              h('div', { class: 'row gap2' }, [
                h('input', {
                  class: 'input', id: 'type-input', placeholder: 'Type your answer',
                  // Not "instead of speaking" — typing is an equal path, and the label
                  // a screen reader announces should not imply it is second best.
                  'aria-label': 'Type your answer',
                }),
                h('button', { class: 'btn btn-sm', type: 'button', id: 'send-btn', text: 'Send' }),
              ]),
              h('div', { class: 'row gap2' }, [
                h('button', { class: 'btn btn-sm grow', type: 'button', id: 'mute-btn',
                  title: 'Turn the microphone off and answer by typing' }, [
                  h('span', { html: icon('mic', 14) }), 'Mute',
                ]),
                h('button', {
                  class: 'btn btn-sm grow', type: 'button', id: 'done-btn',
                  text: "I'm done answering",
                }),
              ]),
            ]),
          ]),
        ]),
      ]),
    );

    const q = (s) => this.root.querySelector(s);
    q('#mute-btn').onclick = () => {
      this.muted = !this.muted;
      q('#mute-btn').replaceChildren(
        h('span', { html: icon('mic', 14) }), this.muted ? 'Unmute' : 'Mute');
      q('#mute-btn').setAttribute('aria-pressed', String(this.muted));
    };
    q('#done-btn').onclick = () => this._send({ type: 'activity_end' });
    q('#end-btn').onclick = () => { this._send({ type: 'end' }); };
    const sendTyped = () => {
      const el = q('#type-input');
      const text = el.value.trim();
      if (!text) return;
      this._send({ type: 'text', text });
      el.value = '';
    };
    q('#send-btn').onclick = sendTyped;
    q('#type-input').onkeydown = (e) => { if (e.key === 'Enter') sendTyped(); };
  }

  _renderPanel(panel) {
    const el = this.root.querySelector('#panel');
    if (!el) return;
    this.panel = panel;
    clear(el).append(...panel.map((p) => h('div', {
      class: `tile p-${p.key}`, id: `tile-${p.key}`,
    }, [
      h('div', { class: 'tile-face' }, [
        h('span', { class: 'tile-initials mono', text: (p.label || p.key).slice(0, 2).toUpperCase() }),
        h('span', { class: 'tally', 'aria-hidden': 'true' }),
      ]),
      h('div', { class: 'tile-meta' }, [
        h('strong', { class: 'fs13', text: p.label || PERSONA_LABEL[p.key] || p.key }),
        h('span', { class: 'fs11 t3', id: `tile-state-${p.key}`, text: 'Listening' }),
      ]),
    ])));
  }

  /** Floor state is luminance + tally + a text label — never colour alone. */
  _setSpeaking(personaKey) {
    this.speaking = personaKey;
    this._paintTiles();
  }

  /** Called by BotAudio when sound actually starts and stops. */
  _setAudible(audible) {
    this.audible = audible;
    if (audible) this.audioSeen = true;
    this._paintTiles();
  }

  _paintTiles() {
    (this.panel || []).forEach((p) => {
      const tile = this.root.querySelector(`#tile-${p.key}`);
      const state = this.root.querySelector(`#tile-state-${p.key}`);
      if (!tile) return;
      const holdsFloor = p.key === this.speaking;
      tile.classList.toggle('speaking', holdsFloor);
      // Only animate the tally when audio is genuinely playing, so a pulsing tile
      // always means "you should be hearing this".
      tile.classList.toggle('audible', holdsFloor && this.audible);
      if (state) {
        state.textContent = !holdsFloor ? 'Listening'
          : (this.audible ? 'Speaking' : 'Thinking…');
      }
    });
  }

  /** Surface a voice failure once, with something the candidate can act on. */
  _voiceWarning(text) {
    if (this._warnedNoVoice) return;
    this._warnedNoVoice = true;
    const banner = this.root.querySelector('#voice-warning');
    if (!banner) return;
    banner.hidden = false;
    banner.replaceChildren(
      h('span', { html: icon('warning', 15) }),
      h('span', { text: text || 'The interviewers\u2019 voice is not coming through on our '
        + 'side. Their questions are in the transcript on the right \u2014 answer by typing. '
        + 'This is scored exactly the same way.' }),
    );
  }

  _setMicState(on) {
    const el = this.root.querySelector('#mic-state');
    if (el) {
      el.textContent = on ? 'YOU ARE SPEAKING' : 'MIC IDLE';
      el.classList.toggle('hot', on);
    }
  }

  _setDifficulty(level) {
    if (level === this.difficulty) return;
    this.difficulty = level;
    const slot = this.root.querySelector('#diff-slot');
    if (!slot) return;
    clear(slot).append(h('span', { class: 'diff', title: `Difficulty ${level} of 5` }, [
      h('span', { class: 'diff-bars', 'aria-hidden': 'true' },
        [1, 2, 3, 4, 5].map((n) => h('i', { class: n <= level ? 'on' : '' }))),
      h('span', { class: 'fs11 t3 mono', text: `L${level}` }),
    ]));
  }

  _pushTurn(msg) {
    const last = this.turns[this.turns.length - 1];
    if (last && last.speaker === msg.speaker && !last.final) {
      last.text = msg.text;
      last.final = msg.final;
    } else {
      this.turns.push({ speaker: msg.speaker, text: msg.text, final: msg.final });
    }
    this._renderTranscript();
  }

  _renderTranscript() {
    const el = this.root.querySelector('#transcript');
    if (!el) return;
    clear(el).append(...this.turns.map((t) => {
      const isCandidate = t.speaker === 'candidate';
      const label = isCandidate ? 'You'
        : (this.panel || []).find((p) => p.key === t.speaker)?.label
          || PERSONA_LABEL[t.speaker] || t.speaker;
      return h('div', { class: `line ${isCandidate ? 'me' : `p-${t.speaker}`}` }, [
        h('span', { class: 'line-who fs11 mono', text: label.toUpperCase() }),
        h('p', { class: 'fs13', text: t.text || '…' }),
      ]);
    }));
    el.scrollTop = el.scrollHeight;
  }

  _renderTrace() { /* the employer monitor renders traces; the candidate never sees them */ }

  _startClock() {
    clearInterval(this._timer);
    const el = this.root.querySelector('#clock');
    const tick = () => {
      if (!this.deadline || !el) return;
      const left = Math.max(0, Math.round((this.deadline - Date.now()) / 1000));
      el.textContent = `${String(Math.floor(left / 60)).padStart(2, '0')}:${String(left % 60).padStart(2, '0')}`;
    };
    tick();
    this._timer = setInterval(tick, 1000);
  }

  _status(text, isError = false) {
    const el = this.root.querySelector('#status');
    if (!el) return;
    el.textContent = text || '';
    el.style.color = isError ? 'var(--danger)' : 'var(--text-3)';
  }

  async _renderEnded(msg) {
    clearInterval(this._timer);
    await this.destroy();
    clear(this.root).append(h('div', { class: 'room-done force-dark' }, [
      h('div', { class: 'card card-pad col gap5', style: { maxWidth: '520px' } }, [
        h('span', { class: 'ai-badge' }, [h('span', { html: icon('activity', 12) }), 'AI interview']),
        h('h1', { class: 'display', style: { fontSize: 'var(--fs-28)' }, text: 'Interview complete' }),
        h('p', { class: 't2 fs13', text:
          `Thank you. You spoke for ${Math.round((msg.duration_s || 0) / 60)} minute(s) across ${msg.turns || 0} turns.` }),
        // Scores are never shown to the candidate here: feedback is released by the
        // employer after review, which is what stops candidates gaming later rounds.
        h('p', { class: 'hint', text:
          'Your assessment goes to the hiring team for review. When they release it, the feedback appears under My applications — every point in it links to what you actually said.' }),
        h('a', { class: 'btn btn-primary', href: '#/candidate/applications', text: 'Back to my applications',
          onClick: () => this.onExit() }),
      ]),
    ]));
  }
}
