"""Cross-implementation wire-compatibility tests.

Nostr events are a wire protocol: the event ID is a consensus value and the
signature must verify under any conforming implementation. A signature scheme
that only agrees with itself is worthless — a round-trip test would pass just as
happily on a private, incompatible curve.

So these tests check this SDK against implementations it shares no code with:

  * BIP-340 published test vectors (the standard itself).
  * Events signed by the .NET SDK (le-agent-sdk-dotnet) via NBitcoin.Secp256k1,
    committed under tests/fixtures/ so no .NET toolchain is needed here.

The fixtures are protocol artifacts, not scaffolding. If they stop verifying,
this SDK has broken compatibility with the network — regenerate them only when
the .NET SDK's wire format deliberately changes, never to make a test pass.
"""

import json
from pathlib import Path

import pytest

from le_agent_sdk.nostr.event import NostrEvent

FIXTURES = Path(__file__).parent / "fixtures"


def _load_dotnet_events():
    with open(FIXTURES / "dotnet_signed_events.json", encoding="utf-8") as fh:
        return json.load(fh)["events"]


def _strip(event: dict) -> dict:
    """Drop harness-only annotations, leaving the on-the-wire event."""
    return {k: v for k, v in event.items() if not k.startswith("_")}


DOTNET_EVENTS = _load_dotnet_events()
DOTNET_IDS = [e["_label"] for e in DOTNET_EVENTS]


class TestDotNetSignedEventsVerify:
    """.NET (NBitcoin.Secp256k1) -> Python (coincurve)."""

    @pytest.mark.parametrize("event", DOTNET_EVENTS, ids=DOTNET_IDS)
    def test_dotnet_event_id_agrees(self, event):
        """Both implementations must derive the same ID from the same event.

        Covers the canonical NIP-01 serialization, including the non-ASCII and
        astral-plane escaping rules the two languages implement separately.
        """
        event = _strip(event)
        assert NostrEvent.compute_id(event) == event["id"]

    @pytest.mark.parametrize("event", DOTNET_EVENTS, ids=DOTNET_IDS)
    def test_dotnet_signature_verifies(self, event):
        """A signature made by the .NET SDK must verify here."""
        assert NostrEvent.verify(_strip(event)) is True

    def test_fixture_actually_covers_non_ascii(self):
        """Guard the guard: the unicode case must not silently vanish.

        Non-ASCII is where independently written serializers realistically
        diverge, so losing that fixture would gut these tests without failing
        anything.
        """
        assert any(
            any(ord(ch) > 0xFFFF for ch in e["content"]) for e in DOTNET_EVENTS
        ), "no astral-plane content in fixtures"
        assert any(
            any(ord(ch) > 127 for ch in e["content"]) for e in DOTNET_EVENTS
        ), "no non-ASCII content in fixtures"


class TestDotNetSignedEventsRejectTampering:
    """The cross-impl fixtures must not verify once altered.

    Without these, a verify() that returned True unconditionally would pass
    every test above.
    """

    def test_tampered_content_breaks_dotnet_event(self):
        event = _strip(DOTNET_EVENTS[0])
        event["content"] = "tampered by a relay in transit"
        assert NostrEvent.verify(event) is False

    def test_swapped_signature_breaks_dotnet_event(self):
        event = _strip(DOTNET_EVENTS[0])
        event["sig"] = _strip(DOTNET_EVENTS[1])["sig"]
        assert NostrEvent.verify(event) is False

    def test_dotnet_event_reattributed_to_another_pubkey_rejected(self):
        """Reattribution with a recomputed ID: the ID check passes, sig must not."""
        event = _strip(DOTNET_EVENTS[0])
        event["pubkey"] = _strip(DOTNET_EVENTS[1])["pubkey"]
        event["id"] = NostrEvent.compute_id(event)

        assert NostrEvent.compute_id(event) == event["id"], "ID check would pass"
        assert NostrEvent.verify(event) is False


class TestPythonSignedEventsMatchDotNetKeys:
    """Python -> .NET, verified through values .NET produced.

    Signatures are randomized per BIP-340 aux, so Python's signature bytes are
    not expected to equal .NET's. Key derivation is deterministic, so agreement
    on the pubkey is checkable here; the signature direction (Python-signed
    events verifying under .NET) is exercised out-of-band against the .NET SDK.
    """

    @pytest.mark.parametrize(
        "private_key,expected_pubkey",
        [
            # (private key, x-only pubkey as derived by .NET's GetPublicKey()).
            # These also match the BIP-340 published vectors.
            (
                "0000000000000000000000000000000000000000000000000000000000000001",
                "79be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798",
            ),
            (
                "b7e151628aed2a6abf7158809cf4f3c762e7160f38b4da56a784d9045190cfef",
                "dff1d77f2a671c5f36183726db2341be58feae1da2deced843240f7b502ba659",
            ),
            (
                "c90fdaa22168c234c4c6628b80dc1cd129024e088a67cc74020bbea63b14e5c9",
                "dd308afec5777e13121fa72b9cc1b7cc0139715309b086c960e18fd969774eb8",
            ),
            (
                "0b432b2677937381aef05bb02a66ecd012773062cf3fa2549e44f58ed2401710",
                "25d1dff95105f5253c4022f628a996ad3a0d95fbf21d468a1b33f8c160d8f517",
            ),
        ],
    )
    def test_pubkey_derivation_matches_dotnet(self, private_key, expected_pubkey):
        """x-only derivation must drop y-parity the same way in both SDKs.

        Two of these keys have odd y, so a binding that leaked parity into the
        x-only key would disagree here and silently produce events attributed to
        a pubkey no one else computes.
        """
        assert NostrEvent.pubkey_from_private_key(private_key) == expected_pubkey

    def test_locally_signed_event_verifies_under_dotnet_derived_pubkey(self):
        """Sign here, verify against the pubkey .NET says the key has."""
        private_key = "b7e151628aed2a6abf7158809cf4f3c762e7160f38b4da56a784d9045190cfef"
        event = NostrEvent.create(
            kind=38400,
            content="signed by python, keyed by .NET's derivation",
            tags=[["d", "svc-1"]],
            private_key=private_key,
            created_at=1700000000,
        )
        assert event["pubkey"] == (
            "dff1d77f2a671c5f36183726db2341be58feae1da2deced843240f7b502ba659"
        )
        assert NostrEvent.verify(event) is True


