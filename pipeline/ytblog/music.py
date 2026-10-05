"""릴스 배경음악: 저작권 걱정 없는 자체 생성 로파이(lo-fi) 트랙.

영상마다 영상 ID 로 조(key)·진행·빠르기를 바꿔 매번 조금씩 다른 곡이 된다.
pipeline/music/ 폴더에 mp3·wav·m4a 를 넣어 두면(예: 유튜브 오디오 보관함의 '저작자 표시 불필요' 곡) 그 곡을 대신 쓴다.

  python -m ytblog music <영상ID> [초]   → out/<영상ID>/images/reel/music.wav
"""
from __future__ import annotations

import hashlib
import subprocess
import wave
from pathlib import Path

import numpy as np

from .config import PIPELINE_DIR

SR = 44100
MUSIC_DIR = PIPELINE_DIR / "music"

QUALITY = {"maj7": [0, 4, 7, 11], "m7": [0, 3, 7, 10], "dom7": [0, 4, 7, 10], "maj9": [0, 4, 7, 11, 14], "m9": [0, 3, 7, 10, 14]}
PROGRESSIONS = [
    [(0, "maj9"), (9, "m7"), (5, "maj7"), (7, "dom7")],      # I  vi  IV  V
    [(2, "m9"), (7, "dom7"), (0, "maj9"), (9, "m7")],        # ii V   I   vi
    [(5, "maj7"), (4, "m7"), (9, "m9"), (0, "maj7")],        # IV iii vi  I
    [(0, "maj7"), (4, "m7"), (5, "maj9"), (5, "m7")],        # I  iii IV  iv
    [(9, "m9"), (5, "maj7"), (0, "maj7"), (7, "dom7")],      # vi IV  I   V
]
PENTA = [0, 2, 4, 7, 9]


def _rng(seed: str) -> np.random.Generator:
    return np.random.default_rng(int(hashlib.md5(seed.encode()).hexdigest()[:8], 16))


