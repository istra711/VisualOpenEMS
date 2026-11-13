"""Generate the tutorial soundtrack and score the MP4.

Two original, synthesized tracks are created with numpy/scipy (no third-party
samples) in a temporary folder:

* ``music_ambient.wav``      - slow pads, sub bass, sparse plucks
* ``music_deep_techno.wav``  - 124 BPM kick, rolling bass, hats, pad, arp

The current video is stripped to a temporary silent track, then both versions
are encoded:

* ``IFA_5p8GHz_Tutorial.mp4``              - ambient (default)
* ``IFA_5p8GHz_Tutorial_DeepTechno.mp4``   - deep techno alternate

The temporary WAVs and silent video are not stored in the repository.

Run from the repository root:

    python docs/tutorial/generate_music.py
"""

from __future__ import annotations

import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

TUTORIAL = Path(__file__).resolve().parent
VIDEO = TUTORIAL / "IFA_5p8GHz_Tutorial.mp4"
TECHNO_VIDEO = TUTORIAL / "IFA_5p8GHz_Tutorial_DeepTechno.mp4"
WORK = Path(tempfile.mkdtemp(prefix="ifa_tutorial_music_"))
SILENT = WORK / "silent.mp4"
AMBIENT_WAV = WORK / "music_ambient.wav"
TECHNO_WAV = WORK / "music_deep_techno.wav"

SR = 48_000
TAIL = 0.0
PEAK = 0.89  # about -1 dBFS


# ---------------------------------------------------------------------------
# Helpers


def _t(duration: float) -> np.ndarray:
    return np.arange(int(duration * SR), dtype=np.float32) / SR


def _fade(signal_array: np.ndarray, fade_in: float = 2.0, fade_out: float = 4.0) -> np.ndarray:
    n = len(signal_array)
    a = min(n, int(fade_in * SR))
    r = min(n, int(fade_out * SR))
    if a:
        curve = np.linspace(0.0, 1.0, a, dtype=np.float32) ** 1.5
        signal_array[:a] *= curve[:, None] if signal_array.ndim == 2 else curve
    if r:
        curve = np.linspace(1.0, 0.0, r, dtype=np.float32) ** 1.5
        signal_array[-r:] *= curve[:, None] if signal_array.ndim == 2 else curve
    return signal_array


def _normalise(signal_array: np.ndarray, peak: float = PEAK) -> np.ndarray:
    maximum = float(np.max(np.abs(signal_array)))
    if maximum <= 1e-12:
        return signal_array
    return (signal_array * (peak / maximum)).astype(np.float32)


