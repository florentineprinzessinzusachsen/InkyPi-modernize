"""
Regenvorschau (com.chsoftware.regenvorschau) custom wire-protocol implementation.

Reverse-engineered from jadx-decompiled sources during an AUTHORIZED security
assessment commissioned by the app's author. Every constant and field layout
below is cited against the exact decompiled file/line it came from, and each
is tagged with a confidence level:

    [VERIFIED]  - read directly from clean, unmangled decompiled code and/or
                  independently confirmed byte-for-byte.
    [INFERRED]  - field shape (type, position) is clear from the writer code,
                  but the *semantic meaning* (what the value represents) is a
                  best-effort guess and should be validated against live
                  traffic before being relied upon.
    [UNCERTAIN] - decompiler output for this region had control-flow
                  reconstruction artifacts (see comments); treat with extra
                  caution.

Only use this against hosts the app itself talks to, with the app owner's
authorization. This module does not implement any flooding/looping
capability by design.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Layer 0: the app-wide string/byte "obfuscation" (NOT real cryptography)
# ---------------------------------------------------------------------------

# AbstractC1642o0.f6289a - the single repeating-key additive cipher key used
# for ~660 call sites across the entire app to hide string literals.
# [VERIFIED] - independently decoded API host list, URL paths, HTTP method
# names, etc. with this exact key.
STRING_KEY = [-2, 4, 3, 1, -2, -5, 2, -3, 4, -4, 3, -2, 1, -1, 2]


def deobfuscate_string(s: str, key=STRING_KEY, decrypt: bool = True) -> str:
    """Port of AbstractC1653z.m3453q(String, int[], boolean).

    Additive substitution cipher, key repeats every len(key) chars.
    decrypt=True mirrors the app's z4=true call convention (subtracts key).
    """
    sign = -1 if decrypt else 1
    out = []
    for i, ch in enumerate(s):
        out.append(chr((key[i % len(key)] * sign + ord(ch)) % 0x110000))
    return "".join(out)


# AbstractC1653z.m3438b - fixed 100-char repeating-key XOR applied to whole
# request/response byte ranges (in addition to a byte-reversal, see below).
# [VERIFIED] - read verbatim from AbstractC1653z.java:118.
XOR_KEY = ("K$jh#!xi8&Uk?!;QPjks7jhi#PBdhsk2jFbnHbyniws9huz%()gFueih wgoQzW-*"
           "#guZghC=sduPdsjsMj=Q-;[]);PAy<:>T!?")
assert len(XOR_KEY) == 100


def xor_transform(buf: bytearray, offset: int, length: int) -> None:
    """Port of AbstractC1653z.m3438b - in-place XOR over buf[offset:offset+length]."""
    for n, i in enumerate(range(offset, offset + length)):
        buf[i] ^= ord(XOR_KEY[n % 100])


def reverse_range(buf: bytearray, offset: int, length: int) -> None:
    """In-place reversal of buf[offset:offset+length] (both request builders
    and the response parser all do a plain two-pointer swap; verified
    identical logic at 4 separate call sites)."""
    i, j = offset, offset + length - 1
    while i < j:
        buf[i], buf[j] = buf[j], buf[i]
        i += 1
        j -= 1


def encode_range(buf: bytearray, offset: int, length: int) -> None:
    """Outbound transform used by BOTH /rain/bin and /rain/report request
    builders: reverse(range) then xor(range)."""
    reverse_range(buf, offset, length)
    xor_transform(buf, offset, length)


def decode_range(buf: bytearray, offset: int, length: int) -> None:
    """Inbound transform (self-inverse of encode_range): xor(range) then
    reverse(range)."""
    xor_transform(buf, offset, length)
    reverse_range(buf, offset, length)


# ---------------------------------------------------------------------------
# Layer 1: the hand-rolled TLV wire format (C1753b writer / C1752a reader)
# ---------------------------------------------------------------------------
# Every field is [tag: 1 byte][length: 1 or 4 bytes][value: length bytes].
# Length encoding (C1753b.m3710f / C1752a.m3700h) [VERIFIED]:
#   if length < 128:  1 byte  = length
#   else:              4 bytes = 0x80|((length>>24)&0xFF), (length>>16)&0xFF,
#                                 (length>>8)&0xFF, length&0xFF
# "int" fields (C1753b.m3708d) are ALWAYS a single byte (0-255 / -128..127).
# "long" fields (C1753b.m3714j) are ALWAYS exactly 4 bytes, big-endian - this
# is really a uint32, not a true 64-bit long, despite the Java method name.
# Strings: m3706b = US-ASCII, m3715k = UTF-8.


class TLVWriter:
    """Port of p122s2/C1753b.java."""

    def __init__(self):
        self.buf = bytearray()

    def raw_byte(self, v: int) -> "TLVWriter":
        self.buf.append(v & 0xFF)
        return self

    def _write_length(self, n: int) -> None:
        if n < 128:
            self.raw_byte(n)
        else:
            self.raw_byte(0x80 | ((n >> 24) & 0xFF))
            self.raw_byte((n >> 16) & 0xFF)
            self.raw_byte((n >> 8) & 0xFF)
            self.raw_byte(n & 0xFF)

    def byte_field(self, tag: int, value: int) -> "TLVWriter":
        """m3708d - tag, len=1, single byte value."""
        self.raw_byte(tag)
        self.raw_byte(1)
        self.raw_byte(value)
        return self

    def bool_field(self, tag: int, value: bool) -> "TLVWriter":
        """m3707c."""
        self.raw_byte(tag)
        self.raw_byte(1)
        self.raw_byte(1 if value else 0)
        return self

    def uint32_field(self, tag: int, value: int) -> "TLVWriter":
        """m3714j - tag, len=4, 4-byte big-endian value ("long" in the app,
        but truncated to 32 bits on the wire)."""
        self.raw_byte(tag)
        self.raw_byte(4)
        self.buf += struct.pack(">I", value & 0xFFFFFFFF)
        return self

    def string_ascii(self, tag: int, s: Optional[str]) -> "TLVWriter":
        """m3706b."""
        data = b"" if s is None else s.encode("ascii", errors="replace")
        self.raw_byte(tag)
        self._write_length(len(data))
        self.buf += data
        return self

    def string_utf8(self, tag: int, s: Optional[str]) -> "TLVWriter":
        """m3715k."""
        data = b"" if s is None else s.encode("utf-8")
        self.raw_byte(tag)
        self._write_length(len(data))
        self.buf += data
        return self

    def submessage(self, tag: int, sub: "TLVWriter") -> "TLVWriter":
        """m3709e - nested TLVWriter embedded with its own length prefix."""
        self.raw_byte(tag)
        self._write_length(len(sub.buf))
        self.buf += sub.buf
        return self

    def raw_float_pair(self, tag: int, a: float, b: float) -> "TLVWriter":
        """The coordinate field is hand-written (not via a generic helper):
        raw tag byte, raw length byte (always 8), then two big-endian
        IEEE-754 floats. [VERIFIED] AbstractC1646s.java:264-268."""
        self.raw_byte(tag)
        self.raw_byte(8)
        self.buf += struct.pack(">ff", a, b)
        return self

    def get_bytes(self) -> bytes:
        return bytes(self.buf)


class TLVReader:
    """Port of p122s2/C1752a.java. Non-destructive top-level walk mirrors
    C1758g.m3718c, which is the code path CONFIRMED correct (position
    captured *after* the length bytes are consumed, as separate sequential
    statements - no ambiguity).

    NOTE: the OTHER extraction helper in the original, m3698f() ("find field
    by tag, return a sub-reader"), has an argument-evaluation-order quirk in
    the decompiled Java that appears to construct its sub-reader's start
    offset *before* the length bytes are skipped. That path is only used by
    the /rain/report confirmation response (see decode_rain_report_response
    below) - it is reproduced faithfully there (bugs and all) rather than
    "corrected", since whatever the compiled app actually does is what the
    live server was written to interoperate with. If real captured traffic
    is available, validate that one function's output against it.
    """

    def __init__(self, buf: bytes, offset: int, length: int):
        self.buf = buf
        self.pos = offset
        self.end = min(offset + length, len(buf))

    def remaining(self) -> int:
        return max(0, self.end - self.pos)

    def peek_tag(self) -> int:
        if self.pos < self.end:
            return self.buf[self.pos]
        return -1

    def read_byte(self) -> int:
        if self.pos >= self.end:
            return -1
        v = self.buf[self.pos]
        self.pos += 1
        return v

    def read_length(self) -> int:
        if self.remaining() < 1:
            return 0
        b0 = self.read_byte()
        if b0 < 128:
            return b0
        if self.remaining() < 3:
            return 0
        b1 = self.read_byte()
        b2 = self.read_byte()
        b3 = self.read_byte()
        return ((b0 & 0x7F) << 24) | (b1 << 16) | (b2 << 8) | b3

    def read_remaining_bytes(self) -> bytes:
        n = self.remaining()
        out = self.buf[self.pos:self.pos + n]
        self.pos += n
        return bytes(out)

    def read_string(self, charset: str = "utf-8") -> str:
        return self.read_remaining_bytes().decode(charset, errors="replace")

    def read_uint16(self) -> int:
        if self.remaining() < 2:
            return 0
        return (self.read_byte() << 8) | self.read_byte()

    def read_uint32(self) -> int:
        if self.remaining() < 4:
            return 0
        b0, b1, b2, b3 = (self.read_byte() for _ in range(4))
        return (b0 << 24) | (b1 << 16) | (b2 << 8) | b3

    def read_float(self) -> float:
        return struct.unpack(">f", struct.pack(">I", self.read_uint32()))[0]

    def read_int32_array(self) -> list:
        n = self.remaining() // 4
        return [self.read_uint32() for _ in range(n)]

    def skip_field(self) -> None:
        """m3704l - skip tag+length+value of the field at the current position."""
        self.read_byte()
        n = self.read_length()
        self.pos += n

    def find_field(self, tag: int) -> Optional["TLVReader"]:
        """Faithful port of C1752a.m3698f, INCLUDING its evaluation-order
        quirk - see class docstring. Non-destructive (restores self.pos)."""
        saved = self.pos
        t = self.peek_tag()
        while t > -1:
            if t == tag:
                self.read_byte()  # consume tag
                # NOTE: in the original, "this.f6496b" (== self.pos here) is
                # evaluated as a constructor argument *before* m3700h()
                # (read_length) runs and advances the position - so the
                # sub-reader's start is the LENGTH byte's position, not the
                # value's. Reproduced verbatim:
                start = self.pos
                length = self.read_length()
                sub = TLVReader(self.buf, start, length)
                self.pos = saved
                return sub
            self.skip_field()
            t = self.peek_tag()
        self.pos = saved
        return None
