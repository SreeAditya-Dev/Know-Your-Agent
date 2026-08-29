"""Ed25519 signing and verification.

Ed25519 is what Web Bot Auth and Visa's Trusted Agent Protocol both specify, so
using it here is what makes agents built for those protocols work against this
gateway unmodified.

Verification never raises on a bad signature — it returns False. A gate needs
to distinguish "signature is wrong" (deny) from "we could not check" (degrade),
and an exception-based API blurs exactly that line.
"""

from __future__ import annotations

from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from kya.canonical import b64u_decode, b64u_encode, canonicalize


@dataclass(frozen=True, slots=True)
class KeyPair:
    """An Ed25519 keypair with a stable key id."""

    key_id: str
    private: Ed25519PrivateKey
    public: Ed25519PublicKey

    @property
    def public_b64u(self) -> str:
        return b64u_encode(raw_public_bytes(self.public))


def generate_keypair(key_id: str) -> KeyPair:
    priv = Ed25519PrivateKey.generate()
    return KeyPair(key_id=key_id, private=priv, public=priv.public_key())


def keypair_from_seed(key_id: str, seed: bytes) -> KeyPair:
    """Deterministic keypair from a 32-byte seed. Used for reproducible fixtures."""
    if len(seed) != 32:
        raise ValueError("Ed25519 seed must be exactly 32 bytes")
    priv = Ed25519PrivateKey.from_private_bytes(seed)
    return KeyPair(key_id=key_id, private=priv, public=priv.public_key())


def raw_public_bytes(pub: Ed25519PublicKey) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def public_from_b64u(text: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(b64u_decode(text))


def sign(private: Ed25519PrivateKey, message: bytes) -> str:
    """Sign raw bytes, returning a base64url signature."""
    return b64u_encode(private.sign(message))


def verify_bytes(public: Ed25519PublicKey, message: bytes, signature: bytes) -> bool:
    """Verify a raw signature. Returns False rather than raising.

    Gates must distinguish "signature is wrong" (deny) from "we could not
    check" (degrade), so a wrong signature is a return value, not an exception.
    """
    try:
        public.verify(signature, message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def verify(public: Ed25519PublicKey, message: bytes, signature_b64u: str) -> bool:
    """Verify a base64url signature.

    A malformed signature string is treated the same as a wrong one: both mean
    the caller failed to prove authorship.
    """
    try:
        raw = b64u_decode(signature_b64u)
    except (ValueError, TypeError):
        return False
    return verify_bytes(public, message, raw)


def sign_payload(private: Ed25519PrivateKey, payload: object) -> str:
    """Sign the canonical form of a structured payload."""
    return sign(private, canonicalize(payload))


def verify_payload(
    public: Ed25519PublicKey, payload: object, signature_b64u: str
) -> bool:
    return verify(public, canonicalize(payload), signature_b64u)
