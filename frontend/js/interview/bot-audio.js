/* Interviewer audio playback (plan-v3.md §5.1).

   The server relays the model's speech over our own WebSocket as 24 kHz mono 16-bit
   PCM. This module:
     1. plays it for the candidate, gapless, by scheduling each chunk on a shared clock;
     2. can FLUSH instantly on barge-in.

   The AI's voice as an Agora channel participant does NOT route through here — it comes
   from a genuinely separate Agora Conversational AI agent per persona
   (backend/app/interview/agora_convoai.py), each with its own agent_rtc_uid, and is
   subscribed and played directly by room.js like any other remote participant. Piping
   this module's LOCAL playback back out as a second, candidate-identity-branded Agora
   track was the earlier design and was never finished (a dead `attachAgora` hook); it
   would also have meant the "AI participant" spoke under the candidate's own uid rather
   than as a real distinct identity, which the ConvoAI-agent path gives for free.

   (2) is the one that matters most. Gemini stops generating the moment the candidate
   speaks, but anything already scheduled would keep playing — the interviewer would
   talk over the candidate for a second after being interrupted, which reads as broken.
   So every scheduled source is tracked and stopped on flush. */

export class BotAudio {
  constructor(sampleRate = 24000) {
    this.srcRate = sampleRate;
    this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    this.gain = this.ctx.createGain();
    this.gain.connect(this.ctx.destination);

    this.nextAt = 0;
    this.pending = new Set();
    this.speaking = false;
    this.onSpeakingChange = null;
  }

  async resume() {
    // Browsers start an AudioContext suspended until a user gesture.
    if (this.ctx.state === 'suspended') await this.ctx.resume();
  }

  /* Accepts an ArrayBuffer of little-endian int16 samples. */
  enqueue(arrayBuffer) {
    if (!arrayBuffer || arrayBuffer.byteLength < 2) return;
    const pcm = new Int16Array(arrayBuffer);
    const buf = this.ctx.createBuffer(1, pcm.length, this.srcRate);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 0x8000;

    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.gain);

    // A small lead keeps the first chunk from being clipped by scheduling latency.
    const now = this.ctx.currentTime;
    const startAt = Math.max(now + 0.04, this.nextAt);
    src.start(startAt);
    this.nextAt = startAt + buf.duration;

    this.pending.add(src);
    src.onended = () => {
      this.pending.delete(src);
      if (this.pending.size === 0) this._setSpeaking(false);
    };
    this._setSpeaking(true);
  }

  /* Barge-in: drop everything already scheduled, immediately. */
  flush() {
    for (const src of this.pending) {
      try { src.onended = null; src.stop(); } catch (_) { /* already ended */ }
    }
    this.pending.clear();
    this.nextAt = 0;
    this._setSpeaking(false);
  }

  _setSpeaking(v) {
    if (this.speaking === v) return;
    this.speaking = v;
    if (this.onSpeakingChange) this.onSpeakingChange(v);
  }

  async close() {
    this.flush();
    try { await this.ctx.close(); } catch (_) { /* already closed */ }
  }
}

/* Mic → 16 kHz PCM frames + voice-activity events. Returns { stop(), setMuted() }.
   `onFrame` receives an ArrayBuffer of int16 samples, ready to send as-is.
   `onVoice` receives 'speech_start' | 'speech_end' — the worklet decides end-of-turn
   because the moderator, not the model, must choose who answers (see mic-worklet.js). */
export async function captureMic(mediaStreamTrack, onFrame, workletUrl, onVoice) {
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  if (!ctx.audioWorklet) {
    await ctx.close();
    throw new Error('This browser has no AudioWorklet support, which the live interview needs.');
  }
  await ctx.audioWorklet.addModule(workletUrl);
  const stream = new MediaStream([mediaStreamTrack]);
  const source = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, 'mic-capture');
  node.port.onmessage = (e) => {
    const d = e.data;
    if (d instanceof ArrayBuffer) { onFrame(d); return; }
    if (d && d.type && onVoice) onVoice(d.type);
  };
  source.connect(node);
  // Terminate the graph without making the mic audible to the candidate (that would be
  // a feedback loop). A zero-gain sink keeps the worklet pulling.
  const sink = ctx.createGain();
  sink.gain.value = 0;
  node.connect(sink).connect(ctx.destination);

  return {
    sampleRate: ctx.sampleRate,
    setMuted(v) { node.port.postMessage({ type: 'mute', value: !!v }); },
    async stop() {
      try { node.port.onmessage = null; source.disconnect(); node.disconnect(); } catch (_) {}
      try { await ctx.close(); } catch (_) {}
    },
  };
}
