"""Locks squad messages with the team code, so the public relays (and anyone on the same home network) only
ever carry unreadable bytes. Standard algorithms in plain Python (no extra packages), checked against the
RFC test vectors in tests/test_teamcrypt.py:

  * PBKDF2-HMAC-SHA256 turns the team code into a relay channel name and a 256-bit key. The channel name
    and the key come from different halves of the output, so knowing the channel doesn't give the key.
  * ChaCha20-Poly1305 (RFC 8439) locks each message: nobody without the code can read it, and a message
    changed or made up without the code is thrown away.
  * X25519 (RFC 7748) seals debug reports to the publisher's public key (see launcher.send_report): only
    the publisher can read them.

A longer code is a stronger lock: the setup window suggests a random one.
"""
from __future__ import annotations

import functools
import hashlib
import hmac
import json
import os
import re
import struct

SALT = b"chesttracker/team/v3"
ROUNDS = 100_000
MAGIC = b"CT3"          # locked team message: MAGIC + nonce (12) + ciphertext + tag (16)
REPORT_MAGIC = b"CTR1"  # sealed report: REPORT_MAGIC + ephemeral public key (32) + locked message
_M = 0xFFFFFFFF


def normalize_code(code) -> str:
    """Capitals, spaces and symbols don't matter: "Siblings 42" is "siblings42"."""
    return re.sub(r"[^a-z0-9]", "", str(code or "").lower())


CODE_LETTERS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # (no 0/O, 1/I/L: easy to read out to a friend)


def make_code(groups: int = 4) -> str:
    """A random team code that's hard to guess, like "K7QM-4XPA-9RWD-T3HC" (about 80 bits)."""
    import secrets
    return "-".join("".join(secrets.choice(CODE_LETTERS) for _ in range(4)) for _ in range(groups))


def code_strength(code) -> str:
    """"weak", "ok" or "strong", by how many letters and digits it has (a rough guide for the setup window)."""
    n = len(normalize_code(code))
    return "strong" if n >= 14 else "ok" if n >= 10 else "weak"


@functools.lru_cache(maxsize=8)
def team_secrets(code) -> tuple[str, bytes]:
    """(relay channel name, 32-byte key) for a team code; ("", b"") for no code."""
    c = normalize_code(code)
    if not c:
        return "", b""
    m = hashlib.pbkdf2_hmac("sha256", c.encode(), SALT, ROUNDS, 64)
    return m[:12].hex(), m[32:]


# --------------------------------------------------------------------------------------
# ChaCha20-Poly1305 (RFC 8439)
# --------------------------------------------------------------------------------------
def _qr(x, a, b, c, d):
    x[a] = (x[a] + x[b]) & _M; x[d] ^= x[a]; x[d] = ((x[d] << 16) | (x[d] >> 16)) & _M
    x[c] = (x[c] + x[d]) & _M; x[b] ^= x[c]; x[b] = ((x[b] << 12) | (x[b] >> 20)) & _M
    x[a] = (x[a] + x[b]) & _M; x[d] ^= x[a]; x[d] = ((x[d] << 8) | (x[d] >> 24)) & _M
    x[c] = (x[c] + x[d]) & _M; x[b] ^= x[c]; x[b] = ((x[b] << 7) | (x[b] >> 25)) & _M


def chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    s = [0x61707865, 0x3320646E, 0x79622D32, 0x6B206574, *struct.unpack("<8I", key), counter & _M,
         *struct.unpack("<3I", nonce)]
    x = s[:]
    for _ in range(10):
        _qr(x, 0, 4, 8, 12); _qr(x, 1, 5, 9, 13); _qr(x, 2, 6, 10, 14); _qr(x, 3, 7, 11, 15)
        _qr(x, 0, 5, 10, 15); _qr(x, 1, 6, 11, 12); _qr(x, 2, 7, 8, 13); _qr(x, 3, 4, 9, 14)
    return struct.pack("<16I", *((x[i] + s[i]) & _M for i in range(16)))


