/* Mic capture worklet — resamples the candidate's microphone to the 16 kHz mono
   16-bit PCM that Gemini Live requires (plan-v3.md §5.1).

   Loaded via audioContext.audioWorklet.addModule(), so this file runs in the
   AudioWorkletGlobalScope: no DOM, no window, no console guarantees.

   The device rate is usually 48000 (a clean 3:1 decimation) but can be 44100, which is
   a fractional 2.75625:1. So the resampler is a general box-filter decimator: it
   averages every input sample that falls inside one output sample's span. Averaging
   rather than picking every Nth sample matters — plain decimation aliases, and aliased
   speech is exactly the kind of "it works but transcription is bad" bug that is very
   hard to trace back to here. */

const TARGET_RATE = 16000;
// 640 samples @16 kHz = 40 ms per message: small enough to keep barge-in responsive,
// large enough that we are not paying WebSocket framing overhead per 2 ms.
const FRAME_SAMPLES = 640;

/* Voice activity detection.

   Gemini Live's automatic VAD would make the model reply the instant the candidate stops
   talking — which bypasses the moderator entirely and hands turn-taking back to the model.
   PS11 asks for *controlled* turn-taking, so automatic detection is disabled server-side
   and end-of-turn is decided here instead: we tell the server when speech starts and
   stops, the moderator picks who answers, and only then does a persona speak.

   Energy-based with hysteresis. A single threshold flutters on breath and room noise, so
   speech must exceed START_RMS to open and stay under END_RMS for HANG_MS to close. */
const START_RMS = 0.018;      // ~-35 dBFS: above typical room noise, below quiet speech
const END_RMS = 0.010;        // lower bar to stay open, so pauses mid-sentence don't cut
const HANG_MS = 700;          // silence before we call the turn over
const MIN_SPEECH_MS = 250;    // ignore coughs, clicks and door slams
// Safety valve: the only way out of `speaking` above is the RMS staying under END_RMS
// for HANG_MS straight. A candidate whose room's ambient noise floor sits at or above
// END_RMS (a fan, AC, traffic, room echo) never produces a quiet enough gap, so
// speech_end never fires at all — the tile reads "YOU ARE SPEAKING" forever and no
// answer ever reaches the transcript, because the turn never settles server-side
// either. This forces a boundary regardless of RMS so the interview can never lock up
// that way. If the candidate is still genuinely talking, MIN_SPEECH_MS reopens
// speech_start within a quarter-second of this firing; the backend's own late-tail
// append logic (session.py `_flush_candidate_turn`) is what stitches a long answer
// split by this back into one transcript row.
const MAX_SPEECH_MS = 20000;

class MicCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE;   // `sampleRate` is a worklet global
    this.acc = 0;         // running sum of input samples in the current output bucket
    this.accCount = 0;    // how many input samples are in it
    this.pos = 0;         // fractional position within the current bucket
    this.out = new Int16Array(FRAME_SAMPLES);
    this.outLen = 0;
    this.muted = false;
    this.speaking = false;
    this.silentMs = 0;
    this.speechMs = 0;
    this.continuousMs = 0;
    this.frameMs = 0;
    this.port.onmessage = (e) => {
      if (e.data && e.data.type === 'mute') this.muted = !!e.data.value;
    };
  }

  _push(sample) {
    // Clamp before scaling: a float slightly outside [-1,1] would wrap to the opposite
    // sign as int16 and produce an audible click.
    const s = Math.max(-1, Math.min(1, sample));
    this.out[this.outLen++] = s < 0 ? s * 0x8000 : s * 0x7fff;
    if (this.outLen === FRAME_SAMPLES) {
      const frame = this.out.slice(0);          // copy; `out` is reused
      // ArrayBuffer => audio. Plain object => a VAD event. The main thread branches on type.
      this.port.postMessage(frame.buffer, [frame.buffer]);
      this.outLen = 0;
    }
  }

  _vad(ch) {
    // RMS of this render quantum (128 samples ≈ 2.7 ms at 48 kHz).
    let sum = 0;
    for (let i = 0; i < ch.length; i++) sum += ch[i] * ch[i];
    const rms = Math.sqrt(sum / ch.length);
    const ms = (ch.length / sampleRate) * 1000;

    if (!this.speaking) {
      if (rms > START_RMS) {
        this.speechMs += ms;
        if (this.speechMs >= MIN_SPEECH_MS) {
          this.speaking = true;
          this.silentMs = 0;
          this.continuousMs = 0;
          this.port.postMessage({ type: 'speech_start' });
        }
      } else {
        this.speechMs = 0;
      }
      return;
    }

    this.continuousMs += ms;
    if (rms < END_RMS) {
      this.silentMs += ms;
      if (this.silentMs >= HANG_MS) {
        this._endSpeech();
        return;
      }
    } else {
      this.silentMs = 0;
    }
    if (this.continuousMs >= MAX_SPEECH_MS) {
      this._endSpeech();
    }
  }

  _endSpeech() {
    this.speaking = false;
    this.speechMs = 0;
    this.silentMs = 0;
    this.continuousMs = 0;
    this.port.postMessage({ type: 'speech_end' });
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input.length) return true;
    const ch = input[0];
    if (!ch) return true;

    if (!this.muted) this._vad(ch);

    for (let i = 0; i < ch.length; i++) {
      this.acc += this.muted ? 0 : ch[i];
      this.accCount++;
      this.pos += 1;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        this._push(this.accCount ? this.acc / this.accCount : 0);
        this.acc = 0;
        this.accCount = 0;
      }
    }
    return true;   // keep the node alive even while the track is silent
  }
}

registerProcessor('mic-capture', MicCapture);
