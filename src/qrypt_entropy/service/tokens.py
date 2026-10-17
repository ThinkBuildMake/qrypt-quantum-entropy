"""Tokens: redeemable tokens and redemption. Owns `tokens`, `redemption_outcomes`.

S01 STUB: no hashing, no storage; redeem always says `redeemed`. Real: S16 (store, hash), S17 (seed),
S21 (redeem), S22 (rate limit).
"""

import hashlib

from qrypt_entropy.stubs import stub


@stub("S16")
def hash_token(owner: str, value: str) -> bytes:
    return hashlib.sha256(f"{owner}\0{value}".encode()).digest()  # real: HMAC-SHA-256 under the pepper


@stub("S16")
def store_token(tx, issuance_id: str, owner: str, kind: str, value: str, expiry_seconds: int) -> None:
    return None


@stub("S17")
def store_seed_token(owner: str, kind: str, value: str, expiry_seconds: int) -> None:
    return None


@stub("S21")
def redeem(owner: str, value: str) -> str:
    return "redeemed"
