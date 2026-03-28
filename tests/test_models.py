"""Tests for ASA protocol data models."""

import pytest

from le_agent_sdk.models.capability import AgentCapability, AgentPricing
from le_agent_sdk.models.request import AgentServiceRequest
from le_agent_sdk.models.agreement import AgentServiceAgreement
from le_agent_sdk.models.attestation import AgentAttestation


# --- AgentPricing ---


class TestAgentPricing:
    def test_to_tag(self):
        p = AgentPricing(amount=100, unit="sats", model="per-request")
        assert p.to_tag() == ["price", "100", "sats", "per-request"]

    def test_from_tag_full(self):
        tag = ["price", "50", "msats", "per-token"]
        p = AgentPricing.from_tag(tag)
        assert p.amount == 50
        assert p.unit == "msats"
        assert p.model == "per-token"

    def test_from_tag_minimal(self):
        tag = ["price", "10"]
        p = AgentPricing.from_tag(tag)
        assert p.amount == 10
        assert p.unit == "sats"
        assert p.model == "per-request"

    def test_from_tag_invalid(self):
        with pytest.raises(ValueError):
            AgentPricing.from_tag(["price"])

    def test_roundtrip(self):
        original = AgentPricing(amount=42, unit="sats", model="per-minute")
        tag = original.to_tag()
        restored = AgentPricing.from_tag(tag)
        assert restored.amount == original.amount
        assert restored.unit == original.unit
        assert restored.model == original.model


# --- AgentCapability ---