class TestBip340PublishedVectors:
    """Conformance to the BIP-340 standard itself.

    From the BIP-340 reference test vectors (indices 0-4). These pin the SDK to
    the spec rather than to any one library's behaviour, and would catch a
    backend swap that changed message-hashing semantics (e.g. a binding that
    hashes the message again before signing — the event ID is already a digest
    and must be signed as-is).

    Provenance matters here: a vector copied out of the library under test would
    only prove the library agrees with itself. Each signature below was
    independently reproduced by NBitcoin.Secp256k1 (the .NET SDK's backend)
    signing the same key/message/aux_rand — BIP-340 signing is deterministic
    given aux_rand, so two unrelated implementations emitting identical bytes is
    what establishes these as the spec's values.
    """

    # (index, pubkey, message, signature, expected)
    VECTORS = [
        (
            0,
            "f9308a019258c31049344f85f89d5229b531c845836f99b08601f113bce036f9",
            "0000000000000000000000000000000000000000000000000000000000000000",
            "e907831f80848d1069a5371b402410364bdf1c5f8307b0084c55f1ce2dca8215"
            "25f66a4a85ea8b71e482a74f382d2ce5ebeee8fdb2172f477df4900d310536c0",
            True,
        ),
        (
            1,
            "dff1d77f2a671c5f36183726db2341be58feae1da2deced843240f7b502ba659",
            "243f6a8885a308d313198a2e03707344a4093822299f31d0082efa98ec4e6c89",
            "6896bd60eeae296db48a229ff71dfe071bde413e6d43f917dc8dcf8c78de3341"
            "8906d11ac976abccb20b091292bff4ea897efcb639ea871cfa95f6de339e4b0a",
            True,
        ),
        (
            2,
            "dd308afec5777e13121fa72b9cc1b7cc0139715309b086c960e18fd969774eb8",
            "7e2d58d8b3bcdf1abadec7829054f90dda9805aab56c77333024b9d0a508b75c",
            "5831aaeed7b44bb74e5eab94ba9d4294c49bcf2a60728d8b4c200f50dd313c1b"
            "ab745879a5ad954a72c45a91c3a51d3c7adea98d82f8481e0e1e03674a6f3fb7",
            True,
        ),
        (
            3,
            "25d1dff95105f5253c4022f628a996ad3a0d95fbf21d468a1b33f8c160d8f517",
            "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
            "7eb0509757e246f19449885651611cb965ecc1a187dd51b64fda1edc9637d5ec"
            "97582b9cb13db3933705b32ba982af5af25fd78881ebb32771fc5922efc66ea3",
            True,
        ),
        (
            4,
            "d69c3509bb99e412e68b0fe8544e72837dfa30746d8be2aa65975f29d22dc7b9",
            "4df3c3f68fcc83b27e9d42c90431a72499f17875c81a599b566c9889b9696703",
            "00000000000000000000003b78ce563f89a0ed9414f5aa28ad0d96d6795f9c63"
            "76afb1548af603b3eb45c9f8207dee1060cb71c04e80f593060b07d28308d7f4",
            True,
        ),
    ]

    @pytest.mark.parametrize(
        "index,pubkey,message,signature,expected",
        VECTORS,
        ids=[f"bip340-vector-{v[0]}" for v in VECTORS],
    )
    def test_bip340_vector(self, index, pubkey, message, signature, expected):
        """Drive the vector through verify() by presenting it as an event.

        verify() checks the ID before the signature, so the vector's message is
        placed in `id`: that is exactly how NIP-01 uses it — the 32-byte value
        signed. The ID check is neutralised by monkeypatching compute_id, since
        these vectors are raw BIP-340 messages, not NIP-01 serializations.
        """
        event = {"id": message, "pubkey": pubkey, "sig": signature}

        original = NostrEvent.compute_id
        try:
            NostrEvent.compute_id = staticmethod(lambda e: e["id"])
            assert NostrEvent.verify(event) is expected
        finally:
            NostrEvent.compute_id = original

    def test_vector_signature_rejected_under_wrong_pubkey(self):
        """Negative control: the vectors must not verify under any key."""
        _, _, message, signature, _ = self.VECTORS[1]
        event = {
            "id": message,
            "pubkey": self.VECTORS[2][1],  # a different, valid pubkey
            "sig": signature,
        }

        original = NostrEvent.compute_id
        try:
            NostrEvent.compute_id = staticmethod(lambda e: e["id"])
            assert NostrEvent.verify(event) is False
        finally:
            NostrEvent.compute_id = original