def _hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    """2차(1차 두 번) 저역통과를 주파수 영역에서 한 번에 적용한다(긴 신호도 빠름)."""
    n = len(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    h = 1 / (1 + 1j * f / cutoff) ** 2
    return np.fft.irfft(np.fft.rfft(x) * h, n)


def _ep(f: float, dur: float, vel: float) -> np.ndarray:
    """전자피아노(로즈) 느낌: 기음 + 빨리 사라지는 배음 + 살짝 어긋난 두 번째 소리(코러스)."""
    n = int(SR * (dur + 0.6))
    t = np.arange(n) / SR
    tone = (np.sin(2 * np.pi * f * t) + 0.5 * np.sin(2 * np.pi * f * 1.0035 * t + 0.7)
            + 0.30 * np.exp(-t * 4) * np.sin(2 * np.pi * 2 * f * t)
            + 0.10 * np.exp(-t * 9) * np.sin(2 * np.pi * 3.01 * f * t))
    env = np.minimum(t / 0.006, 1) * (0.28 + 0.72 * np.exp(-t * 1.6))
    rel = np.clip((dur + 0.6 - t) / 0.6, 0, 1)          # 음 끝에서 부드럽게
    env *= np.where(t > dur, rel, 1)
    return vel * tone * env


def _bell(f: float, vel: float) -> np.ndarray:
    n = int(SR * 1.6)
    t = np.arange(n) / SR
    return vel * (np.sin(2 * np.pi * f * t) + 0.25 * np.sin(2 * np.pi * 2.0 * f * t) * np.exp(-t * 6)) * np.exp(-t * 2.6) * np.minimum(t / 0.004, 1)


def _bass(f: float, dur: float, vel: float) -> np.ndarray:
    n = int(SR * (dur + 0.05))
    t = np.arange(n) / SR
    return vel * (np.sin(2 * np.pi * f * t) + 0.18 * np.sin(2 * np.pi * 2 * f * t)) * np.minimum(t / 0.01, 1) * np.exp(-t * 1.8) * np.clip((dur + 0.05 - t) / 0.05, 0, 1)


def _kick(rng) -> np.ndarray:
    t = np.arange(int(SR * 0.4)) / SR
    freq = 46 + 85 * np.exp(-t * 32)
    ph = 2 * np.pi * np.cumsum(freq) / SR
    return (np.sin(ph) * np.exp(-t * 8.5) + 0.25 * np.exp(-t * 200) * rng.standard_normal(len(t)) * 0.3) * 0.9


def _snare(rng) -> np.ndarray:
    t = np.arange(int(SR * 0.28)) / SR
    noise = _lowpass(rng.standard_normal(len(t)), 5200) * np.exp(-t * 16)
    body = np.sin(2 * np.pi * 185 * t) * np.exp(-t * 26)
    return 0.42 * noise + 0.22 * body


def _hat(rng) -> np.ndarray:
    t = np.arange(int(SR * 0.06)) / SR
    x = np.diff(rng.standard_normal(len(t) + 1))           # 고역만 남김
    return 0.10 * x * np.exp(-t * 70)


def _put(buf: np.ndarray, x: np.ndarray, at: float, pan: float = 0.0) -> None:
    i = int(at * SR)
    if i >= buf.shape[0]:
        return
    x = x[: buf.shape[0] - i]
    l, r = np.sqrt(0.5 * (1 - pan)), np.sqrt(0.5 * (1 + pan))
    buf[i:i + len(x), 0] += x * l
    buf[i:i + len(x), 1] += x * r


def lofi(seed: str, duration: float) -> np.ndarray:
    rng = _rng(seed)
    bpm = float(rng.choice([78, 82, 86, 90]))
    beat = 60 / bpm
    key = int(rng.choice([0, 2, 3, 5, 7, 9, 10]))          # C D Eb F G A Bb
    prog = PROGRESSIONS[int(rng.integers(len(PROGRESSIONS)))]
    swing = 0.58
    total = duration + 1.0
    buf = np.zeros((int(SR * (total + 2)), 2))
    bars = int(np.ceil(total / (4 * beat)))
    base = 48 + key
    for b in range(bars):
        deg, q = prog[b % len(prog)]
        root = base + deg
        while root > 55:
            root -= 12
        t0 = b * 4 * beat
        notes = [root + 12 + iv for iv in QUALITY[q]]
        # 화음: 1박에 길게, 3.5박에 짧게(살짝 엇박)
        for j, m in enumerate(notes):
            pan = -0.35 + 0.7 * j / max(1, len(notes) - 1)
            _put(buf, _ep(_hz(m), 2.6 * beat, 0.11), t0 + j * 0.012, pan)
            _put(buf, _ep(_hz(m), 0.9 * beat, 0.07), t0 + 2.5 * beat + j * 0.01, pan)
        # 베이스
        _put(buf, _bass(_hz(root - 12), 1.6 * beat, 0.42), t0)
        _put(buf, _bass(_hz(root - 12), 0.8 * beat, 0.30), t0 + 2.5 * beat)
        _put(buf, _bass(_hz(root - 5), 0.4 * beat, 0.22), t0 + 3.5 * beat)
        # 멜로디: 펜타토닉에서 드문드문(2마디마다 모양을 바꿔 반복)
        if b >= 1:
            for k in range(4):
                if rng.random() < 0.55:
                    m = base + 24 + PENTA[int(rng.integers(len(PENTA)))]
                    _put(buf, _bell(_hz(m), 0.06), t0 + (k + (0.5 if rng.random() < 0.3 else 0)) * beat, 0.25)
        # 드럼: 첫 마디는 화음만(시작 부드럽게)
        if b >= 1 or bars <= 2:
            for k in range(4):
                if k in (0,) or (k == 2 and rng.random() < 0.6):
                    _put(buf, _kick(rng), t0 + k * beat + (0.5 * beat if k == 2 else 0))
                if k in (1, 3):
                    _put(buf, _snare(rng), t0 + k * beat, 0.05)
                for e in (0, 1):
                    at = t0 + k * beat + (e * swing * beat)
                    _put(buf, _hat(rng) * (1.0 if e == 0 else 0.6), at, 0.3)
    # 바이닐 잡음: 아주 작은 히스 + 가끔 툭툭
    n = buf.shape[0]
    hiss = _lowpass(rng.standard_normal(n), 3500) * 0.004
    buf[:, 0] += hiss
    buf[:, 1] += np.roll(hiss, 17)
    for at in rng.uniform(0, total, int(total * 5)):
        _put(buf, np.exp(-np.arange(90) / 12) * rng.uniform(0.01, 0.04) * rng.choice([-1, 1]), at, rng.uniform(-0.6, 0.6))
    buf = buf[: int(SR * duration)]
    # 따뜻하게(고역 살짝 깎음) + 부드러운 포화 + 정규화
    for c in (0, 1):
        buf[:, c] = 0.6 * buf[:, c] + 0.4 * _lowpass(buf[:, c], 2600)
    buf = np.tanh(buf * 1.4) / np.tanh(1.4)
    peak = np.max(np.abs(buf)) or 1
    return buf * (0.89 / peak)


def _write_wav(x: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def make_music(seed: str, duration: float, out: Path) -> Path:
    """배경음악 wav 를 만든다. music/ 폴더에 곡이 있으면 그중 하나(영상마다 돌아가며)를 잘라 쓴다."""
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    tracks = sorted(p for p in MUSIC_DIR.glob("*") if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".aac", ".ogg")) if MUSIC_DIR.exists() else []
    raw = out.with_name(out.stem + "_raw.wav")
    if tracks:
        src = tracks[int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(tracks)]
        subprocess.run([ff, "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(src), "-t", f"{duration:.2f}",
                        "-ar", str(SR), "-ac", "2", str(raw)], check=True, timeout=300)
    else:
        _write_wav(lofi(seed, duration), raw)
    # 시작·끝 페이드, 음량을 SNS 기준(-16 LUFS)으로
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(raw), "-af",
                    f"afade=t=in:d=0.4,afade=t=out:st={max(0.0, duration - 1.6):.2f}:d=1.6,loudnorm=I=-16:TP=-1.5:LRA=11",
                    "-ar", "48000", str(out)], check=True, timeout=300)
    raw.unlink(missing_ok=True)
    return out
