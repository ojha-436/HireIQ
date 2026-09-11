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

/* ADAPTIVE NOISE FLOOR.

   END_RMS is an absolute threshold, and that is the bug candidates actually feel: in a
   room whose ambient level sits above 0.010 — a fan, an air conditioner, traffic, an
   open-plan office — the signal NEVER falls under it, so the turn never closes on its
   own. The tile sits on "YOU ARE SPEAKING", the panel waits, and the candidate has to
   reach for "I'm done answering" after every single answer. MAX_SPEECH_MS eventually
   rescues it, twenty seconds later, which is the "it takes forever to reply" report.

   So the close threshold follows the room. The floor is learned ONLY while the
   candidate is not speaking — during an answer it is frozen, so a long steady answer
   cannot raise the bar out from under itself — and the turn ends when the level falls
   back to near that floor. In a quiet room this is still END_RMS; in a noisy one it is
   whatever quiet actually sounds like there. */
const FLOOR_MULT = 2.0;       // "back to ambient" = twice the learned floor
const FLOOR_FALL = 0.05;      // learn a quieter room in well under a second
//: Frames are ~2.7ms, so this is a time constant of roughly thirteen seconds. It has to
//: be that slow: anything quicker and a few seconds of speech leaking in before the
//: turn opens drags the floor up with it, and then nothing can ever clear the bar.
const FLOOR_RISE = 0.0002;
//: A hard ceiling. Past this the room is too loud to do energy detection in at all, and
//: letting the floor chase it would silently make the microphone unusable.
const FLOOR_MAX = 0.05;
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

/* ECHO GATE.

   A candidate on laptop speakers hears the interviewer through the room, and the mic
   hears it back. Browser AEC attenuates that but does not remove it, so the residue
   crossed START_RMS and the panel interrupted ITSELF: an interviewer was cut off 819ms
   into a 5-second greeting, its own voice was transcribed as the candidate's answer
   (arriving as "..." and as fragments of other languages), and the turn restarted —
   over and over, pushing speech-end-to-reply past twenty seconds.

   While the interviewer is actually audible, a genuine barge-in has to clear a much
   higher bar and sustain it for longer. Real speech from the person in the room is far
   louder than speaker bleed; echo residue is not. */
const ECHO_START_RMS = 0.075;   // ~4x the quiet-room threshold

/* How long speech must persist to CUT OFF an interviewer who is mid-sentence.

   "Hmm", "mm-hm", "right", a laugh, a throat-clear — these are backchannel, the noises
   a person makes to show they are still listening. At 450ms they cleared the bar and
   stopped the interviewer dead, so acknowledging a question cancelled it. Interrupting
   someone is a deliberate act and reads as one: it takes about a second of continuous
   speech before a listener accepts that you have taken the floor. A real answer clears
   this comfortably; a filler never does.

   This gates INTERRUPTION only. When it is already the candidate's turn the normal
   MIN_SPEECH_MS applies, so answering stays as responsive as it ever was. */
const ECHO_MIN_SPEECH_MS = 1000;

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
    this.botSpeaking = false;    // set from the main thread while playback is audible
    this.noiseFloor = END_RMS;   // learned from the room between answers
    this.silentMs = 0;
    this.speechMs = 0;
    this.continuousMs = 0;
    this.frameMs = 0;
    this.port.onmessage = (e) => {
      if (e.data && e.data.type === 'mute') this.muted = !!e.data.value;
      // Whether the interviewer is audible RIGHT NOW. Only opening a turn is gated on
      // it; closing one never is, or an echo-triggered turn could never end.
      if (e.data && e.data.type === 'bot-speaking') this.botSpeaking = !!e.data.value;
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
      // Speaker bleed only has to be rejected while there is something to bleed.
      // Learn the room only while nobody is talking into it.
      this.noiseFloor = Math.min(FLOOR_MAX, rms < this.noiseFloor
        ? (1 - FLOOR_FALL) * this.noiseFloor + FLOOR_FALL * rms
        : (1 - FLOOR_RISE) * this.noiseFloor + FLOOR_RISE * rms);

      const startBar = this.botSpeaking
        ? Math.max(ECHO_START_RMS, this.noiseFloor * 3)
        : Math.max(START_RMS, this.noiseFloor * 2.5);
      const needMs = this.botSpeaking ? ECHO_MIN_SPEECH_MS : MIN_SPEECH_MS;
      if (rms > startBar) {
        this.speechMs += ms;
        if (this.speechMs >= needMs) {
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
    // Frozen for the duration of the answer — see FLOOR_MULT above.
    const endBar = Math.max(END_RMS, this.noiseFloor * FLOOR_MULT);
    if (rms < endBar) {
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