class TestAgentCapability:
    def _sample_event(self) -> dict:
        return {
            "id": "abc123",
            "pubkey": "deadbeef",
            "created_at": 1700000000,
            "kind": 38400,
            "content": "A translation service",
            "tags": [
                ["d", "translate-v1"],
                ["s", "ai"],
                ["s", "translation"],
                ["price", "10", "sats", "per-request"],
                ["price", "1", "sats", "per-token"],
                ["l402", "https://api.example.com/l402/translate"],
                ["api_endpoint", "https://api.example.com/translate"],
                ["api_method", "POST"],
                ["schema", "https://api.example.com/schema.json"],
                ["t", "translation"],
                ["t", "ai"],
            ],
            "sig": "sig123",
        }

    def test_from_nostr_event(self):
        event = self._sample_event()
        cap = AgentCapability.from_nostr_event(event)

        assert cap.event_id == "abc123"
        assert cap.pubkey == "deadbeef"
        assert cap.created_at == 1700000000
        assert cap.service_id == "translate-v1"
        assert cap.categories == ["ai", "translation"]
        assert cap.content == "A translation service"
        assert len(cap.pricing) == 2
        assert cap.pricing[0].amount == 10
        assert cap.pricing[1].model == "per-token"
        assert cap.l402_endpoint == "https://api.example.com/l402/translate"
        assert cap.api_endpoint == "https://api.example.com/translate"
        assert cap.api_method == "POST"
        assert cap.schema_url == "https://api.example.com/schema.json"
        assert cap.hashtags == ["translation", "ai"]

    def test_to_nostr_tags(self):
        cap = AgentCapability(
            service_id="test-svc",
            categories=["ai"],
            pricing=[AgentPricing(amount=5, unit="sats", model="per-request")],
            l402_endpoint="https://example.com/l402",
            hashtags=["test"],
        )
        tags = cap.to_nostr_tags()

        assert ["d", "test-svc"] in tags
        assert ["s", "ai"] in tags
        assert ["price", "5", "sats", "per-request"] in tags
        assert ["l402", "https://example.com/l402"] in tags
        assert ["t", "test"] in tags

    def test_roundtrip(self):
        cap = AgentCapability(
            service_id="round-trip",
            categories=["ml", "vision"],
            content="Image recognition service",
            pricing=[AgentPricing(amount=25, unit="sats", model="per-request")],
            l402_endpoint="https://api.example.com/l402",
            api_endpoint="https://api.example.com/recognize",
            api_method="POST",
            schema_url="https://api.example.com/schema.json",
            hashtags=["vision", "ml"],
        )
        tags = cap.to_nostr_tags()

        event = {
            "id": "roundtrip-id",
            "pubkey": "roundtrip-pub",
            "created_at": 1700000001,
            "kind": 38400,
            "content": cap.content,
            "tags": tags,
            "sig": "",
        }
        restored = AgentCapability.from_nostr_event(event)

        assert restored.service_id == cap.service_id
        assert restored.categories == cap.categories
        assert restored.content == cap.content
        assert restored.l402_endpoint == cap.l402_endpoint
        assert restored.api_endpoint == cap.api_endpoint
        assert restored.api_method == cap.api_method
        assert restored.schema_url == cap.schema_url
        assert restored.hashtags == cap.hashtags
        assert len(restored.pricing) == len(cap.pricing)

    def test_empty_event(self):
        cap = AgentCapability.from_nostr_event({"tags": []})
        assert cap.service_id == ""
        assert cap.categories == []
        assert cap.pricing == []

    def test_kind_constant(self):
        assert AgentCapability.KIND == 38400

    def test_negotiable_default_true(self):
        cap = AgentCapability()
        assert cap.negotiable is True
        assert cap.min_price_sats is None
        tags = cap.to_nostr_tags()
        assert ["negotiable", "true"] in tags

    def test_negotiable_false(self):
        cap = AgentCapability(negotiable=False)
        tags = cap.to_nostr_tags()
        assert ["negotiable", "false"] in tags
        assert ["negotiable", "true"] not in tags

    def test_negotiable_floor(self):
        cap = AgentCapability(negotiable=True, min_price_sats=30000)
        tags = cap.to_nostr_tags()
        assert ["negotiable", "floor", "30000"] in tags

    def test_negotiable_parse_false(self):
        event = {
            "tags": [["d", "svc"], ["negotiable", "false"]],
            "content": "",
        }
        cap = AgentCapability.from_nostr_event(event)
        assert cap.negotiable is False
        assert cap.min_price_sats is None

    def test_negotiable_parse_floor(self):
        event = {
            "tags": [["d", "svc"], ["negotiable", "floor", "10000"]],
            "content": "",
        }
        cap = AgentCapability.from_nostr_event(event)
        assert cap.negotiable is True
        assert cap.min_price_sats == 10000

    def test_negotiable_roundtrip_floor(self):
        cap = AgentCapability(
            service_id="floor-rt",
            negotiable=True,
            min_price_sats=5000,
        )
        tags = cap.to_nostr_tags()
        event = {
            "id": "rt",
            "pubkey": "pk",
            "created_at": 1,
            "kind": 38400,
            "content": "",
            "tags": tags,
        }
        restored = AgentCapability.from_nostr_event(event)
        assert restored.negotiable is True
        assert restored.min_price_sats == 5000


# --- AgentServiceRequest ---


class TestAgentServiceRequest:
    def _sample_event(self) -> dict:
        return {
            "id": "req123",
            "pubkey": "requester_pub",
            "created_at": 1700000002,
            "kind": 38401,
            "content": "Need translation",
            "tags": [
                ["e", "cap_event_id"],
                ["p", "provider_pub"],
                ["budget", "500"],
                ["param", "source_lang", "en"],
                ["param", "target_lang", "es"],
            ],
            "sig": "sig456",
        }

    def test_from_nostr_event(self):
        event = self._sample_event()
        req = AgentServiceRequest.from_nostr_event(event)

        assert req.event_id == "req123"
        assert req.pubkey == "requester_pub"
        assert req.capability_event_id == "cap_event_id"
        assert req.provider_pubkey == "provider_pub"
        assert req.budget_sats == 500
        assert req.params == {"source_lang": "en", "target_lang": "es"}
        assert req.content == "Need translation"

    def test_to_nostr_tags(self):
        req = AgentServiceRequest(
            capability_event_id="cap1",
            provider_pubkey="prov1",
            budget_sats=100,
            params={"key": "val"},
        )
        tags = req.to_nostr_tags()

        assert ["e", "cap1"] in tags
        assert ["p", "prov1"] in tags
        assert ["budget", "100"] in tags
        assert ["param", "key", "val"] in tags

    def test_roundtrip(self):
        req = AgentServiceRequest(
            capability_event_id="cap_rt",
            provider_pubkey="prov_rt",
            budget_sats=250,
            content="Test request",
            params={"lang": "fr"},
        )
        tags = req.to_nostr_tags()
        event = {
            "id": "rt_req",
            "pubkey": "rt_pub",
            "created_at": 1700000003,
            "kind": 38401,
            "content": req.content,
            "tags": tags,
            "sig": "",
        }
        restored = AgentServiceRequest.from_nostr_event(event)
        assert restored.capability_event_id == req.capability_event_id
        assert restored.provider_pubkey == req.provider_pubkey
        assert restored.budget_sats == req.budget_sats
        assert restored.params == req.params

    def test_kind_constant(self):
        assert AgentServiceRequest.KIND == 38401


