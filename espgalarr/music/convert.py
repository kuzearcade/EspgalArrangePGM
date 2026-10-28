"""ADX stream -> ICS2115 u-law stream, following a TrackPlan."""
from __future__ import annotations

import wave
from dataclasses import dataclass

import numpy as np

from ..pgm import ics2115
from . import loop as loop_mod


@dataclass
class StreamTrack:
    name: str
    rate: int
    data: bytes            # u-law bytes, intro + one loop body
    loop_start: int        # byte offset where the loop restarts
    loop_end: int          # byte offset (exclusive) == len(data)
    src_seconds: float


def to_mono(pcm: np.ndarray) -> np.ndarray:
    if pcm.ndim == 1:
        return pcm.astype(np.int16)
    return np.clip(np.rint(pcm.astype(np.float64).mean(axis=1)), -32768, 32767).astype(np.int16)


def normalise(mono: np.ndarray, peak: float = 0.95) -> np.ndarray:
    m = float(np.abs(mono).max()) or 1.0
    g = (32767.0 * peak) / m
    if g <= 1.0:
        return mono
    return np.clip(np.rint(mono.astype(np.float64) * g), -32768, 32767).astype(np.int16)


def convert_track(name: str, pcm: np.ndarray, src_rate: int, plan: loop_mod.TrackPlan, dst_rate: int) -> StreamTrack:
    mono = normalise(to_mono(pcm[: plan.loop_end]))
    res = loop_mod.resample(mono, src_rate, dst_rate)
    ls = int(plan.intro_end * dst_rate / src_rate) & ~1
    codes = ics2115.ulaw_encode(res)
    return StreamTrack(name, dst_rate, codes.tobytes(), ls, len(codes), plan.loop_end / src_rate)


def write_wav_preview(track: StreamTrack, path, loops: int = 2) -> None:
    """Decode the u-law stream back to a WAV (for listening/QA only)."""
    codes = np.frombuffer(track.data, dtype=np.uint8)
    pcm = ics2115.ulaw_decode(codes)
    body = pcm[track.loop_start:]
    full = np.concatenate([pcm] + [body] * (loops - 1))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(track.rate)
        w.writeframes(full.astype(np.int16).tobytes())
