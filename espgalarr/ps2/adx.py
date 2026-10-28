"""CRI ADX (type 3 / fixed-coefficient) header parser and decoder.

The ADX format is publicly documented (see the vgmstream and Multimedia Wiki
descriptions). Only what the Arrange disc uses is implemented: encoding 3,
4-bit samples, 18-byte frames, version 3/4 headers with loop info.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class AdxInfo:
    data_offset: int
    encoding: int
    block_size: int
    bits: int
    channels: int
    sample_rate: int
    samples: int
    cutoff: int
    version: int
    loop: bool
    loop_start: int   # sample index
    loop_end: int     # sample index (exclusive)

    @property
    def duration(self) -> float:
        return self.samples / self.sample_rate

    @property
    def samples_per_frame(self) -> int:
        return (self.block_size - 2) * 8 // self.bits


def parse_header(h: bytes) -> AdxInfo:
    if h[:2] != b"\x80\x00":
        raise ValueError("not an ADX stream")
    copyright_off = struct.unpack_from(">H", h, 2)[0]
    enc, block, bits, ch = h[4], h[5], h[6], h[7]
    sr, ns = struct.unpack_from(">II", h, 8)
    cutoff = struct.unpack_from(">H", h, 0x10)[0]
    version = h[0x12]
    loop = False
    ls = le = 0
    if version == 3 and copyright_off >= 0x2c:
        flag, ls, _lsb, le, _leb = struct.unpack_from(">IIIII", h, 0x18)
        loop = flag != 0
    elif version == 4 and copyright_off >= 0x38:
        flag, ls, _lsb, le, _leb = struct.unpack_from(">IIIII", h, 0x24)
        loop = flag != 0
    return AdxInfo(copyright_off + 4, enc, block, bits, ch, sr, ns, cutoff, version, loop, ls, le)


def _coefficients(cutoff: int, sr: int) -> tuple[float, float]:
    a = math.sqrt(2.0) - math.cos(2.0 * math.pi * cutoff / sr)
    b = math.sqrt(2.0) - 1.0
    c = (a - math.sqrt((a + b) * (a - b))) / b
    return 2.0 * c, -(c * c)


def decode(data: bytes, info: AdxInfo | None = None) -> np.ndarray:
    """Decode to int16 PCM, shape (samples, channels). Pure numpy, frame-vectorised
    across channels with a per-frame Python loop (fast enough: ~1 s per minute)."""
    if info is None:
        info = parse_header(data[:0x100])
    if info.encoding != 3 or info.bits != 4:
        raise NotImplementedError(f"ADX encoding {info.encoding}/{info.bits}-bit not supported")
    c1, c2 = _coefficients(info.cutoff, info.sample_rate)
    ch = info.channels
    spf = info.samples_per_frame
    nframes = -(-info.samples // spf)
    body = np.frombuffer(data, dtype=np.uint8, count=nframes * ch * info.block_size,
                         offset=info.data_offset)
    frames = body.reshape(nframes, ch, info.block_size)
    scale = (frames[:, :, 0].astype(np.int32) << 8) | frames[:, :, 1].astype(np.int32)
    nib = frames[:, :, 2:]
    # high nibble first, sign-extended 4-bit
    hi = (nib >> 4).astype(np.int8)
    lo = (nib & 0xF).astype(np.int8)
    hi = np.where(hi > 7, hi - 16, hi).astype(np.int32)
    lo = np.where(lo > 7, lo - 16, lo).astype(np.int32)
    deltas = np.empty((nframes, ch, spf), dtype=np.int32)
    deltas[:, :, 0::2] = hi
    deltas[:, :, 1::2] = lo
    pred = deltas * scale[:, :, None]   # scaled residual
    out = np.empty((nframes * spf, ch), dtype=np.int16)
    h1 = np.zeros(ch, dtype=np.float64)
    h2 = np.zeros(ch, dtype=np.float64)
    for f in range(nframes):
        blk = pred[f].T.astype(np.float64)   # (spf, ch)
        res = np.empty_like(blk)
        for s in range(spf):
            v = blk[s] + c1 * h1 + c2 * h2
            v = np.clip(v, -32768, 32767)
            res[s] = v
            h2 = h1
            h1 = v
        out[f * spf:(f + 1) * spf] = res.astype(np.int16)
    return out[: info.samples]


def decode_file(path, max_seconds: float | None = None) -> tuple[AdxInfo, np.ndarray]:
    data = open(path, "rb").read()
    info = parse_header(data[:0x100])
    if max_seconds is not None:
        n = min(info.samples, int(max_seconds * info.sample_rate))
        info = AdxInfo(**{**info.__dict__, "samples": n})
    return info, decode(data, info)