def _highpass(signal_array: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    sos = signal.butter(order, cutoff / (SR / 2), btype="highpass", output="sos")
    return signal.sosfilt(sos, signal_array).astype(np.float32)


def _lowpass(signal_array: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    cutoff = min(cutoff, SR / 2 * 0.95)
    sos = signal.butter(order, cutoff / (SR / 2), btype="lowpass", output="sos")
    return signal.sosfilt(sos, signal_array).astype(np.float32)


def _bandpass(signal_array: np.ndarray, low: float, high: float, order: int = 2) -> np.ndarray:
    high = min(high, SR / 2 * 0.95)
    sos = signal.butter(order, [low / (SR / 2), high / (SR / 2)], btype="bandpass", output="sos")
    return signal.sosfilt(sos, signal_array).astype(np.float32)


def _soft_clip(signal_array: np.ndarray, drive: float = 1.2) -> np.ndarray:
    return np.tanh(signal_array * drive) / math.tanh(drive)


def _pan(signal_array: np.ndarray, pan: float) -> np.ndarray:
    """Mono -> stereo with a constant-power-ish pan (-1 left, +1 right)."""
    left = math.sqrt(max(0.0, 0.5 * (1.0 - pan)))
    right = math.sqrt(max(0.0, 0.5 * (1.0 + pan)))
    return np.stack([signal_array * left, signal_array * right], axis=1)


def _reverb(left: np.ndarray, right: np.ndarray, wet: float = 0.25) -> tuple[np.ndarray, np.ndarray]:
    """Convolution reverb with a synthetic, stereo-decorrelated impulse."""
    duration = 2.8
    n = int(duration * SR)
    t = np.arange(n, dtype=np.float32) / SR
    rng = np.random.default_rng(7)
    ir_left = rng.standard_normal(n).astype(np.float32) * np.exp(-t / 0.72)
    ir_right = rng.standard_normal(n).astype(np.float32) * np.exp(-t / 0.80)
    ir_left = _lowpass(ir_left, 6500)
    ir_right = _lowpass(ir_right, 6000)
    ir_left /= math.sqrt(float(np.sum(ir_left**2))) or 1.0
    ir_right /= math.sqrt(float(np.sum(ir_right**2))) or 1.0
    wet_left = signal.fftconvolve(left, ir_left, mode="full")[: len(left)]
    wet_right = signal.fftconvolve(right, ir_right, mode="full")[: len(right)]
    return (
        (left * (1.0 - wet) + wet_left * wet).astype(np.float32),
        (right * (1.0 - wet) + wet_right * wet).astype(np.float32),
    )


def _note(midi: float) -> float:
    return 440.0 * 2.0 ** ((midi - 69.0) / 12.0)


def _pad_note(freq: float, duration: float, phase: float = 0.0) -> np.ndarray:
    """Warm additive pad voice: detuned partials with a slow vibrato."""
    t = _t(duration)
    vibrato = 1.0 + 0.0035 * np.sin(2 * np.pi * 0.11 * t + phase)
    out = np.zeros_like(t)
    for detune, gain in ((-0.0035, 0.5), (0.0035, 0.5)):
        f = freq * (1.0 + detune) * vibrato
        for harmonic, weight in ((1, 1.0), (2, 0.36), (3, 0.18), (4, 0.08)):
            out += gain * weight * np.sin(2 * np.pi * f * harmonic * t + phase * harmonic)
    return (out / 2.4).astype(np.float32)


def _pluck(freq: float, duration: float, decay: float = 0.55) -> np.ndarray:
    t = _t(duration)
    env = np.exp(-t / decay).astype(np.float32)
    out = (
        np.sin(2 * np.pi * freq * t)
        + 0.35 * np.sin(2 * np.pi * 2 * freq * t)
        + 0.16 * np.sin(2 * np.pi * 3 * freq * t)
    ) * env
    return (out / 1.5).astype(np.float32)


def _chord_envelope(length: int, attack: float = 1.4, release: float = 2.0) -> np.ndarray:
    env = np.ones(length, dtype=np.float32)
    a = min(length, int(attack * SR))
    r = min(length, int(release * SR))
    if a:
        env[:a] = np.linspace(0.0, 1.0, a, dtype=np.float32) ** 1.4
    if r:
        env[-r:] *= np.linspace(1.0, 0.0, r, dtype=np.float32) ** 1.3
    return env


def _add(buffer: np.ndarray, mono: np.ndarray, start: int, pan: float = 0.0, gain: float = 1.0) -> None:
    if start >= len(buffer):
        return
    mono = mono[: len(buffer) - start]
    stereo = _pan(mono * gain, pan)
    buffer[start : start + len(mono)] += stereo


def _duration() -> float:
    source = SILENT if SILENT.exists() else VIDEO
    if not source.exists():
        raise FileNotFoundError(source)
    probe = shutil.which("ffprobe")
    candidates = [
        Path(r"C:\Program Files\GNU Octave\Octave-9.1.0\mingw64\bin\ffprobe.exe"),
        Path(r"C:\ffmpeg\bin\ffprobe.exe"),
    ]
    if not probe:
        for candidate in candidates:
            if candidate.exists():
                probe = str(candidate)
                break
    if probe:
        try:
            result = subprocess.run(
                [probe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(source)],
                capture_output=True,
                text=True,
                check=True,
            )
            return float(result.stdout.strip()) + TAIL
        except Exception:  # noqa: BLE001
            pass
    return 102.0


# ---------------------------------------------------------------------------
# Ambient


def render_ambient(duration: float) -> np.ndarray:
    n = int(duration * SR)
    pad = np.zeros((n, 2), dtype=np.float32)
    bass = np.zeros((n, 2), dtype=np.float32)
    pluck_bus = np.zeros((n, 2), dtype=np.float32)
    texture = np.zeros((n, 2), dtype=np.float32)

    bpm = 72.0
    beat = 60.0 / bpm
    bar = 4 * beat
    chord_bars = 2
    chord_len = chord_bars * bar

    # Am9, Fmaj7, Cmaj7, G6 (MIDI notes, low to high)
    chords = [
        (45, [57, 64, 67, 71, 72]),   # A2 + E3 G3 B3 C4
        (41, [53, 60, 64, 69]),       # F2 + C3 E3 A3
        (48, [55, 64, 67, 71]),       # C3 + G3 B3 E4
        (43, [50, 59, 62, 67]),       # G2 + D3 B3 E4
    ]
    rng = np.random.default_rng(11)
    pluck_notes = [69, 72, 76, 79, 81, 84]  # A4 C5 E5 G5 A5 C6

    time = 0.0
    chord_index = 0
    while time < duration:
        root, notes = chords[chord_index % len(chords)]
        length = min(chord_len + 2.0, duration - time + 0.5)
        if length <= 0.1:
            break
        env = _chord_envelope(int(length * SR))
        start = int(time * SR)
        # Pad: one panned voice per chord note.
        for note_index, midi in enumerate(notes):
            voice = _pad_note(_note(midi), length, phase=note_index * 0.7 + chord_index)
            pan = -0.65 + 1.3 * (note_index / max(1, len(notes) - 1))
            _add(pad, voice * env, start, pan, gain=0.21)
        # Sub root, one octave below the chord root.
        sub = np.sin(2 * np.pi * _note(root - 12) * _t(length)).astype(np.float32)
        sub_env = _chord_envelope(len(sub), attack=2.0, release=2.5) * 0.32
        _add(bass, sub * sub_env, start, 0.0, gain=0.5)
        # Sparse plucks, every two beats.
        if chord_index % 2 == 1:
            for offset_beats in (0.0, 2.0):
                note = pluck_notes[(chord_index + int(offset_beats)) % len(pluck_notes)]
                when = start + int((offset_beats * beat + 0.4 * beat) * SR)
                _add(pluck_bus, _pluck(_note(note), 2.4, decay=1.1), when, 0.35, 0.14)
        time += chord_len
        chord_index += 1

    # Airy noise texture, slowly breathing.
    noise = rng.standard_normal(n).astype(np.float32)
    noise = _bandpass(noise, 350.0, 2600.0, order=2)
    slow = 0.55 + 0.45 * np.sin(2 * np.pi * 0.045 * np.arange(n) / SR).astype(np.float32)
    texture[:, 0] = noise * slow * 0.012
    texture[:, 1] = np.roll(noise, 137) * slow * 0.012

    # Filter the pad, then add the wet bus.
    pad[:, 0] = _lowpass(pad[:, 0], 1150.0)
    pad[:, 1] = _lowpass(pad[:, 1], 1100.0)
    pluck_bus[:, 0] = _lowpass(pluck_bus[:, 0], 4200.0)
    pluck_bus[:, 1] = _lowpass(pluck_bus[:, 1], 4000.0)
    # Simple delay on the plucks.
    delay = int(0.375 * SR)
    delayed = np.zeros_like(pluck_bus)
    delayed[delay:] = pluck_bus[:-delay] * 0.35
    delayed2 = np.zeros_like(pluck_bus)
    delayed2[2 * delay:] = pluck_bus[:-2 * delay] * 0.16
    wet_left, wet_right = _reverb(
        (pluck_bus[:, 0] + delayed[:, 0] + delayed2[:, 0]),
        (pluck_bus[:, 1] + delayed[:, 1] + delayed2[:, 1]),
        wet=0.42,
    )

    mix_left = pad[:, 0] + bass[:, 0] + wet_left * 0.9 + texture[:, 0]
    mix_right = pad[:, 1] + bass[:, 1] + wet_right * 0.9 + texture[:, 1]
    mix = np.stack([mix_left, mix_right], axis=1)
    mix = _highpass(mix.T, 28.0).T
    mix = _soft_clip(mix, 1.05)
    mix = _fade(_normalise(mix), 2.5, 5.0)
    return mix


# ---------------------------------------------------------------------------
# Deep techno


def render_deep_techno(duration: float) -> np.ndarray:
    n = int(duration * SR)
    kick = np.zeros((n, 2), dtype=np.float32)
    bass = np.zeros((n, 2), dtype=np.float32)
    pad = np.zeros((n, 2), dtype=np.float32)
    hats = np.zeros((n, 2), dtype=np.float32)
    claps = np.zeros((n, 2), dtype=np.float32)
    arp = np.zeros((n, 2), dtype=np.float32)
    texture = np.zeros((n, 2), dtype=np.float32)

    bpm = 124.0
    beat = 60.0 / bpm
    bar = 4 * beat
    sixteenth = beat / 4
    bars = int(math.ceil(duration / bar)) + 1

    rng = np.random.default_rng(23)
    # Am, F, C, G: (root, [chord tones])
    chords = [
        (45, [57, 60, 64, 67]),
        (41, [53, 57, 60, 64]),
        (48, [55, 60, 64, 67]),
        (43, [50, 55, 59, 62]),
    ]

    def chord_for_bar(bar_index: int):
        return chords[(bar_index // 2) % len(chords)]

    # Kick and bass only in the main section and the outro.
    for bar_index in range(bars):
        bar_start = bar_index * bar
        if bar_start >= duration:
            break
        main = 8 <= bar_index < 40
        outro = bar_index >= 48
        intro = bar_index < 8
        break_section = 40 <= bar_index < 48

        root, tones = chord_for_bar(bar_index)

        if main or outro:
            for beat_index in range(4):
                when = bar_start + beat_index * beat
                if when >= duration:
                    break
                # Kick
                length = 0.42
                t = _t(length)
                phase = 2 * np.pi * (
                    44.0 * t + (118.0 - 44.0) * 0.028 * (1.0 - np.exp(-t / 0.028))
                )
                kick_sig = np.sin(phase) * np.exp(-t / 0.21)
                click = rng.standard_normal(len(t)).astype(np.float32)
                click = _highpass(click, 2500.0) * np.exp(-t / 0.004) * 0.25
                _add(kick, (kick_sig + click).astype(np.float32), int(when * SR), 0.0, 0.9)

                # Clap on 2 and 4
                if beat_index in (1, 3):
                    length = 0.24
                    t = _t(length)
                    noise = rng.standard_normal(len(t)).astype(np.float32)
                    noise = _bandpass(noise, 900.0, 4200.0) * np.exp(-t / 0.10)
                    body = np.sin(2 * np.pi * 180.0 * t) * np.exp(-t / 0.06) * 0.35
                    _add(claps, (noise + body).astype(np.float32), int(when * SR), 0.05, 0.28)

                # Closed hat on the offbeat eighth
                hat_when = when + beat / 2
                length = 0.06
                t = _t(length)
                hat_noise = rng.standard_normal(len(t)).astype(np.float32)
                hat_noise = _highpass(hat_noise, 7500.0) * np.exp(-t / 0.018)
                _add(hats, hat_noise.astype(np.float32), int(hat_when * SR), 0.3, 0.16)
                # Open hat on the and of 2 / 4
                if beat_index in (1, 3):
                    length = 0.20
                    t = _t(length)
                    open_noise = rng.standard_normal(len(t)).astype(np.float32)
                    open_noise = _highpass(open_noise, 6500.0) * np.exp(-t / 0.09)
                    _add(hats, open_noise.astype(np.float32), int((hat_when + 0.02) * SR), -0.35, 0.12)

        # Bass: rolling 16ths, only main and outro.
        if main or outro:
            pattern = [0, 0, 12, 0, 7, 0, 12, 0, 0, 12, 0, 7, 0, 0, 12, 0]
            for step in range(16):
                when = bar_start + step * sixteenth
                if when >= duration:
                    break
                midi = root - 12 + pattern[step]
                length = min(0.23, sixteenth * 1.8)
                t = _t(length)
                freq = _note(midi)
                wave = (
                    np.sin(2 * np.pi * freq * t)
                    + 0.35 * np.sin(2 * np.pi * 2 * freq * t)
                    + 0.18 * np.sign(np.sin(2 * np.pi * freq * t))
                )
                env = (1.0 - np.exp(-t / 0.006)) * np.exp(-t / 0.16)
                _add(bass, (wave * env).astype(np.float32), int(when * SR), 0.0, 0.30)

        # Pad: every two bars, low-passed and sidechained later.
        if bar_index % 2 == 0 and not (bar_index >= 52):
            length = 2 * bar + 1.5
            if bar_start + length > duration:
                length = duration - bar_start + 0.5
            env = _chord_envelope(int(length * SR), attack=0.7, release=1.5)
            for note_index, midi in enumerate(tones):
                voice = _pad_note(_note(midi), length, phase=note_index * 0.9 + bar_index)
                pan = -0.5 + 1.0 * (note_index / max(1, len(tones) - 1))
                _add(pad, voice * env, int(bar_start * SR), pan, 0.12)

        # Arp from bar 16 onward (lower level in the break).
        if bar_index >= 16:
            seq = [0, 2, 1, 3, 2, 0, 3, 1]
            for step in range(16):
                when = bar_start + step * sixteenth
                if when >= duration:
                    break
                midi = tones[seq[step % len(seq)]] + 12
                level = 0.11 if not break_section else 0.12
                _add(arp, _pluck(_note(midi), 0.45, decay=0.22), int(when * SR), 0.25 if step % 2 else -0.25, level)

    # Sidechain envelope: dip bass/pad on every kick.
    sidechain = np.ones(n, dtype=np.float32)
    for bar_index in range(bars):
        if not (8 <= bar_index < 40 or bar_index >= 48):
            continue
        for beat_index in range(4):
            start = int((bar_index * bar + beat_index * beat) * SR)
            if start >= n:
                break
            length = min(int(0.36 * SR), n - start)
            t = np.arange(length, dtype=np.float32) / SR
            dip = 1.0 - 0.58 * np.exp(-t / 0.13)
            sidechain[start : start + length] = np.minimum(
                sidechain[start : start + length], dip
            )

    # Filtering and processing.
    bass[:, 0] = _lowpass(bass[:, 0], 420.0, order=3)
    bass[:, 1] = _lowpass(bass[:, 1], 420.0, order=3)
    bass = np.tanh(bass * 1.5).astype(np.float32) * 0.9
    pad[:, 0] = _lowpass(pad[:, 0], 900.0)
    pad[:, 1] = _lowpass(pad[:, 1], 880.0)
    pad *= sidechain[:, None]
    bass *= sidechain[:, None]

    arp[:, 0] = _lowpass(arp[:, 0], 5200.0)
    arp[:, 1] = _lowpass(arp[:, 1], 5000.0)
    # Ping-pong-ish delay.
    delay = int(sixteenth * 3 * SR)
    delayed = np.zeros_like(arp)
    delayed[delay:, 0] = arp[:-delay, 1] * 0.30
    delayed[delay:, 1] = arp[:-delay, 0] * 0.30
    wet_left, wet_right = _reverb(
        arp[:, 0] + delayed[:, 0], arp[:, 1] + delayed[:, 1], wet=0.34
    )

    # Slow noise rise in the break.
    noise = rng.standard_normal(n).astype(np.float32)
    noise = _bandpass(noise, 300.0, 5000.0)
    t_all = np.arange(n, dtype=np.float32) / SR
    sweep = np.clip((t_all - 75.0) / 18.0, 0.0, 1.0) * np.clip((96.0 - t_all) / 4.0, 0.0, 1.0)
    texture[:, 0] = noise * sweep * 0.035
    texture[:, 1] = np.roll(noise, 211) * sweep * 0.035

    mix = (
        kick * 0.9
        + bass
        + pad
        + hats
        + claps
        + wet_left[:, None] * 0.75
        + wet_right[:, None] * 0.75
        + texture
    )
    mix = _highpass(mix.T, 26.0).T
    mix = _soft_clip(mix, 1.15)
    mix = _fade(_normalise(mix), 2.0, 5.0)
    return mix


# ---------------------------------------------------------------------------
# Video muxing


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


def score(silent: Path, music: Path, output: Path, duration: float) -> None:
    command = [
        _ffmpeg(),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(silent),
        "-i",
        str(music),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-af",
        "loudnorm=I=-19:TP=-2:LRA=7",
        "-t",
        f"{duration:.3f}",
        "-movflags",
        "+faststart",
        str(output),
    ]
    subprocess.run(command, check=True)


def _ensure_silent() -> None:
    """Strip the audio from the current video so the music is never doubled."""
    if not VIDEO.exists():
        raise FileNotFoundError(VIDEO)
    command = [
        _ffmpeg(),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(VIDEO),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-an",
        str(SILENT),
    ]
    subprocess.run(command, check=True)


def main() -> int:
    _ensure_silent()
    duration = _duration()
    print(f"duration: {duration:.1f} s")

    print("rendering ambient ...")
    ambient = render_ambient(duration)
    sf.write(AMBIENT_WAV, ambient, SR, subtype="PCM_24")
    print(
        f"  {AMBIENT_WAV.name}: {len(ambient)/SR:.1f}s, peak {np.max(np.abs(ambient)):.3f}"
    )

    print("rendering deep techno ...")
    techno = render_deep_techno(duration)
    sf.write(TECHNO_WAV, techno, SR, subtype="PCM_24")
    print(
        f"  {TECHNO_WAV.name}: {len(techno)/SR:.1f}s, peak {np.max(np.abs(techno)):.3f}"
    )

    print("muxing ambient version (default) ...")
    score(SILENT, AMBIENT_WAV, VIDEO, duration)
    print("muxing deep techno version ...")
    score(SILENT, TECHNO_WAV, TECHNO_VIDEO, duration)

    for path in (VIDEO, TECHNO_VIDEO):
        print(f"  {path.name}: {path.stat().st_size/1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
