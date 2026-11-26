"""Measure the clock from the audio itself, before any decode can succeed.

TDRIFT needs a decode, and a decode needs the clock within about 2 s. So a
clock that is 30 s out is unrecoverable by the normal path: audio flows, the
decoder hears it, nothing decodes, and no offset is ever measured.

JS8 frames start on 15 s boundaries and last 12.6 s, so 84% of each period is
signal and 16% is gap. Broadband RMS cannot see that at -20 dB SNR (measured
on a live 20 m feed: every 50 ms window sits between 1401 and 1701), but a
single 31 Hz tone bin can: the same signal that is -20 dB across 2.5 kHz is
about -1 dB inside one bin. So the detector tracks the strongest narrow bin per
window and correlates that series with a 12.6 s pulse over all 15 s phases.
Averaging ~100 windows buys the rest of the margin.

Coarse on purpose: 100 ms steps, tens of milliseconds of jitter, which is all
that is needed to get inside the decode window. Once decodes start, the TDRIFT
median refines the offset.
"""
from __future__ import annotations

import array
import cmath
import math
import statistics
import time

from . import pulse

SLOT = 15.0          # JS8 period, seconds
FRAME = 12.6         # frame length inside the period
RATE = 8000
NFFT = 1024          # 128 ms
HOP = 1024           # non-overlapping windows
LO_HZ, HI_HZ = 300, 2700


def _samples(data: bytes) -> array.array:
    a = array.array("h")
    a.frombytes(data[:len(data) // 2 * 2])
    return a


_WIN = [0.5 - 0.5 * math.cos(2 * math.pi * i / (NFFT - 1)) for i in range(NFFT)]


def _fft(x):
    """Iterative radix-2 FFT. Small and dependency-free."""
    n = len(x)
    j = 0
    x = list(x)
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            x[i], x[j] = x[j], x[i]
    length = 2
    while length <= n:
        ang = -2 * math.pi / length
        wl = cmath.exp(1j * ang)
        for i in range(0, n, length):
            w = 1 + 0j
            half = length // 2
            for k in range(i, i + half):
                u = x[k]
                v = x[k + half] * w
                x[k] = u + v
                x[k + half] = u - v
                w *= wl
        length <<= 1
    return x


def tone_series(data: bytes, rate: int = RATE) -> list[tuple[float, float]]:
    """[(seconds_from_start, peak narrowbin power)] over the capture."""
    a = _samples(data)
    lo = int(LO_HZ * NFFT / rate)
    hi = int(HI_HZ * NFFT / rate)
    out = []
    for start in range(0, len(a) - NFFT + 1, HOP):
        frame = a[start:start + NFFT]
        spec = _fft([complex(frame[i] * _WIN[i]) for i in range(NFFT)])
        peak = max(abs(spec[b]) ** 2 for b in range(lo, hi + 1))
        out.append((start / rate, peak))
    return out


def phase_means(series, phase: float, slot: float = SLOT,
                frame: float = FRAME) -> tuple[float, float]:
    """(mean in-frame power, mean out-of-frame power) for one phase."""
    inside = outside = 0.0
    n_in = n_out = 0
    for t, power in series:
        pos = (t - phase) % slot
        if pos < frame:
            inside += power
            n_in += 1
        else:
            outside += power
            n_out += 1
    if not n_in or not n_out:
        return 0.0, 0.0
    return inside / n_in, outside / n_out


def score_phase(series, phase: float, slot: float = SLOT,
                frame: float = FRAME) -> float:
    inside, outside = phase_means(series, phase, slot, frame)
    if not inside and not outside:
        return float("-inf")
    return inside - outside


def wrap_phase(seconds: float, slot: float = SLOT) -> float:
    """Fold a time into (-slot/2, +slot/2]."""
    return (seconds % slot + slot / 2) % slot - slot / 2


def measure(source: str | None = None, seconds: float = 20.0,
            rate: int = RATE, slot: float = SLOT) -> dict:
    """Listen, find the frame grid, and report how far the clock sits from it.

    `phase` is what the audio shows: frames beginning at our :03 mean our clock
    is 3 s fast, so `correction` is negative. Times are anchored to the *end*
    of the capture, because parec starts when it starts.
    """
    data = pulse.capture_samples(seconds, rate, source=source)
    t_end = time.time()
    series = tone_series(data, rate)
    if len(series) < int(slot / (HOP / rate)) + 2:
        return {"phase": None, "correction": None, "score": None,
                "margin": None, "windows": len(series)}
    best_phase, best_score = 0.0, float("-inf")
    step = 0.1
    p = 0.0
    while p < slot:
        s = score_phase(series, p, slot)
        if s > best_score:
            best_phase, best_score = p, s
        p = round(p + step, 3)
    # fold: a frame could equally be described as starting one slot later
    phase = wrap_phase(best_phase, slot)
    runner = max(
        (score_phase(series, round(p, 3), slot) for p in
         [(phase + o) % slot for o in (2.0, 4.0, 6.0, -2.0, -4.0, -6.0)]),
        default=float("-inf"))
    # series times are relative to the capture start; convert to wall clock
    duration = len(data) / (2.0 * rate)
    wall_phase = wrap_phase(phase + (t_end - duration), slot)
    inside, outside = phase_means(series, best_phase, slot)
    ratio = inside / outside if outside else float("inf")
    return {"phase": round(wall_phase, 3), "correction": round(-wall_phase, 3),
            "score": round(best_score, 1),
            "margin": round(best_score - runner, 1) if runner != float("-inf") else None,
            "ratio": round(ratio, 2), "windows": len(series)}
