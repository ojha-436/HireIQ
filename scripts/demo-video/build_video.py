"""Assemble the HireIQ product tour: cards + screen capture + narration.

Every clip is rendered to a self-contained mp4 with its own audio track first, then
concatenated. Doing it that way keeps audio and video in lockstep — a single giant
filtergraph drifts as soon as one source has a slightly different frame rate, which the
browser captures do.
"""

from __future__ import annotations

# --- paths: resolved from this file, so the scripts work in any checkout ---
import os as _os
import pathlib as _pl
import sys as _sys

REPO = _pl.Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO / "backend"
#: Everything the shoot produces. Override with HIREIQ_VIDEO_DIR; gitignored by default.
WORKDIR = _pl.Path(_os.environ.get("HIREIQ_VIDEO_DIR", REPO / ".demo-video"))
WORKDIR.mkdir(parents=True, exist_ok=True)
if str(BACKEND_DIR) not in _sys.path:
    _sys.path.insert(0, str(BACKEND_DIR))
# --- end paths ---

import hashlib
import pathlib
import subprocess
import sys
import wave



S = WORKDIR
CARDS, VID = S / "cards", S / "video"
WORK = S / "build"
WORK.mkdir(exist_ok=True)
# macOS `say` reads the words correctly and sounds like a machine doing it: flat pitch
# contour, no breath, every sentence the same shape. Gemini's TTS takes a direction like
# a voice artist would and delivers the line, so that is the narrator; `say` stays only
# as the offline fallback.
TTS_MODEL = "gemini-3.1-flash-tts-preview"
TTS_VOICE = "Charon"
TTS_DIRECTION = (
    "You are narrating an enterprise product film. Read the following in a warm, "
    "measured, confident voice — an experienced documentary narrator, not an "
    "advertisement. Unhurried. Let the punctuation breathe, and land the end of each "
    "sentence rather than rushing into the next. Slightly lift the phrases that carry "
    "the point. Never sing-song, never chirpy.\n\nRead this:\n\n")
FALLBACK_VOICE, FALLBACK_RATE = "Daniel", 168
W, H, FPS = 1600, 900, 30
#: Slowest a captured clip may be stretched before it looks like a stall.
MIN_SPEED = 0.45

#: Seconds to drop from the head of a capture. Each segment begins by signing in, which
#: only the first one narrates — everywhere else it is dead air over a login form while
#: the voice-over has already moved on to the assessment.
TRIM = {"s2": 6.5, "s3": 6.0, "s4": 5.5, "s5": 7.0}


def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(" ".join(str(c) for c in cmd[:14]), "...")
        print(r.stderr[-1500:])
        raise SystemExit("ffmpeg failed")
    return r


def dur(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float(r.stdout.strip())


def _say_fallback(text, wav):
    aiff = wav.with_suffix(".aiff")
    subprocess.run(["say", "-v", FALLBACK_VOICE, "-r", str(FALLBACK_RATE),
                    "-o", str(aiff), text], check=True)
    sh(["ffmpeg", "-y", "-i", str(aiff), "-ar", "48000", "-ac", "2", str(wav)])


def narrate(text, tag):
    """Narration line -> 48 kHz stereo wav, and its duration.

    Cached on the text itself: a rebuild that only changes pacing should not re-bill or
    re-roll fourteen TTS calls, and re-rolling would also change every duration the
    timeline was just balanced against.
    """
    key = hashlib.sha1((TTS_MODEL + TTS_VOICE + text).encode()).hexdigest()[:12]
    wav = WORK / f"vo_{tag}_{key}.wav"
    if wav.exists():
        return wav, dur(wav)

    try:
        from app.config import settings
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        resp = client.models.generate_content(
            model=TTS_MODEL, contents=TTS_DIRECTION + text,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=TTS_VOICE)))))
        pcm = resp.candidates[0].content.parts[0].inline_data.data
        raw = WORK / f"raw_{tag}.wav"
        with wave.open(str(raw), "wb") as w:      # the API returns 24 kHz mono PCM16
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(pcm)
        # Gentle polish: a high-pass to clear rumble, mild compression so the quiet ends
        # of sentences stay audible under the footage, and a little headroom.
        sh(["ffmpeg", "-y", "-i", str(raw),
            "-af", "highpass=f=75,acompressor=threshold=-18dB:ratio=2.5:attack=8:release=180,"
                   "loudnorm=I=-18:TP=-2:LRA=11",
            "-ar", "48000", "-ac", "2", str(wav)])
    except Exception as exc:  # noqa: BLE001 — a narration failure must not lose the build
        print(f"   ! TTS failed for {tag} ({type(exc).__name__}: {str(exc)[:90]}); "
              f"falling back to say")
        _say_fallback(text, wav)
    return wav, dur(wav)