def chacha20(key: bytes, counter: int, nonce: bytes, data: bytes) -> bytes:
    n = len(data)
    if not n:
        return b""
    stream = b"".join(chacha20_block(key, counter + i, nonce) for i in range((n + 63) // 64))[:n]
    return (int.from_bytes(data, "little") ^ int.from_bytes(stream, "little")).to_bytes(n, "little")


def poly1305(key: bytes, msg: bytes) -> bytes:
    r = int.from_bytes(key[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key[16:32], "little")
    p, acc = (1 << 130) - 5, 0
    for i in range(0, len(msg), 16):
        acc = (acc + int.from_bytes(msg[i:i + 16] + b"\x01", "little")) * r % p
    return ((acc + s) & ((1 << 128) - 1)).to_bytes(16, "little")


def _pad16(b: bytes) -> bytes:
    return b"\x00" * (-len(b) % 16)


def _tag(key: bytes, nonce: bytes, aad: bytes, ct: bytes) -> bytes:
    otk = chacha20_block(key, 0, nonce)[:32]
    return poly1305(otk, aad + _pad16(aad) + ct + _pad16(ct) + struct.pack("<QQ", len(aad), len(ct)))


def aead_encrypt(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    """ciphertext + 16-byte tag"""
    ct = chacha20(key, 1, nonce, plaintext)
    return ct + _tag(key, nonce, aad, ct)


def aead_decrypt(key: bytes, nonce: bytes, data: bytes, aad: bytes = b"") -> bytes | None:
    if len(data) < 16:
        return None
    ct, tag = data[:-16], data[-16:]
    if not hmac.compare_digest(_tag(key, nonce, aad, ct), tag):
        return None
    return chacha20(key, 1, nonce, ct)


# --------------------------------------------------------------------------------------
# Team messages
# --------------------------------------------------------------------------------------
def lock(key: bytes, message) -> bytes:
    """A team message (dict, or JSON bytes/str) locked with the team key."""
    if isinstance(message, (dict, list)):
        message = json.dumps(message, separators=(",", ":"))
    if isinstance(message, str):
        message = message.encode()
    nonce = os.urandom(12)
    return MAGIC + nonce + aead_encrypt(key, nonce, message, MAGIC)


def unlock(key: bytes, data: bytes):
    """The message inside, parsed from JSON; None if it isn't one of ours or was changed."""
    if not key or not isinstance(data, (bytes, bytearray)) or not data.startswith(MAGIC) or len(data) < 31:
        return None
    plain = aead_decrypt(key, bytes(data[3:15]), bytes(data[15:]), MAGIC)
    if plain is None:
        return None
    try:
        return json.loads(plain.decode())
    except (ValueError, UnicodeDecodeError):
        return None


# --------------------------------------------------------------------------------------
# X25519 (RFC 7748): reports sealed to the publisher
# --------------------------------------------------------------------------------------
_P = 2 ** 255 - 19


def x25519(k: bytes, u: bytes) -> bytes:
    kb = bytearray(k)
    kb[0] &= 248; kb[31] &= 127; kb[31] |= 64
    k_ = int.from_bytes(kb, "little")
    x1 = int.from_bytes(u, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in range(254, -1, -1):
        kt = (k_ >> t) & 1
        if swap ^ kt:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = kt
        a, b = (x2 + z2) % _P, (x2 - z2) % _P
        aa, bb = a * a % _P, b * b % _P
        e = (aa - bb) % _P
        c, d = (x3 + z3) % _P, (x3 - z3) % _P
        da, cb = d * a % _P, c * b % _P
        x3, z3 = (da + cb) ** 2 % _P, x1 * (da - cb) ** 2 % _P
        x2, z2 = aa * bb % _P, e * (aa + 121665 * e) % _P
    if swap:
        x2, z2 = x3, z3
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, "little")


def x25519_public(secret: bytes) -> bytes:
    return x25519(secret, (9).to_bytes(32, "little"))


def _report_key(shared: bytes, eph_pub: bytes, pub: bytes) -> bytes:
    return hashlib.sha256(b"chesttracker/report/v1" + shared + eph_pub + pub).digest()


def seal_to(pub: bytes, data: bytes) -> bytes:
    """data that only the holder of pub's secret can open."""
    eph = os.urandom(32)
    eph_pub = x25519_public(eph)
    key = _report_key(x25519(eph, pub), eph_pub, pub)
    nonce = os.urandom(12)
    return REPORT_MAGIC + eph_pub + nonce + aead_encrypt(key, nonce, data, REPORT_MAGIC)


def open_sealed(secret: bytes, blob: bytes) -> bytes | None:
    if not isinstance(blob, (bytes, bytearray)) or not blob.startswith(REPORT_MAGIC) or len(blob) < 4 + 32 + 12 + 16:
        return None
    eph_pub, nonce, rest = bytes(blob[4:36]), bytes(blob[36:48]), bytes(blob[48:])
    pub = x25519_public(secret)
    return aead_decrypt(_report_key(x25519(secret, eph_pub), eph_pub, pub), nonce, rest, REPORT_MAGIC)
