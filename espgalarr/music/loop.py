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


def find_loop_point(pcm_mono: np.ndarray, rate: int, lo: int, hi: int,
                    min_period_s: float = 45.0, threshold: float = 0.93) -> tuple[int, int] | None:
    """Search the loop body pcm[lo:hi] for a repeated musical section: a lag L and a start
    t0 such that env[t0:t0+L] ~= env[t0+L:t0+2L] (normalised correlation >= threshold).
    Returns (loop_start, loop_end) in samples, with loop_end - loop_start == L, choosing the
    earliest t0 for the best-correlating L. Works on the 20 ms RMS envelope so it is robust
    to the lossy decode and small timing drift. None if nothing repeats."""
    hop_ms = 20.0
    hop = int(rate * hop_ms / 1000)
    env = envelope(pcm_mono[lo:hi], rate, hop_ms)
    env = env - env.mean()
    n = len(env)
    min_lag = int(min_period_s * 1000 / hop_ms)
    if n < 2 * min_lag or not np.any(env):
        return None
    # global autocorrelation to shortlist lags
    f = np.fft.rfft(env, 1 << (2 * n - 1).bit_length())
    ac = np.fft.irfft(f * np.conj(f))[:n]
    ac = ac / (ac[0] + 1e-9)
    cand = [lag for lag in range(min_lag, n // 2 + 1) if ac[lag] > 0.5]
    cand.sort(key=lambda lag: -ac[lag])
    best = None
    for lag in cand[:12]:
        # slide t0 and find the best local match of two consecutive windows of length lag
        for t0 in range(0, n - 2 * lag + 1, max(1, lag // 50)):
            a = env[t0:t0 + lag]
            b = env[t0 + lag:t0 + 2 * lag]
            c = float(np.dot(a, b)) / (np.sqrt(float(np.dot(a, a)) * float(np.dot(b, b))) + 1e-9)
            if c >= threshold and (best is None or c > best[2] + 1e-6 or (abs(c - best[2]) <= 1e-6 and lag < best[1])):
                best = (t0, lag, c)
                break   # earliest t0 for this lag is what we want
    if best is None:
        return None
    t0, lag, _ = best
    return lo + t0 * hop, lo + (t0 + lag) * hop


def plan_track(name: str, pcm: np.ndarray, rate: int, loop: tuple[int, int] | None) -> TrackPlan:
    mono = pcm.mean(axis=1) if pcm.ndim == 2 else pcm
    if loop is None or loop[1] <= loop[0]:
        return TrackPlan(name, rate, len(mono), len(mono), "no loop metadata: play once")
    lo, hi = loop
    found = find_loop_point(mono, rate, lo, hi)
    if found is not None and found[1] < hi:
        ls, le = found
        return TrackPlan(name, rate, ls, le, f"repeat found: loop {ls/rate:.1f}-{le/rate:.1f}s (was {lo/rate:.1f}-{hi/rate:.1f})")
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


# --- duration capping -----------------------------------------------------------

def best_cut(mono: np.ndarray, rate: int, loop_start: int, max_len: int, search_s: float = 20.0) -> int:
    """Pick a loop end <= max_len whose neighbourhood resembles the loop start (so the jump
    back is musically plausible), using the 20 ms RMS envelope over a window of 2 s."""
    hop_ms = 20.0
    hop = int(rate * hop_ms / 1000)
    env = envelope(mono, rate, hop_ms)
    env = env - env.mean()
    win = int(2000 / hop_ms)
    s0 = loop_start // hop
    ref = env[s0:s0 + win]
    if len(ref) < win or not np.any(ref):
        return max_len
    lo = max(s0 + win, (max_len - int(search_s * rate)) // hop)
    hi = min(len(env) - win, max_len // hop)
    best, best_c = max_len, -2.0
    for t in range(lo, hi):
        seg = env[t:t + win]
        c = float(np.dot(ref, seg)) / (np.sqrt(float(np.dot(ref, ref)) * float(np.dot(seg, seg))) + 1e-9)
        if c > best_c:
            best, best_c = t * hop, c
    return best & ~1


def cap_plan(plan: TrackPlan, mono: np.ndarray, max_seconds: float) -> TrackPlan:
    cap = int(max_seconds * plan.src_rate)
    if plan.loop_end <= cap:
        return plan
    if plan.intro_end >= cap:
        return TrackPlan(plan.name, plan.src_rate, 0, cap, plan.note + f"; capped to {max_seconds:.0f}s (no loop)")
    end = best_cut(mono, plan.src_rate, plan.intro_end, cap)
    return TrackPlan(plan.name, plan.src_rate, plan.intro_end, end, plan.note + f"; capped to {end/plan.src_rate:.0f}s at best cut")


def fit_budget(plans: dict[str, TrackPlan], monos: dict[str, np.ndarray], budget_bytes: int,
               priority: dict[str, int], min_len: dict[int, float], rates: list[int] = STANDARD_RATES,
               max_track_bytes: int | None = None) -> tuple[int, dict[str, TrackPlan]]:
    """Pick the highest rate at which every track can keep at least its tier's minimum
    length (and fit `max_track_bytes`, e.g. one ICS2115 bank), then cap tracks to fit the
    total budget, lowest-priority tier first, down to the tier minimum; else go lower."""
    for rate in sorted(rates, reverse=True):
        cap_s = (max_track_bytes / rate) if max_track_bytes else float("inf")
        # every tier's minimum must be representable at this rate within one bank
        if any(min_len[t] > cap_s for t in set(priority.values())):
            continue
        cur = {n: (cap_plan(pl, monos[n], cap_s) if pl.kept_samples / pl.src_rate > cap_s else pl)
               for n, pl in plans.items()}

        def total():
            return sum(pl.kept_samples / pl.src_rate for pl in cur.values()) * rate
        for tier in sorted(set(priority.values()), reverse=True):
            if total() <= budget_bytes:
                break
            for name, pl in list(cur.items()):
                if priority[name] == tier and pl.kept_samples / pl.src_rate > min_len[tier]:
                    cur[name] = cap_plan(pl, monos[name], min_len[tier])
        if total() <= budget_bytes:
            return rate, cur
    raise MemoryError("cannot fit even at minimum lengths and lowest rate")
