"""Nostr event builder and signing (NIP-01).

Handles event creation, ID computation (SHA-256 of canonical serialization),
and Schnorr signing (BIP-340) via the secp256k1 library.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Optional

# secp256k1 is a hard dependency, but it requires a native build and can be
# absent from an otherwise "successful" install. Import defensively so that
# import-time failure is deferred to the operations that actually need it.
#
# Every operation that depends on it — sign(), pubkey_from_private_key() and
# verify() — raises RuntimeError when it is missing. None of them degrade to a
# weaker check. Only building/serializing unsigned events works without it.
try:
    import secp256k1

    _HAS_SECP256K1 = True
except ImportError:
    _HAS_SECP256K1 = False


class Secp256k1UnavailableError(RuntimeError):
    """Raised when an operation needs secp256k1 but it could not be imported.

    Subclasses RuntimeError so existing `except RuntimeError` handlers keep
    working. Distinguishable so that callers can tell this environment fault
    apart from an operational error (e.g. a relay disconnect) and avoid
    retrying something that will never succeed.
    """


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
        if not _HAS_SECP256K1:
            raise Secp256k1UnavailableError(
                "secp256k1 library is required for key derivation. "
                "Install with: pip install secp256k1"
            )
        privkey_bytes = bytes.fromhex(private_key_hex)
        if len(privkey_bytes) != 32:
            raise ValueError(
                f"Private key must be 32 bytes, got {len(privkey_bytes)} bytes"
            )
        keypair = secp256k1.PrivateKey(privkey_bytes)
        # secp256k1 public key is 33 bytes (compressed); strip the prefix byte
        pubkey_bytes = keypair.pubkey.serialize(compressed=True)
        # x-only pubkey is the last 32 bytes of the compressed key (drop 0x02/0x03 prefix)
        return pubkey_bytes[1:].hex()

    @staticmethod
    def sign(event_id_hex: str, private_key_hex: str) -> str:
        """Create a Schnorr signature (BIP-340) over the event ID.

        Returns:
            64-byte signature as hex string.
        """
        if not _HAS_SECP256K1:
            raise Secp256k1UnavailableError(
                "secp256k1 library is required for signing. "
                "Install with: pip install secp256k1"
            )
        privkey_bytes = bytes.fromhex(private_key_hex)
        if len(privkey_bytes) != 32:
            raise ValueError(
                f"Private key must be 32 bytes, got {len(privkey_bytes)} bytes"
            )
        msg_bytes = bytes.fromhex(event_id_hex)

        keypair = secp256k1.PrivateKey(privkey_bytes)
        sig = keypair.schnorr_sign(msg_bytes, bip340tag=b"", raw=True)
        return sig.hex()

    @staticmethod
    def verify(event: dict[str, Any]) -> bool:
        """Verify a Nostr event's ID and signature.

        The ID alone is NOT authentication: it is a plain SHA-256 over public
        fields with no secret input, so anyone can compute a matching ID for an
        event they forged. Authenticity comes solely from the BIP-340 signature,
        which requires secp256k1.

        Returns:
            True only if the ID matches AND the signature is a valid BIP-340
            signature over that ID under the claimed pubkey. False otherwise.

        Raises:
            Secp256k1UnavailableError: If secp256k1 is unavailable, so the
                signature cannot be checked. This fails closed and loudly,
                consistent with sign() and pubkey_from_private_key(). It is a
                RuntimeError subclass and never silently passes.
        """
        # Verify ID
        computed_id = NostrEvent.compute_id(event)
        if computed_id != event.get("id", ""):
            return False

        if not _HAS_SECP256K1:
            raise Secp256k1UnavailableError(
                "secp256k1 library is required for signature verification. "
                "Refusing to treat the event as verified: the event ID is a "
                "plain hash of public fields and proves nothing about "
                "authenticity. Install with: pip install secp256k1"
            )

        pubkey_hex = event.get("pubkey", "")
        sig_hex = event.get("sig", "")
        if not pubkey_hex or not sig_hex:
            return False

        try:
            msg_bytes = bytes.fromhex(event["id"])
            sig_bytes = bytes.fromhex(sig_hex)
            # Reconstruct compressed pubkey (prepend 0x02)
            pubkey_bytes = bytes.fromhex("02" + pubkey_hex)
            pubkey = secp256k1.PublicKey(pubkey_bytes, raw=True)
            return pubkey.schnorr_verify(msg_bytes, sig_bytes, bip340tag=b"", raw=True)
        except Exception:
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
