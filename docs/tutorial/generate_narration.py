"""Add an English narrator and slow the tutorial to the narration pace.

The slide metadata comes from ``assets/narration.json`` (written by
``generate_tutorial.py``).  Each slide is spoken by the offline Windows voice
"Microsoft David" (SAPI).  Slide durations are at least twice the old 4.2 s
hold, and longer whenever the narration needs the time.  The existing ambient
or deep-techno soundtrack is mixed underneath at a low level and ducked while
the narrator speaks.

Outputs:

* ``IFA_5p8GHz_Tutorial.mp4``            - narrated + ambient (default)
* ``IFA_5p8GHz_Tutorial_DeepTechno.mp4`` - narrated + deep techno

Run from the repository root:

    python docs/tutorial/generate_narration.py
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image
from scipy import signal
from scipy.ndimage import uniform_filter1d

TUTORIAL = Path(__file__).resolve().parent
ASSETS = TUTORIAL / "assets"
FRAMES = TUTORIAL / "frames"
VIDEO = TUTORIAL / "IFA_5p8GHz_Tutorial.mp4"
TECHNO_VIDEO = TUTORIAL / "IFA_5p8GHz_Tutorial_DeepTechno.mp4"
NARRATION_JSON = ASSETS / "narration.json"
TIMINGS_JSON = ASSETS / "narration_timings.json"
WORK = Path(tempfile.mkdtemp(prefix="ifa_tutorial_voice_"))

SR = 48_000
FPS = 24
MIN_HOLD = 8.4        # twice the old 4.2 s
FADE = 0.7            # twice the old 0.35 s
START_PAD = 0.45
END_PAD = 1.2
VOICE_RATE = 3        # SAPI rate: -10...+10, clear tutorial pace
VOICE_NAME = "David"

if str(TUTORIAL) not in sys.path:
    sys.path.insert(0, str(TUTORIAL))


# ---------------------------------------------------------------------------
# Text and TTS


_REPLACEMENTS = {
    "GND_bottom": "ground bottom",
    "GND_top": "ground top",
    "IFA_arm": "I F A arm",
    "IFA_short": "I F A short",
    "IFA_feed": "I F A feed",
    "PCB_FR4": "P C B F R four",
    "FR4": "F R four",
    "NF2FF": "N F two F F",
    "S-Params": "S parameters",
    "S-parameters": "S parameters",
    "Project/Geometry": "Project and Geometry",
    "status/log": "status and log",
    "Re/Im": "real and imaginary",
    "zmin": "z min",
    "|S11|": "S eleven",
    "|S|": "S magnitude",
    "S11": "S eleven",
    "VSWR": "V S W R",
    "3D": "three D",
    "2D": "two D",
    "eps_r": "epsilon r",
    "tan_d": "loss tangent",
    "lambda0": "lambda zero",
    "Ctrl+Shift+P": "Control Shift P",
    "Ctrl+Shift+M": "Control Shift M",
    "Ctrl+Shift+D": "Control Shift D",
    "Ctrl+Shift+S": "Control Shift S",
    "Ctrl+N": "Control N",
    "Ctrl+S": "Control S",
    "Ctrl+M": "Control M",
    "Ctrl+R": "Control R",
    "Ctrl+D": "Control D",
    "Ctrl+L": "Control L",
    "->": " to ",
    "...": " to ",
    "|": " ",
    "**": "",
    "\u2026": ", ",
    "\u2013": ", ",
    "\u2014": ", ",
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u00a0": " ",
}


def _clean(text: str) -> str:
    text = str(text)
    for old, new in _REPLACEMENTS.items():
        text = text.replace(old, new)
    text = text.replace(" / ", ", ").replace("/", ", ")
    text = text.replace(" mm", " millimeters").replace(" GHz", " gigahertz")
    text = text.replace(" dBi", " d B i").replace(" dB", " decibels")
    text = text.replace(" ohm", " ohms")
    text = text.replace(".:", ":").replace(" .", ".").replace("..", ".")
    text = text.replace(", :", ":").replace(" ,", ",").replace("  ", " ")
    text = text.replace("0.5 millimeters gap", "0.5 millimeter gap")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _script(entry: dict) -> str:
    parts: list[str] = []
    step = entry.get("step")
    if step:
        parts.append(f"Step {step}.")
    parts.append(str(entry.get("title", "")) + ".")
    parts.append(str(entry.get("caption", "")))
    side = entry.get("side_card")
    if side and side[0] in ("Window map",):
        parts.append(str(side[0]) + ":")
        parts.extend(str(line) for line in side[1])
    return _clean(" ".join(parts))


def _voice():
    import win32com.client

    voice = win32com.client.Dispatch("SAPI.SpVoice")
    voices = voice.GetVoices()
    chosen = None
    for index in range(voices.Count):
        description = voices.Item(index).GetDescription()
        if VOICE_NAME.lower() in description.lower():
            chosen = voices.Item(index)
            break
    if chosen is None:
        for index in range(voices.Count):
            description = voices.Item(index).GetDescription().lower()
            if "english" in description or "david" in description:
                chosen = voices.Item(index)
                break
    if chosen is None and voices.Count:
        chosen = voices.Item(0)
    if chosen is not None:
        voice.Voice = chosen
    voice.Rate = VOICE_RATE
    voice.Volume = 100
    return voice


def _synthesize(voice, text: str, path: Path) -> None:
    import win32com.client

    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    stream.Open(str(path), 3, False)  # SSFMCreateForWrite
    voice.AudioOutputStream = stream
    voice.Speak(text)
    stream.Close()


def _read_voice(path: Path) -> np.ndarray:
    audio, sample_rate = sf.read(path, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sample_rate != SR:
        gcd = math.gcd(int(SR), int(sample_rate))
        audio = signal.resample_poly(audio, SR // gcd, sample_rate // gcd).astype(np.float32)
    audio = np.asarray(audio, dtype=np.float32)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 1e-9:
        audio *= 0.85 / peak
    pad = int(0.10 * SR)
    return np.pad(audio, (pad, pad))


# ---------------------------------------------------------------------------
# Timing and video


def _timeline(voice_arrays: list[np.ndarray]) -> tuple[list[float], list[float], float]:
    durations = [len(audio) / SR for audio in voice_arrays]
    holds = [max(MIN_HOLD, duration + START_PAD + END_PAD) for duration in durations]
    starts: list[float] = []
    cursor = 0.0
    for index, hold in enumerate(holds):
        starts.append(cursor)
        cursor += hold
        if index + 1 < len(holds):
            cursor += FADE
    return holds, starts, cursor


def _voice_track(voice_arrays: list[np.ndarray], starts: list[float], total: float) -> np.ndarray:
    n = int(math.ceil(total * SR))
    track = np.zeros(n, dtype=np.float32)
    for audio, start in zip(voice_arrays, starts):
        index = int((start + START_PAD) * SR)
        end = min(n, index + len(audio))
        if index < n:
            track[index:end] += audio[: end - index]
    track = signal.sosfilt(
        signal.butter(2, 80 / (SR / 2), btype="highpass", output="sos"), track
    ).astype(np.float32)
    peak = float(np.max(np.abs(track))) if track.size else 0.0
    if peak > 1e-9:
        track *= 0.92 / peak
    return np.stack([track, track], axis=1)


def _encode_frames(slides: list[Image.Image], holds: list[float]) -> tuple[Path, int]:
    frame_dir = WORK / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    index = 0
    fade_frames = max(1, int(FADE * FPS))
    for slide_index, slide in enumerate(slides):
        for _ in range(max(1, int(holds[slide_index] * FPS))):
            slide.save(frame_dir / f"{index:05d}.jpg", quality=90)
            index += 1
        if slide_index + 1 < len(slides):
            following = slides[slide_index + 1]
            for step in range(1, fade_frames + 1):
                blended = Image.blend(slide, following, step / (fade_frames + 1))
                blended.save(frame_dir / f"{index:05d}.jpg", quality=90)
                index += 1
    return frame_dir, index


def _mix(voice: np.ndarray, music: np.ndarray) -> np.ndarray:
    n = min(len(voice), len(music))
    voice = voice[:n]
    music = music[:n]
    mono = np.mean(np.abs(voice), axis=1)
    envelope = uniform_filter1d(mono, size=max(1, int(0.16 * SR)))
    peak = float(np.max(envelope)) if envelope.size else 0.0
    if peak > 1e-9:
        envelope = envelope / peak
    duck = 1.0 - 0.62 * envelope
    mixed = voice + music * (0.36 * duck[:, None])
    peak = float(np.max(np.abs(mixed))) if mixed.size else 0.0
    if peak > 0.98:
        mixed *= 0.98 / peak
    return mixed.astype(np.float32)


def _ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path:
        return path
    for candidate in (
        Path(r"C:\Program Files\GNU Octave\Octave-9.1.0\mingw64\bin\ffmpeg.exe"),
        Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
    ):
        if candidate.exists():
            return str(candidate)
    raise RuntimeError("ffmpeg not found")


def _mux(frame_dir: Path, audio: Path, output: Path, frames: int) -> None:
    command = [
        _ffmpeg(),
        "-y",
        "-loglevel",
        "error",
        "-framerate",
        str(FPS),
        "-i",
        str(frame_dir / "%05d.jpg"),
        "-i",
        str(audio),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "22",
        "-vf",
        "scale=1280:720:flags=lanczos",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-af",
        "loudnorm=I=-17:TP=-2:LRA=7",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output),
    ]
    subprocess.run(command, check=True)


def main() -> int:
    if not NARRATION_JSON.exists():
        raise FileNotFoundError(NARRATION_JSON)
    entries = json.loads(NARRATION_JSON.read_text(encoding="utf-8"))
    slide_paths = sorted(FRAMES.glob("[0-9][0-9].png"))
    if len(slide_paths) != len(entries):
        raise RuntimeError(
            f"slide count mismatch: {len(slide_paths)} PNGs vs {len(entries)} narrations"
        )

    print(f"voice: Microsoft {VOICE_NAME}, rate {VOICE_RATE}")
    voice = _voice()
    voice_arrays: list[np.ndarray] = []
    for index, entry in enumerate(entries, start=1):
        text = _script(entry)
        wav = WORK / f"voice_{index:02d}.wav"
        _synthesize(voice, text, wav)
        audio = _read_voice(wav)
        voice_arrays.append(audio)
        print(f"  {index:02d} {len(audio)/SR:5.2f}s  {text[:70]}...")

    holds, starts, total = _timeline(voice_arrays)
    print(f"total video: {total/60:.1f} min ({total:.1f} s)")
    timings = []
    for index, entry in enumerate(entries):
        timings.append(
            {
                "step": entry.get("step"),
                "title": entry.get("title"),
                "start": round(starts[index], 3),
                "hold": round(holds[index], 3),
                "voice": round(len(voice_arrays[index]) / SR, 3),
            }
        )
    TIMINGS_JSON.write_text(
        json.dumps(
            {"duration": round(total, 3), "slides": timings}, indent=2, ensure_ascii=False
        ),
        encoding="utf-8",
    )

    slides = [Image.open(path).convert("RGB") for path in slide_paths]
    frame_dir, frame_count = _encode_frames(slides, holds)

    voice_track = _voice_track(voice_arrays, starts, total)

    # Music is rendered by the existing synth module at the new duration.
    import generate_music

    print("rendering ambient bed ...")
    ambient = generate_music.render_ambient(total)
    ambient_path = WORK / "ambient.wav"
    sf.write(ambient_path, ambient, SR, subtype="PCM_24")
    ambient_mix = _mix(voice_track, ambient)
    ambient_wav = WORK / "mix_ambient.wav"
    sf.write(ambient_wav, ambient_mix, SR, subtype="PCM_24")
    print("muxing narrated ambient video ...")
    _mux(frame_dir, ambient_wav, VIDEO, frame_count)

    print("rendering deep techno bed ...")
    techno = generate_music.render_deep_techno(total)
    techno_path = WORK / "techno.wav"
    sf.write(techno_path, techno, SR, subtype="PCM_24")
    techno_mix = _mix(voice_track, techno)
    techno_wav = WORK / "mix_techno.wav"
    sf.write(techno_wav, techno_mix, SR, subtype="PCM_24")
    print("muxing narrated deep techno video ...")
    _mux(frame_dir, techno_wav, TECHNO_VIDEO, frame_count)

    print(f"  {VIDEO.name}: {VIDEO.stat().st_size/1e6:.1f} MB")
    print(f"  {TECHNO_VIDEO.name}: {TECHNO_VIDEO.stat().st_size/1e6:.1f} MB")
    print(f"  timings: {TIMINGS_JSON}")

    # Refresh the storyboard chapter timings.
    try:
        import generate_tutorial

        generate_tutorial._write_storyboard3([], FRAMES / "01.png")
        print(f"  storyboard: {TUTORIAL / 'README.md'}")
    except Exception as error:  # noqa: BLE001
        print(f"  storyboard refresh skipped: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