# --- AgentServiceAgreement ---


class TestAgentServiceAgreement:
    def _sample_event(self) -> dict:
        return {
            "id": "agr123",
            "pubkey": "provider_pub",
            "created_at": 1700000004,
            "kind": 38402,
            "content": "Agreement reached",
            "tags": [
                ["e", "req_event_id"],
                ["e", "cap_event_id"],
                ["p", "provider_pub"],
                ["p", "requester_pub"],
                ["price", "100"],
                ["l402", "https://api.example.com/l402/service"],
                ["terms", "Max 10 requests per minute"],
                ["expiration", "1700100000"],
            ],
            "sig": "sig789",
        }

    def test_from_nostr_event(self):
        event = self._sample_event()
        agr = AgentServiceAgreement.from_nostr_event(event)

        assert agr.event_id == "agr123"
        assert agr.request_event_id == "req_event_id"
        assert agr.capability_event_id == "cap_event_id"
        assert agr.provider_pubkey == "provider_pub"
        assert agr.requester_pubkey == "requester_pub"
        assert agr.agreed_price_sats == 100
        assert agr.l402_endpoint == "https://api.example.com/l402/service"
        assert agr.terms == "Max 10 requests per minute"
        assert agr.expires_at == 1700100000

    def test_to_nostr_tags(self):
        agr = AgentServiceAgreement(
            request_event_id="r1",
            capability_event_id="c1",
            provider_pubkey="prov",
            requester_pubkey="req",
            agreed_price_sats=50,
            l402_endpoint="https://example.com/l402",
            terms="Terms here",
            expires_at=1800000000,
        )
        tags = agr.to_nostr_tags()

        e_tags = [t for t in tags if t[0] == "e"]
        p_tags = [t for t in tags if t[0] == "p"]
        assert len(e_tags) == 2
        assert len(p_tags) == 2
        assert ["price", "50"] in tags
        assert ["l402", "https://example.com/l402"] in tags
        assert ["terms", "Terms here"] in tags
        assert ["expiration", "1800000000"] in tags

    def test_roundtrip(self):
        agr = AgentServiceAgreement(
            request_event_id="req_rt",
            capability_event_id="cap_rt",
            provider_pubkey="prov_rt",
            requester_pubkey="req_pub_rt",
            agreed_price_sats=75,
            l402_endpoint="https://example.com/l402/rt",
            terms="RT terms",
            content="RT content",
            expires_at=1900000000,
        )
        tags = agr.to_nostr_tags()
        event = {
            "id": "rt_agr",
            "pubkey": "rt_pub",
            "created_at": 1700000005,
            "kind": 38402,
            "content": agr.content,
            "tags": tags,
            "sig": "",
        }
        restored = AgentServiceAgreement.from_nostr_event(event)
        assert restored.request_event_id == agr.request_event_id
        assert restored.capability_event_id == agr.capability_event_id
        assert restored.provider_pubkey == agr.provider_pubkey
        assert restored.requester_pubkey == agr.requester_pubkey
        assert restored.agreed_price_sats == agr.agreed_price_sats
        assert restored.l402_endpoint == agr.l402_endpoint
        assert restored.terms == agr.terms
        assert restored.expires_at == agr.expires_at

    def test_kind_constant(self):
        assert AgentServiceAgreement.KIND == 38402

    def test_no_expiration(self):
        agr = AgentServiceAgreement(agreed_price_sats=10)
        tags = agr.to_nostr_tags()
        exp_tags = [t for t in tags if t[0] == "expiration"]
        assert len(exp_tags) == 0

    def test_status_default(self):
        agr = AgentServiceAgreement()
        assert agr.status == "proposed"
        tags = agr.to_nostr_tags()
        assert ["status", "proposed"] in tags

    def test_status_completed_with_payment_hash(self):
        agr = AgentServiceAgreement(
            request_event_id="r1",
            capability_event_id="c1",
            provider_pubkey="prov",
            requester_pubkey="req",
            agreed_price_sats=100,
            status="completed",
            payment_hash="a" * 64,
        )
        tags = agr.to_nostr_tags()
        assert ["status", "completed"] in tags
        assert ["payment_hash", "a" * 64] in tags

    def test_status_proposed_no_payment_hash(self):
        """payment_hash tag should only appear when status is completed."""
        agr = AgentServiceAgreement(
            agreed_price_sats=50,
            status="proposed",
            payment_hash="b" * 64,
        )
        tags = agr.to_nostr_tags()
        assert ["status", "proposed"] in tags
        ph_tags = [t for t in tags if t[0] == "payment_hash"]
        assert len(ph_tags) == 0

    def test_status_completed_no_payment_hash_value(self):
        """No payment_hash tag when status is completed but hash is None."""
        agr = AgentServiceAgreement(
            agreed_price_sats=50,
            status="completed",
        )
        tags = agr.to_nostr_tags()
        assert ["status", "completed"] in tags
        ph_tags = [t for t in tags if t[0] == "payment_hash"]
        assert len(ph_tags) == 0

    def test_parse_status_and_payment_hash(self):
        event = {
            "id": "agr_completed",
            "pubkey": "prov_pub",
            "created_at": 1700000006,
            "kind": 38402,
            "content": "",
            "tags": [
                ["e", "req1", "", "request"],
                ["e", "cap1", "", "capability"],
                ["p", "prov_pub", "", "provider"],
                ["p", "req_pub", "", "requester"],
                ["price", "200"],
                ["status", "completed"],
                ["payment_hash", "c" * 64],
            ],
        }
        agr = AgentServiceAgreement.from_nostr_event(event)
        assert agr.status == "completed"
        assert agr.payment_hash == "c" * 64

    def test_roundtrip_completed_with_payment_hash(self):
        agr = AgentServiceAgreement(
            request_event_id="req_rt2",
            capability_event_id="cap_rt2",
            provider_pubkey="prov_rt2",
            requester_pubkey="req_pub_rt2",
            agreed_price_sats=150,
            status="completed",
            payment_hash="d" * 64,
        )
        tags = agr.to_nostr_tags()
        event = {
            "id": "rt_agr2",
            "pubkey": "rt_pub2",
            "created_at": 1700000007,
            "kind": 38402,
            "content": "",
            "tags": tags,
        }
        restored = AgentServiceAgreement.from_nostr_event(event)
        assert restored.status == "completed"
        assert restored.payment_hash == "d" * 64

    def test_parse_drops_payment_hash_when_not_completed(self):
        """payment_hash should be dropped when status is not completed (invariant)."""
        event = {
            "id": "inv_agr",
            "pubkey": "inv_pub",
            "created_at": 1700000008,
            "kind": 38402,
            "content": "",
            "tags": [
                ["d", "inv-test"],
                ["status", "active"],
                ["payment_hash", "e" * 64],
                ["p", "provider_pub", "", "provider"],
                ["p", "requester_pub", "", "requester"],
            ],
        }
        agr = AgentServiceAgreement.from_nostr_event(event)
        assert agr.status == "active"
        assert agr.payment_hash is None


