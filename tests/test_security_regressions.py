"""Security regression tests for fail-open / budget-bypass defects.

Each test in this module corresponds to a confirmed vulnerability. They are
written to fail against the pre-fix code and pass after the fix.

Covered:
  1. NostrEvent.verify() fail-open when the crypto backend is unavailable.
  2. L402Client.pay_and_access() ignoring max_amount_sats.
  3. Budget check skipped when the invoice amount is unparseable.
  4. Reputation scoring ignoring out-of-range ratings (already correct — locked in).
  5. AgentManager trusting unverified relay events.
"""

from unittest.mock import AsyncMock, patch

import pytest

from le_agent_sdk.agent.manager import AgentManager
from le_agent_sdk.l402.client import L402Client
from le_agent_sdk.models.attestation import AgentAttestation
from le_agent_sdk.nostr import event as event_module
from le_agent_sdk.nostr.event import (
    CryptoBackendUnavailableError,
    NostrEvent,
    Secp256k1UnavailableError,
)

# --- Fixtures / helpers -----------------------------------------------------

# A well-formed event whose `id` is a genuine SHA-256 of its public fields but
# whose `sig` is garbage. An attacker can compute `id` offline (no secret input),
# so ID-only validation is not authentication.
def _forged_event(pubkey: str = "de" * 32) -> dict:
    event = {
        "pubkey": pubkey,
        "created_at": 1700000000,
        "kind": 38400,
        "tags": [["d", "svc-a"], ["s", "ai"]],
        "content": "Totally legit service",
    }
    event["id"] = NostrEvent.compute_id(event)  # attacker-computable
    event["sig"] = "00" * 64  # not a valid BIP-340 signature
    return event


class TestVerifyFailsClosedWithoutCryptoBackend:
    """Finding 1: verify() returned True when the crypto backend was unimportable.

    The backend moved from secp256k1 to coincurve in 0.4.0. coincurve ships
    prebuilt wheels, so the "absent backend" case is far less likely to occur by
    accident than it was — but "unlikely" is not "impossible" (an unusual
    platform with no wheel, or a pruned install), and the fail-open bug this
    class covers is exactly what happens when an unlikely branch is left
    undefined. The behaviour is still pinned.
    """

    def test_verify_raises_when_backend_unavailable(self):
        """Missing backend must be loud, not a silent pass.

        Consistent with sign()/pubkey_from_private_key(), which already raise.
        """
        with patch.object(event_module, "_HAS_COINCURVE", False):
            with pytest.raises(RuntimeError, match="coincurve"):
                NostrEvent.verify(_forged_event())

    def test_verify_raises_specific_error_type(self):
        """The raised type must stay catchable under both names."""
        with patch.object(event_module, "_HAS_COINCURVE", False):
            with pytest.raises(CryptoBackendUnavailableError):
                NostrEvent.verify(_forged_event())
        # The pre-0.4.0 name is an alias, so existing handlers keep working.
        with patch.object(event_module, "_HAS_COINCURVE", False):
            with pytest.raises(Secp256k1UnavailableError):
                NostrEvent.verify(_forged_event())

    def test_forged_event_does_not_verify_as_true(self):
        """A forged event must never come back as verified.

        Regardless of whether the backend is installed, the one outcome that must
        be impossible is a `True` return for an event with a bogus signature.
        """
        with patch.object(event_module, "_HAS_COINCURVE", False):
            try:
                result = NostrEvent.verify(_forged_event())
            except RuntimeError:
                return  # fail-closed: acceptable
            assert result is not True, "forged event verified as authentic"

    def test_verify_still_rejects_id_mismatch_before_dep_check(self):
        """A tampered ID is rejected without needing the backend."""
        event = _forged_event()
        event["content"] = "tampered after id was computed"
        with patch.object(event_module, "_HAS_COINCURVE", False):
            assert NostrEvent.verify(event) is False

    def test_sign_raises_when_backend_unavailable(self):
        with patch.object(event_module, "_HAS_COINCURVE", False):
            with pytest.raises(CryptoBackendUnavailableError, match="coincurve"):
                NostrEvent.sign("ab" * 32, "01" * 32)

    def test_pubkey_derivation_raises_when_backend_unavailable(self):
        with patch.object(event_module, "_HAS_COINCURVE", False):
            with pytest.raises(CryptoBackendUnavailableError, match="coincurve"):
                NostrEvent.pubkey_from_private_key("01" * 32)


