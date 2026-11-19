"""Curve25519 keys as WireGuard writes them: 32 bytes, base64."""
from __future__ import annotations

import base64
import os

KEY_BYTES = 32

_PRIME = 2 ** 255 - 19
_A24 = 121665
_BASE_POINT = 9
_SCALAR_BITS = 255


def _clamped(key: bytes) -> bytes:
    """A scalar the curve accepts (RFC 7748, section 5)."""
    clamped = bytearray(key)
    clamped[0] &= 248
    clamped[31] &= 127
    clamped[31] |= 64
    return bytes(clamped)


def public_of(private_b64: str) -> str:
    """The public key of a private one (RFC 7748, the Montgomery ladder)."""
    raw = base64.b64decode(private_b64)
    if len(raw) != KEY_BYTES:
        raise ValueError("key length")
    scalar = int.from_bytes(_clamped(raw), "little")
    x1 = _BASE_POINT
    x2 = 1
    z2 = 0
    x3 = _BASE_POINT
    z3 = 1
    swap = 0
    for bit_index in range(_SCALAR_BITS - 1, -1, -1):
        bit = (scalar >> bit_index) & 1
        swap ^= bit
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = bit
        a = (x2 + z2) % _PRIME
        b = (x2 - z2) % _PRIME
        aa = a * a % _PRIME
        bb = b * b % _PRIME
        e = (aa - bb) % _PRIME
        c = (x3 + z3) % _PRIME
        d = (x3 - z3) % _PRIME
        da = d * a % _PRIME
        cb = c * b % _PRIME
        x3 = (da + cb) ** 2 % _PRIME
        z3 = x1 * (da - cb) ** 2 % _PRIME
        x2 = aa * bb % _PRIME
        z2 = e * (aa + _A24 * e) % _PRIME
    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2
    public = x2 * pow(z2, _PRIME - 2, _PRIME) % _PRIME
    return base64.b64encode(public.to_bytes(KEY_BYTES, "little")).decode()


def new_private() -> str:
    return base64.b64encode(_clamped(os.urandom(KEY_BYTES))).decode()


def new_preshared() -> str:
    return base64.b64encode(os.urandom(KEY_BYTES)).decode()