# --- AgentAttestation ---


class TestAgentAttestation:
    def _sample_event(self) -> dict:
        return {
            "id": "att123",
            "pubkey": "reviewer_pub",
            "created_at": 1700000010,
            "kind": 38403,
            "content": "Excellent translation service, fast and accurate.",
            "tags": [
                ["d", "att-agr123-1700000010"],
                ["p", "agent_pub", "", "subject"],
                ["e", "agr_event_id", "", "agreement"],
                ["rating", "5"],
                ["L", "nostr.agent.attestation"],
                ["l", "completed", "nostr.agent.attestation"],
                ["l", "commerce.service_completion", "nostr.agent.attestation"],
                ["proof", "abc123hash"],
            ],
            "sig": "sig_att",
        }

    def test_from_nostr_event(self):
        event = self._sample_event()
        att = AgentAttestation.from_nostr_event(event)

        assert att.event_id == "att123"
        assert att.pubkey == "reviewer_pub"
        assert att.created_at == 1700000010
        assert att.attestation_id == "att-agr123-1700000010"
        assert att.subject_pubkey == "agent_pub"
        assert att.agreement_id == "agr_event_id"
        assert att.rating == 5
        assert att.content == "Excellent translation service, fast and accurate."
        assert att.proof == "abc123hash"

    def test_to_nostr_tags(self):
        att = AgentAttestation(
            attestation_id="att-001",
            subject_pubkey="subject_pub",
            agreement_id="agr_001",
            rating=4,
            proof="proof_hash",
        )
        tags = att.to_nostr_tags()

        assert ["d", "att-001"] in tags
        assert ["p", "subject_pub", "", "subject"] in tags
        assert ["e", "agr_001", "", "agreement"] in tags
        assert ["rating", "4"] in tags
        assert ["L", "nostr.agent.attestation"] in tags
        assert ["l", "completed", "nostr.agent.attestation"] in tags
        assert ["l", "commerce.service_completion", "nostr.agent.attestation"] in tags
        assert ["proof", "proof_hash"] in tags

    def test_roundtrip(self):
        att = AgentAttestation(
            attestation_id="att-rt",
            subject_pubkey="sub_rt",
            agreement_id="agr_rt",
            rating=3,
            content="Good service",
            proof="proof_rt",
        )
        tags = att.to_nostr_tags()
        event = {
            "id": "rt_att",
            "pubkey": "rt_reviewer",
            "created_at": 1700000020,
            "kind": 38403,
            "content": att.content,
            "tags": tags,
            "sig": "",
        }
        restored = AgentAttestation.from_nostr_event(event)
        assert restored.attestation_id == att.attestation_id
        assert restored.subject_pubkey == att.subject_pubkey
        assert restored.agreement_id == att.agreement_id
        assert restored.rating == att.rating
        assert restored.proof == att.proof
        assert restored.content == att.content

    def test_kind_constant(self):
        assert AgentAttestation.KIND == 38403

    def test_no_proof(self):
        att = AgentAttestation(
            attestation_id="att-np",
            subject_pubkey="sub_np",
            agreement_id="agr_np",
            rating=2,
        )
        tags = att.to_nostr_tags()
        proof_tags = [t for t in tags if t[0] == "proof"]
        assert len(proof_tags) == 0

    def test_fallback_parsing_without_markers(self):
        """Test that p-tag and e-tag parsing works without markers."""
        event = {
            "id": "att_fallback",
            "pubkey": "reviewer",
            "created_at": 1700000030,
            "kind": 38403,
            "content": "OK service",
            "tags": [
                ["d", "att-fb"],
                ["p", "agent_pub_fb"],
                ["e", "agr_fb"],
                ["rating", "3"],
            ],
            "sig": "",
        }
        att = AgentAttestation.from_nostr_event(event)
        assert att.subject_pubkey == "agent_pub_fb"
        assert att.agreement_id == "agr_fb"
