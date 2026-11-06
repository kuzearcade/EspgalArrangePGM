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
    pred = deltas * scale[:, :, None]   # scaled residual, (frames, ch, spf)
    x = pred.transpose(1, 0, 2).reshape(ch, -1).astype(np.float64)   # (ch, samples)
    # y[n] = x[n] + c1*y[n-1] + c2*y[n-2]  ==  x * h, with h the impulse response of the
    # 2-pole predictor inverse; h decays geometrically so a truncated FIR is exact to <1 LSB.
    ntap = 2048
    h = np.zeros(ntap)
    h[0] = 1.0
    h[1] = c1
    for n in range(2, ntap):
        h[n] = c1 * h[n - 1] + c2 * h[n - 2]
    out = np.empty((ch, x.shape[1]), dtype=np.int16)
    for c in range(ch):
        y = _fft_convolve(x[c], h)
        out[c] = np.clip(np.rint(y), -32768, 32767).astype(np.int16)
    return out.T[: info.samples].copy()


def _fft_convolve(x: np.ndarray, h: np.ndarray, block: int = 1 << 18) -> np.ndarray:
    """Overlap-add FFT convolution of a long signal with a short kernel; returns len(x) samples."""
    n = len(x)
    m = len(h)
    size = block + m - 1
    nfft = 1 << (size - 1).bit_length()
    H = np.fft.rfft(h, nfft)
    y = np.zeros(n + m, dtype=np.float64)
    for start in range(0, n, block):
        seg = x[start:start + block]
        Y = np.fft.irfft(np.fft.rfft(seg, nfft) * H, nfft)
        y[start:start + len(seg) + m - 1] += Y[: len(seg) + m - 1]
    return y[:n]


def decode_file(path, max_seconds: float | None = None) -> tuple[AdxInfo, np.ndarray]:
    data = open(path, "rb").read()
    info = parse_header(data[:0x100])
    if max_seconds is not None:
        n = min(info.samples, int(max_seconds * info.sample_rate))
        info = AdxInfo(**{**info.__dict__, "samples": n})
    return info, decode(data, info)


# The PS2's own decoder (CRI ADXB, ELF 0x27CD3C; coefficients 7400 / -3342 for cutoff 500 at
# 48 kHz, EE RAM 0x2ECE90 in the PCSX2 dumps): per sample
#   s = clamp16(((c1 * h1 + c2 * h2) >> 12) + nibble * scale),  scale = (word & 0x1FFF) + 1
# (`word` XORed with the stream key, 0 for these streams). The float decode() above is not
# bit-exact; decode_exact() is, and is the reference for the core's rtl/adx_stream.sv.

def coefficients_int(cutoff: int, sr: int) -> tuple[int, int]:
    a = math.sqrt(2.0) - math.cos(2.0 * math.pi * cutoff / sr)
    b = math.sqrt(2.0) - 1.0
    c = (a - math.sqrt((a + b) * (a - b))) / b
    return int(c * 8192), int(c * c * -4096)


def decode_exact(data: bytes, n: int, loop_hist: str = "restore", loop_end_inclusive: bool = False) -> np.ndarray:
    """int16 PCM (n, channels) of an ADX stream played for n samples, looping as the header says.
    loop_hist: at the loop jump restore the history saved when the loop start was first decoded
    ("restore", the PS2's: PCSX2 decoder state at the boss track's seam) or keep it ("continue").
    loop_end_inclusive: also play the header's loop end sample (not yet checked on the PS2)."""
    info = parse_header(data[:0x100])
    if info.encoding != 3 or info.bits != 4 or info.block_size != 18:
        raise NotImplementedError("ADX encoding 3, 4-bit, 18-byte frames only")
    c1, c2 = coefficients_int(info.cutoff, info.sample_rate)
    ch = info.channels
    end = (info.loop_end + int(loop_end_inclusive)) if info.loop else info.samples
    out = np.zeros((n, ch), dtype=np.int16)
    hist = [[0, 0] for _ in range(ch)]
    saved = None
    pos = 0
    for k in range(n):
        if pos == end:
            if not info.loop:
                break
            pos = info.loop_start
            if loop_hist == "restore":
                hist = [h[:] for h in saved]
        if info.loop and pos == info.loop_start and saved is None:
            saved = [h[:] for h in hist]
        f, i = divmod(pos, 32)
        for c in range(ch):
            o = info.data_offset + (f * ch + c) * 18
            scale = (((data[o] << 8) | data[o + 1]) & 0x1FFF) + 1
            b = data[o + 2 + i // 2]
            nib = (b >> 4) if i % 2 == 0 else (b & 0xF)
            nib -= 16 if nib > 7 else 0
            h1, h2 = hist[c]
            s = ((c1 * h1 + c2 * h2) >> 12) + nib * scale
            s = max(-32768, min(32767, s))
            hist[c] = [s, h1]
            out[k, c] = s
        pos += 1
    return out