class TestVerifyAcceptsGenuineRejectsForged:
    """The signature path must discriminate, not just refuse everything.

    Fixing a fail-open bug by failing everything closed would be no fix. These
    tests exercise the real BIP-340 path with genuine signatures.

    Before 0.4.0 these signed fixtures with coincurve and shimmed it over the
    secp256k1 binding, because secp256k1 needs a native build and was routinely
    unimportable — so the signature path could not otherwise be tested at all.
    coincurve is now the runtime backend, so the shim is gone and these drive
    the real production code path end to end. Cross-implementation agreement is
    covered separately in test_interop.py; the concern here is that verify()
    tells genuine and forged apart.
    """

    @staticmethod
    def _sign(private_hex: str, content: str = "genuine") -> dict:
        """Build a genuinely signed event through the SDK's own signing path."""
        return NostrEvent.create(
            kind=38400,
            content=content,
            tags=[["d", "svc-a"]],
            private_key=private_hex,
            created_at=1700000000,
        )

    def test_genuine_signed_event_verifies(self):
        assert NostrEvent.verify(self._sign("01" * 32)) is True

    def test_forged_signature_rejected(self):
        event = self._sign("01" * 32)
        event["sig"] = "00" * 64
        assert NostrEvent.verify(event) is False

    def test_tampered_content_rejected(self):
        event = self._sign("01" * 32)
        event["content"] = "tampered"
        assert NostrEvent.verify(event) is False

    def test_event_reattributed_to_another_pubkey_rejected(self):
        """The core attack: swap the pubkey and recompute a valid ID.

        The ID check alone passes here — it is just a hash of public fields.
        Only the signature check stops it.
        """
        event = self._sign("01" * 32)

        event["pubkey"] = NostrEvent.pubkey_from_private_key("02" * 32)
        event["id"] = NostrEvent.compute_id(event)  # ID now matches again

        assert NostrEvent.compute_id(event) == event["id"], "ID check would pass"
        assert NostrEvent.verify(event) is False, "reattributed event was accepted"

    def test_signature_from_different_key_rejected(self):
        event = self._sign("01" * 32)
        event["sig"] = NostrEvent.sign(event["id"], "03" * 32)
        assert NostrEvent.verify(event) is False

    def test_sign_verify_round_trips_through_public_api(self):
        """create() -> verify() must hold for the documented entry point."""
        event = NostrEvent.create(
            kind=38400,
            content="round trip",
            tags=[["d", "svc-a"], ["s", "ai"]],
            private_key="04" * 32,
        )
        assert event["pubkey"] == NostrEvent.pubkey_from_private_key("04" * 32)
        assert NostrEvent.verify(event) is True