# --------------------------------------------------------------------- script
# Narration is written to be true to what is on screen. Nothing here claims a
# capability the recording does not actually show.
from script_v2 import CARD_VO, SEG_VO  # noqa: E402

#: (kind, key, source, speed). Speed only applies to captured footage.
TIMELINE = [
    ("card", "00_open", None, 1.0),
    ("card", "01_post", None, 1.0),
    ("seg", "s1", VID / "s1.webm", 1.0),
    ("card", "02_match", None, 1.0),
    ("seg", "s2", VID / "s2.webm", 1.0),
    ("card", "03_interview", None, 1.0),
    # The panel thinks and speaks at human pace. A gentle 1.5x trims the dead air
    # without outrunning the viewer: the transcript IS the evidence, and it has to stay
    # readable on screen.
    ("seg", "s3", VID / "s3.webm", 1.5),
    ("card", "04_score", None, 1.0),
    # Slowed slightly: this is the screen the whole video exists to explain.
    ("seg", "s4", VID / "s4.webm", 1.0),
    ("card", "05_candidate", None, 1.0),
    ("seg", "s5", VID / "s5.webm", 1.0),
    ("card", "06_admin", None, 1.0),
    ("seg", "s6", VID / "s6.webm", 1.0),
    ("card", "07_close", None, 1.0),
]

parts = []
total = 0.0
for kind, key, src, speed in TIMELINE:
    text = CARD_VO[key] if kind == "card" else SEG_VO[key]
    wav, vo_len = narrate(text, key)
    out = WORK / f"{key}.mp4"

    if kind == "card":
        # Hold a beat after the line lands, and never flash by faster than 5s.
        length = max(vo_len + 1.5, 5.0)
        frames = int(length * FPS)
        # Slow push in, from a 2x render so it stays sharp.
        vf = (f"scale={W*2}:{H*2},"
              f"zoompan=z='min(1.0+0.00035*on,1.09)':d={frames}:s={W}x{H}:fps={FPS},"
              f"fade=t=in:st=0:d=0.7,fade=t=out:st={length-0.7:.2f}:d=0.7,format=yuv420p")
        sh(["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(CARDS / f"{key}.png"),
            "-i", str(wav),
            "-filter_complex", f"[0:v]{vf}[v];[1:a]adelay=350|350,apad[a]",
            "-map", "[v]", "-map", "[a]", "-t", f"{length:.2f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(out)])
    else:
        cut = TRIM.get(key, 0.0)
        raw = dur(src) - cut
        # Never let the footage run out from under the line. Rather than cutting the
        # sentence — or freezing on a dead frame for ten seconds, which reads as a
        # stall — stretch the playback so the screen keeps moving until the line lands.
        # These are mostly slow scrolls over static screens, so they take it well.
        length = max(raw / speed, vo_len + 1.4)
        speed = raw / length
        hold = 0.0
        if speed < MIN_SPEED:                 # too sluggish to watch; hold the tail instead
            speed = MIN_SPEED
            hold = length - raw / speed
            print(f"  {key:<12} holding last frame {hold:.1f}s (already at {MIN_SPEED}x)")
        pad = f",tpad=stop_mode=clone:stop_duration={hold:.2f}" if hold > 0.05 else ""
        vf = (f"setpts={1/speed:.5f}*PTS,scale={W}:{H}:force_original_aspect_ratio=decrease,"
              f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0a0e0c{pad},"
              f"fade=t=in:st=0:d=0.5,fade=t=out:st={max(length-0.5,0.1):.2f}:d=0.5,"
              f"format=yuv420p")
        sh(["ffmpeg", "-y"] + (["-ss", f"{cut:.2f}"] if cut else []) +
           ["-i", str(src), "-i", str(wav),
            "-filter_complex", f"[0:v]{vf}[v];[1:a]adelay=250|250,apad[a]",
            "-map", "[v]", "-map", "[a]", "-t", f"{length:.2f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(out)])

    d = dur(out)
    total += d
    parts.append(out)
    flag = "  <-- VO longer than clip" if vo_len > d + 0.5 else ""
    print(f"  {key:<12} clip {d:6.1f}s   vo {vo_len:5.1f}s{flag}")

listing = WORK / "concat.txt"
listing.write_text("".join(f"file '{p}'\n" for p in parts))
final = S / "HireIQ_product_tour.mp4"
sh(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
    "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(final)])

print(f"\n{final}")
print(f"runtime {dur(final)/60:.2f} min ({dur(final):.0f}s), "
      f"{final.stat().st_size/1048576:.1f} MB")
