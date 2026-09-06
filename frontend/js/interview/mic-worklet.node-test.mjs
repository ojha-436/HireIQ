/* Executable regression test for the mic-worklet VAD's safety valve.
 *
 * Reported bug: a candidate's mic tile stayed on "YOU ARE SPEAKING" forever and
 * nothing ever reached the transcript. Root cause: once `speaking` flips true, the
 * ONLY way back to false is the RMS staying under END_RMS for HANG_MS straight. A
 * room whose ambient noise floor sits at or above END_RMS never produces that quiet
 * gap, so speech_end never fires and the candidate's turn never settles server-side
 * either. MAX_SPEECH_MS forces a boundary regardless of RMS so this can't lock up.
 *
 * mic-worklet.js runs in AudioWorkletGlobalScope (registerProcessor, a bare
 * AudioWorkletProcessor base class, a global `sampleRate`) which plain Node does not
 * provide, so this stubs exactly those three names, loads the real source file
 * unmodified, and drives the captured class directly -- no browser, no build step,
 * matching this repo's no-bundler policy.
 *
 * Run directly: node frontend/js/interview/mic-worklet.node-test.mjs
 * Wired into the suite via backend/tests/test_mic_worklet_vad.py (skips if `node`
 * is not on PATH -- this is the only Node-executed test in the repo).
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import vm from 'node:vm';

const here = path.dirname(fileURLToPath(import.meta.url));
const source = readFileSync(path.join(here, 'mic-worklet.js'), 'utf8');

let CapturedClass = null;
const sandbox = {
  sampleRate: 48000,
  registerProcessor: (_name, cls) => { CapturedClass = cls; },
  // The real base class supplies `this.port` (a MessagePort) before the subclass
  // constructor body runs; mic-worklet.js's constructor assigns `this.port.onmessage`
  // immediately, so the stub needs a port object in place first.
  AudioWorkletProcessor: class {
    constructor() { this.port = { postMessage() {}, onmessage: null }; }
  },
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: 'mic-worklet.js' });

if (!CapturedClass) throw new Error('registerProcessor was never called — nothing to test');

function assert(cond, msg) {
  if (!cond) { console.error('FAIL:', msg); process.exitCode = 1; }
}

// --- a persistently loud room: RMS well above both START_RMS and END_RMS, never quiet ---
function loudFrame(n = 128, amplitude = 0.03) {
  return new Float32Array(n).fill(amplitude);
}

{
  const mic = new CapturedClass();
  const events = [];
  mic.port = { postMessage: (m) => events.push(m), onmessage: null };

  // Enough frames to cross MIN_SPEECH_MS and trigger speech_start.
  for (let i = 0; i < 200 && !mic.speaking; i++) mic._vad(loudFrame());
  assert(mic.speaking === true, 'expected speech_start to fire on sustained loud input');
  assert(events.some((e) => e.type === 'speech_start'), 'expected a speech_start message');

  // Keep feeding loud frames well past MAX_SPEECH_MS with NO quiet gap at all -- this
  // is exactly the "noisy room" case the old code could never recover from.
  const framesFor25s = Math.ceil(25000 / ((128 / 48000) * 1000));
  for (let i = 0; i < framesFor25s && mic.speaking; i++) mic._vad(loudFrame());

  assert(mic.speaking === false,
    'the safety valve must force speech_end even though the input never quieted down '
    + '-- without it this loops forever, which is the reported bug');
  assert(events.some((e) => e.type === 'speech_end'),
    'expected a speech_end message once MAX_SPEECH_MS was exceeded');
}

// --- a normal quiet pause still ends speech the fast way, unaffected by the valve ---
{
  const mic = new CapturedClass();
  const events = [];
  mic.port = { postMessage: (m) => events.push(m), onmessage: null };

  for (let i = 0; i < 200 && !mic.speaking; i++) mic._vad(loudFrame());
  assert(mic.speaking === true, 'setup: expected speech_start before testing quiet-pause end');

  const silence = new Float32Array(128).fill(0);
  const framesFor1s = Math.ceil(1000 / ((128 / 48000) * 1000));
  for (let i = 0; i < framesFor1s && mic.speaking; i++) mic._vad(silence);

  assert(mic.speaking === false, 'a genuine quiet pause (HANG_MS) must still end speech fast');
  const speechEndCount = events.filter((e) => e.type === 'speech_end').length;
  assert(speechEndCount === 1, `expected exactly one speech_end from the quiet pause, got ${speechEndCount}`);
}

if (process.exitCode) {
  console.error('mic-worklet VAD regression test FAILED');
} else {
  console.log('mic-worklet VAD regression test passed');
}