class TestPayAndAccessRespectsMaxAmount:
    """Finding 2: pay_and_access never read self._max_amount_sats."""

    @pytest.mark.asyncio
    async def test_pay_and_access_rejects_invoice_over_instance_limit(self):
        # 10,000,000 sats == 100m (milli-BTC) invoice, limit is 100 sats.
        expensive = "lnbc100m1pvjluezpp5qqqsyqcyq5rqwzqfqqqsyqcyq5rqwzqfqqqsyqcyq5rq"
        paid = []

        async def pay_callback(invoice):
            paid.append(invoice)
            return "ab" * 32

        client = L402Client(max_amount_sats=100)

        with patch.object(client, "_ensure_client") as mock_ensure:
            mock_http = AsyncMock()
            mock_http.request.return_value = _make_402(expensive)
            mock_ensure.return_value = mock_http

            with pytest.raises(ValueError, match="exceeds maximum"):
                await client.pay_and_access("https://x.test/r", pay_callback)

        assert paid == [], "wallet callback was invoked for an over-budget invoice"

    @pytest.mark.asyncio
    async def test_pay_and_access_allows_invoice_under_limit(self):
        cheap = "lnbc10u1pvjluezpp5qqqsyqcyq5rqwzqfqqqsyqcyq5rqwzqfqqqsyqcyq5rq"  # 1000 sats

        async def pay_callback(invoice):
            return "ab" * 32

        client = L402Client(max_amount_sats=5000)

        with patch.object(client, "_ensure_client") as mock_ensure:
            mock_http = AsyncMock()
            mock_http.request.side_effect = [_make_402(cheap), _make_ok()]
            mock_ensure.return_value = mock_http

            resp = await client.pay_and_access("https://x.test/r", pay_callback)

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_pay_and_access_per_call_override_beats_instance_limit(self):
        cheap = "lnbc10u1pvjluezpp5qqqsyqcyq5rqwzqfqqqsyqcyq5rqwzqfqqqsyqcyq5rq"  # 1000 sats

        async def pay_callback(invoice):
            return "ab" * 32

        client = L402Client(max_amount_sats=100_000)

        with patch.object(client, "_ensure_client") as mock_ensure:
            mock_http = AsyncMock()
            mock_http.request.return_value = _make_402(cheap)
            mock_ensure.return_value = mock_http

            with pytest.raises(ValueError, match="exceeds maximum"):
                await client.pay_and_access(
                    "https://x.test/r", pay_callback, max_amount_sats=10
                )


class TestUnknownAmountIsRefused:
    """Finding 3: unparseable amount was read as 'no limit applies'."""

    def test_amountless_invoice_decodes_to_none(self):
        amountless = "lnbc1pvjluezpp5qqqsyqcyq5rqwzqfqqqsyqcyq5rqwzqfqqqsyqcyq5rqwzqfqypq"
        assert L402Client._decode_invoice_amount_sats(amountless) is None

    def test_amountless_invoice_is_not_misparsed_from_data_part(self):
        """The HRP amount must not be matched from inside the bech32 data part.

        Pre-fix the regex was unanchored, so an amountless (unbounded) invoice
        whose data contained '<digits><munp>1' reported a small bogus amount and
        sailed through the budget check.
        """
        assert L402Client._decode_invoice_amount_sats("lnbc1pabc9u1def") is None
        assert L402Client._decode_invoice_amount_sats("lnbc1pvjl5p1uez") is None

    @pytest.mark.asyncio
    async def test_access_refuses_unparseable_invoice_when_budget_set(self):
        garbage = "not-a-parseable-bolt11-invoice"
        paid = []

        async def pay_callback(invoice):
            paid.append(invoice)
            return "ab" * 32

        client = L402Client(pay_invoice_callback=pay_callback, max_amount_sats=1000)

        with patch.object(client, "_ensure_client") as mock_ensure:
            mock_http = AsyncMock()
            mock_http.request.return_value = _make_402(garbage)
            mock_ensure.return_value = mock_http

            with pytest.raises(ValueError, match="amount"):
                await client.access("https://x.test/r")

        assert paid == [], "unbounded invoice was handed to the wallet callback"

    @pytest.mark.asyncio
    async def test_pay_and_access_refuses_unparseable_invoice_when_budget_set(self):
        garbage = "not-a-parseable-bolt11-invoice"
        paid = []

        async def pay_callback(invoice):
            paid.append(invoice)
            return "ab" * 32

        client = L402Client(max_amount_sats=1000)

        with patch.object(client, "_ensure_client") as mock_ensure:
            mock_http = AsyncMock()
            mock_http.request.return_value = _make_402(garbage)
            mock_ensure.return_value = mock_http

            with pytest.raises(ValueError, match="amount"):
                await client.pay_and_access("https://x.test/r", pay_callback)

        assert paid == []

    @pytest.mark.asyncio
    async def test_unparseable_invoice_still_paid_when_no_budget_configured(self):
        """No budget configured == caller explicitly accepted unbounded payment.

        Refusing here would break the documented `None means no limit` contract
        without adding safety: with no limit, a known 10M-sat invoice is paid too.
        """
        garbage = "not-a-parseable-bolt11-invoice"
        paid = []

        async def pay_callback(invoice):
            paid.append(invoice)
            return "ab" * 32

        client = L402Client(pay_invoice_callback=pay_callback, max_amount_sats=None)

        with patch.object(client, "_ensure_client") as mock_ensure:
            mock_http = AsyncMock()
            mock_http.request.side_effect = [_make_402(garbage), _make_ok()]
            mock_ensure.return_value = mock_http

            resp = await client.access("https://x.test/r")

        assert resp.status_code == 200
        assert paid == [garbage]


