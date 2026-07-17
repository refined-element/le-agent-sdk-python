"""Nostr event builder and signing (NIP-01).

Handles event creation, ID computation (SHA-256 of canonical serialization),
and Schnorr signing (BIP-340) via the coincurve library.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Optional

# coincurve provides BIP-340 Schnorr over secp256k1 and ships prebuilt wheels
# for every platform we support, so a normal `pip install` is sufficient.
#
# The import is still guarded. A wheel can be missing for an unusual
# platform/interpreter combination, and an install can be pruned after the fact,
# so the failure mode has to be defined rather than incidental. Every operation
# that depends on it — sign(), pubkey_from_private_key() and verify() — raises
# when it is missing. None of them degrade to a weaker check. Only
# building/serializing unsigned events works without it.
try:
    import coincurve

    _HAS_COINCURVE = True
except ImportError:
    _HAS_COINCURVE = False


class CryptoBackendUnavailableError(RuntimeError):
    """Raised when an operation needs the BIP-340 backend but it is unimportable.

    Subclasses RuntimeError so existing `except RuntimeError` handlers keep
    working. Distinguishable so that callers can tell this environment fault
    apart from an operational error (e.g. a relay disconnect) and avoid
    retrying something that will never succeed.
    """


# The backend moved from `secp256k1` to `coincurve` in 0.4.0, which dates the
# old name. It is kept as an alias so `except Secp256k1UnavailableError` keeps
# working: the curve is still secp256k1, only the binding changed. Prefer
# CryptoBackendUnavailableError in new code.
Secp256k1UnavailableError = CryptoBackendUnavailableError

_MISSING_BACKEND_HINT = (
    "coincurve is required for BIP-340 Schnorr operations. "
    "Install with: pip install coincurve"
)


class NostrEvent:
    """Builds and signs Nostr events per NIP-01."""

    @staticmethod
    def serialize_for_id(event: dict[str, Any]) -> str:
        """Serialize an event for ID computation per NIP-01.

        The canonical form is:
            [0, <pubkey>, <created_at>, <kind>, <tags>, <content>]

        Returns:
            JSON string with no whitespace.
        """
        commitment = [
            0,
            event["pubkey"],
            event["created_at"],
            event["kind"],
            event["tags"],
            event["content"],
        ]
        return json.dumps(commitment, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def compute_id(event: dict[str, Any]) -> str:
        """Compute the event ID (SHA-256 hex digest of canonical serialization)."""
        serialized = NostrEvent.serialize_for_id(event)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def pubkey_from_private_key(private_key_hex: str) -> str:
        """Derive the x-only public key from a hex private key.

        Returns:
            32-byte x-only public key as hex string.
        """
        if not _HAS_COINCURVE:
            raise CryptoBackendUnavailableError(
                f"Key derivation is unavailable: {_MISSING_BACKEND_HINT}"
            )
        privkey_bytes = bytes.fromhex(private_key_hex)
        if len(privkey_bytes) != 32:
            raise ValueError(
                f"Private key must be 32 bytes, got {len(privkey_bytes)} bytes"
            )
        # BIP-340 keys are x-only: the x coordinate with the y parity dropped.
        return coincurve.PublicKeyXOnly.from_secret(privkey_bytes).format().hex()

    @staticmethod
    def sign(event_id_hex: str, private_key_hex: str) -> str:
        """Create a Schnorr signature (BIP-340) over the event ID.

        Returns:
            64-byte signature as hex string.
        """
        if not _HAS_COINCURVE:
            raise CryptoBackendUnavailableError(
                f"Signing is unavailable: {_MISSING_BACKEND_HINT}"
            )
        privkey_bytes = bytes.fromhex(private_key_hex)
        if len(privkey_bytes) != 32:
            raise ValueError(
                f"Private key must be 32 bytes, got {len(privkey_bytes)} bytes"
            )
        # NIP-01 signs the 32-byte event id directly; it is already a digest and
        # must not be hashed again. sign_schnorr takes the message unhashed.
        msg_bytes = bytes.fromhex(event_id_hex)

        keypair = coincurve.PrivateKey(privkey_bytes)
        return keypair.sign_schnorr(msg_bytes).hex()

    @staticmethod
    def verify(event: dict[str, Any]) -> bool:
        """Verify a Nostr event's ID and signature.

        The ID alone is NOT authentication: it is a plain SHA-256 over public
        fields with no secret input, so anyone can compute a matching ID for an
        event they forged. Authenticity comes solely from the BIP-340 signature.

        Returns:
            True only if the ID matches AND the signature is a valid BIP-340
            signature over that ID under the claimed pubkey. False otherwise.

        Raises:
            CryptoBackendUnavailableError: If coincurve is unavailable, so the
                signature cannot be checked. This fails closed and loudly,
                consistent with sign() and pubkey_from_private_key(). It is a
                RuntimeError subclass and never silently passes.
        """
        # Verify ID
        computed_id = NostrEvent.compute_id(event)
        if computed_id != event.get("id", ""):
            return False

        # Raised before the try below, so a missing backend can never be caught
        # and turned into a plain False — an unverifiable event must not be
        # reported as merely invalid.
        if not _HAS_COINCURVE:
            raise CryptoBackendUnavailableError(
                "Signature verification is unavailable. Refusing to treat the "
                "event as verified: the event ID is a plain hash of public "
                f"fields and proves nothing about authenticity. {_MISSING_BACKEND_HINT}"
            )

        pubkey_hex = event.get("pubkey", "")
        sig_hex = event.get("sig", "")
        if not pubkey_hex or not sig_hex:
            return False

        try:
            msg_bytes = bytes.fromhex(event["id"])
            sig_bytes = bytes.fromhex(sig_hex)
            # BIP-340 verification takes the 32-byte x-only pubkey directly.
            pubkey = coincurve.PublicKeyXOnly(bytes.fromhex(pubkey_hex))
            return bool(pubkey.verify(sig_bytes, msg_bytes))
        except Exception:
            # Malformed pubkey/signature/id — not authentic. Distinct from a
            # missing backend, which is raised above and never reaches here.
            return False

    @staticmethod
    def create(
        kind: int,
        content: str,
        tags: list[list[str]],
        private_key: Optional[str] = None,
        created_at: Optional[int] = None,
        pubkey: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create a Nostr event, optionally signed.

        If private_key is provided, the event is signed. Otherwise, an unsigned
        event is returned with empty sig (for external signing).

        Args:
            kind: Nostr event kind number.
            content: Event content string.
            tags: List of tag arrays.
            private_key: Hex-encoded 32-byte private key (optional).
            created_at: Unix timestamp (defaults to now).
            pubkey: Explicit pubkey hex (used if private_key is not provided).

        Returns:
            Complete Nostr event dict with id, pubkey, sig, etc.
        """
        if created_at is None:
            created_at = int(time.time())

        if private_key:
            derived_pubkey = NostrEvent.pubkey_from_private_key(private_key)
        elif pubkey:
            derived_pubkey = pubkey
        else:
            derived_pubkey = ""

        event: dict[str, Any] = {
            "pubkey": derived_pubkey,
            "created_at": created_at,
            "kind": kind,
            "tags": tags,
            "content": content,
        }

        event["id"] = NostrEvent.compute_id(event)

        if private_key:
            event["sig"] = NostrEvent.sign(event["id"], private_key)
        else:
            event["sig"] = ""

        return event

    @staticmethod
    def create_unsigned(
        kind: int,
        content: str,
        tags: list[list[str]],
        pubkey: str,
        created_at: Optional[int] = None,
    ) -> dict[str, Any]:
        """Create an unsigned Nostr event for external signing.

        Returns:
            Event dict with computed ID but empty sig.
        """
        return NostrEvent.create(
            kind=kind,
            content=content,
            tags=tags,
            pubkey=pubkey,
            created_at=created_at,
        )
