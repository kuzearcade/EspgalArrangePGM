"""Loop-point selection and sample-rate budgeting for streamed BGM.

Policy (approved): mono 8-bit u-law, keep intro + one loop, pick the highest common
sample rate such that everything fits the sample-ROM budget.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class TrackPlan:
    name: str
    src_rate: int
    intro_end: int        # sample index at src_rate where the loop body starts
    loop_end: int         # sample index (exclusive) where playback jumps back to intro_end
    note: str = ""

    @property
    def kept_samples(self) -> int:
        return self.loop_end


def envelope(pcm_mono: np.ndarray, rate: int, hop_ms: float = 20.0) -> np.ndarray:
    hop = max(1, int(rate * hop_ms / 1000))
    n = len(pcm_mono) // hop
    x = pcm_mono[: n * hop].astype(np.float64).reshape(n, hop)
    return np.sqrt((x * x).mean(axis=1))


def find_internal_period(pcm_mono: np.ndarray, rate: int, lo: int, hi: int,
                         min_period_s: float = 20.0, threshold: float = 0.92) -> int | None:
    """If the loop body pcm[lo:hi] is itself k>=2 repeats of a shorter period, return
    the period in samples; otherwise None. Uses normalised autocorrelation of the RMS
    envelope so it is robust to the lossy decode."""
    hop_ms = 20.0
    env = envelope(pcm_mono[lo:hi], rate, hop_ms)
    env = env - env.mean()
    if len(env) < 4 or not np.any(env):
        return None
    hop = int(rate * hop_ms / 1000)
    min_lag = int(min_period_s * 1000 / hop_ms)
    best = None
    denom = float(np.dot(env, env))
    for lag in range(min_lag, len(env) // 2 + 1):
        a = env[:-lag]
        b = env[lag:]
        c = float(np.dot(a, b)) / (np.sqrt(float(np.dot(a, a)) * float(np.dot(b, b))) + 1e-9)
        # require the lag to divide the body (within one hop) into >= 2 repeats
        reps = len(env) / lag
        if c >= threshold and abs(reps - round(reps)) * lag <= 2 and round(reps) >= 2:
            if best is None or c > best[1]:
                best = (lag, c)
    return best[0] * hop if best else None


def plan_track(name: str, pcm: np.ndarray, rate: int, loop: tuple[int, int] | None) -> TrackPlan:
    mono = pcm.mean(axis=1) if pcm.ndim == 2 else pcm
    if loop is None or loop[1] <= loop[0]:
        return TrackPlan(name, rate, len(mono), len(mono), "no loop metadata: play once")
    lo, hi = loop
    period = find_internal_period(mono, rate, lo, hi)
    if period is not None and period < (hi - lo):
        return TrackPlan(name, rate, lo, lo + period, f"loop body is {round((hi-lo)/period)} repeats; kept one")
    return TrackPlan(name, rate, lo, hi, "ADX loop metadata")


STANDARD_RATES = [33075, 22050, 16538, 16000, 12000, 11025, 8000]


def choose_rate(plans: list[TrackPlan], budget_bytes: int, candidates: list[int] = STANDARD_RATES) -> int:
    """Highest candidate rate (<= chip output rate) such that sum(kept seconds) * rate fits."""
    seconds = sum(p.kept_samples / p.src_rate for p in plans)
    for r in sorted(candidates, reverse=True):
        if seconds * r <= budget_bytes:
            return r
    raise MemoryError(f"{seconds:.0f} s of audio cannot fit {budget_bytes} bytes even at {min(candidates)} Hz")


def resample(mono: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Band-limited-ish resampling: windowed-sinc low-pass via FFT then linear interpolation."""
    x = mono.astype(np.float64)
    if dst_rate < src_rate:
        # low-pass at 0.45 * dst_rate using an FFT brick-wall with a short taper
        n = len(x)
        nfft = 1 << (n - 1).bit_length()
        X = np.fft.rfft(x, nfft)
        freqs = np.fft.rfftfreq(nfft, 1.0 / src_rate)
        cutoff = 0.45 * dst_rate
        taper = np.clip((cutoff * 1.1 - freqs) / (cutoff * 0.1), 0.0, 1.0)
        x = np.fft.irfft(X * taper, nfft)[:n]
    t = np.arange(int(len(x) * dst_rate / src_rate)) * (src_rate / dst_rate)
    i = np.floor(t).astype(np.int64)
    frac = t - i
    i = np.clip(i, 0, len(x) - 2)
    y = x[i] * (1 - frac) + x[i + 1] * frac
    return np.clip(np.rint(y), -32768, 32767).astype(np.int16)