class TestReputationIgnoresOutOfRangeRatings:
    """Finding 4: verified as already-correct. Locked in against regression."""

    @pytest.mark.asyncio
    async def test_out_of_range_ratings_excluded_from_average(self):
        attestations = [
            AgentAttestation(rating=5),
            AgentAttestation(rating=5),
            AgentAttestation(rating=9999),  # forged/out-of-range
            AgentAttestation(rating=0),     # unparsed/missing
            AgentAttestation(rating=-5),
        ]
        mgr = AgentManager()
        with patch.object(mgr, "get_attestations", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = attestations
            score = await mgr.get_reputation_score("pub")

        assert score == 5.0

    @pytest.mark.asyncio
    async def test_no_valid_ratings_returns_none(self):
        mgr = AgentManager()
        with patch.object(mgr, "get_attestations", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = [AgentAttestation(rating=0)]
            score = await mgr.get_reputation_score("pub")

        assert score is None


class TestManagerVerifiesRelayEvents:
    """Finding 5: raw relay JSON flowed into models with no verification."""

    @pytest.mark.asyncio
    async def test_discover_drops_events_failing_verification(self):
        mgr = AgentManager()
        good, bad = _forged_event("aa" * 32), _forged_event("bb" * 32)

        with patch.object(mgr, "_query_relays", new_callable=AsyncMock) as mock_query:
            mock_query.return_value = [good, bad]
            with patch.object(
                NostrEvent, "verify", side_effect=lambda e: e["pubkey"] == "aa" * 32
            ):
                caps = await mgr.discover()

        assert len(caps) == 1, "unverified event was not dropped"
        assert caps[0].pubkey == "aa" * 32

    @pytest.mark.asyncio
    async def test_get_attestations_drops_events_failing_verification(self):
        mgr = AgentManager()
        good, bad = _forged_event("aa" * 32), _forged_event("bb" * 32)

        with patch.object(mgr, "_query_relays", new_callable=AsyncMock) as mock_query:
            mock_query.return_value = [good, bad]
            with patch.object(
                NostrEvent, "verify", side_effect=lambda e: e["pubkey"] == "aa" * 32
            ):
                atts = await mgr.get_attestations("aa" * 32)

        assert len(atts) == 1
        assert atts[0].pubkey == "aa" * 32

    @pytest.mark.asyncio
    async def test_discover_drops_all_when_every_event_is_forged(self):
        """One malicious relay must not be able to inject attributed events."""
        mgr = AgentManager()

        with patch.object(mgr, "_query_relays", new_callable=AsyncMock) as mock_query:
            mock_query.return_value = [_forged_event(), _forged_event("cc" * 32)]
            with patch.object(NostrEvent, "verify", return_value=False):
                caps = await mgr.discover()

        assert caps == []

    @pytest.mark.asyncio
    async def test_verification_error_propagates(self):
        """A missing native dep must surface, not silently empty the results."""
        mgr = AgentManager()

        with patch.object(mgr, "_query_relays", new_callable=AsyncMock) as mock_query:
            mock_query.return_value = [_forged_event()]
            with patch.object(
                NostrEvent, "verify", side_effect=RuntimeError("coincurve required")
            ):
                with pytest.raises(RuntimeError, match="coincurve"):
                    await mgr.discover()

    @pytest.mark.asyncio
    async def test_listen_requests_drops_forged_events(self):
        """The streaming ingestion point must verify too, not just the queries."""
        good = _forged_event("aa" * 32)
        good["kind"] = 38401
        bad = _forged_event("bb" * 32)
        bad["kind"] = 38401

        mgr = AgentManager(private_key="01" * 32)
        received = []

        # The forged event is streamed FIRST: without verification it is what the
        # caller receives, so ordering is what gives this test its teeth.
        with patch.object(
            type(mgr), "pubkey", property(lambda self: "aa" * 32)
        ), patch.object(NostrEvent, "verify", side_effect=lambda e: e["pubkey"] == "aa" * 32):
            with patch(
                "le_agent_sdk.agent.manager.RelayClient", _FakeRelayClient([bad, good])
            ):
                async for req in mgr.listen_requests():
                    received.append(req)
                    if len(received) >= 1:
                        break

        assert len(received) == 1
        assert received[0].pubkey == "aa" * 32, "forged request was yielded to caller"

    @pytest.mark.asyncio
    async def test_listen_requests_surfaces_missing_dep_without_reconnect_storm(self):
        """A missing dep must not be mistaken for a relay fault and retried."""
        event = _forged_event("aa" * 32)
        event["kind"] = 38401

        mgr = AgentManager(private_key="01" * 32)
        fake_relay_cls = _FakeRelayClient([event])

        with patch.object(
            type(mgr), "pubkey", property(lambda self: "aa" * 32)
        ), patch.object(
            NostrEvent, "verify", side_effect=CryptoBackendUnavailableError("coincurve missing")
        ):
            with patch("le_agent_sdk.agent.manager.RelayClient", fake_relay_cls):
                with pytest.raises(CryptoBackendUnavailableError):
                    async for _ in mgr.listen_requests():
                        pass

        # Exactly one connect: the error escaped instead of driving reconnects.
        assert fake_relay_cls.connect_count == 1, (
            f"missing dep triggered {fake_relay_cls.connect_count} connects "
            "(reconnect storm) instead of surfacing immediately"
        )


# --- Relay double -----------------------------------------------------------


def _FakeRelayClient(events: list[dict]):
    """Build a RelayClient stand-in class that streams `events` once.

    Returns a class (not an instance) because the manager constructs its own
    RelayClient objects. Connects are counted on the class so a reconnect storm
    is observable.
    """

    class _Fake:
        connect_count = 0

        async def connect(self, url):
            type(self).connect_count += 1

        async def subscribe(self, filters):
            return None

        async def listen(self):
            for event in events:
                yield "EVENT", ("sub-id", event)
            # Stream ends: modelled as a disconnect, which is what a real relay
            # closing the socket looks like to the manager.
            raise ConnectionError("relay closed the stream")

        async def close(self):
            return None

    return _Fake


# --- HTTP response doubles --------------------------------------------------


def _make_402(invoice: str):
    """Build a fake 402 response carrying an L402 challenge."""
    resp = AsyncMock()
    resp.status_code = 402
    resp.headers = {
        "WWW-Authenticate": f'L402 macaroon="AGIAJEem", invoice="{invoice}"'
    }
    return resp


def _make_ok():
    resp = AsyncMock()
    resp.status_code = 200
    resp.headers = {}
    return resp
