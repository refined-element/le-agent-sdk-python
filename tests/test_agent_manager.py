"""Tests for AgentManager — basic flows with mocked relay/HTTP."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from le_agent_sdk.agent.manager import AgentManager
from le_agent_sdk.models.agreement import AgentServiceAgreement
from le_agent_sdk.models.capability import AgentCapability, AgentPricing


class TestAgentManagerInit:
    def test_default_relay(self):
        mgr = AgentManager()
        assert mgr.relay_urls == ["wss://agents.lightningenable.com"]

    def test_custom_relays(self):
        mgr = AgentManager(relay_urls=["wss://relay1.example.com"])
        assert mgr.relay_urls == ["wss://relay1.example.com"]

    def test_no_private_key(self):
        mgr = AgentManager()
        assert mgr.private_key is None

    def test_pubkey_without_private_key_raises(self):
        mgr = AgentManager()
        with pytest.raises(ValueError, match="No private key"):
            _ = mgr.pubkey


class TestAgentManagerDiscover:
    @pytest.mark.asyncio
    async def test_discover_returns_capabilities(self):
        sample_events = [
            {
                "id": "ev1",
                "pubkey": "pub1",
                "created_at": 1700000000,
                "kind": 38400,
                "content": "Service A",
                "tags": [["d", "svc-a"], ["s", "ai"]],
                "sig": "",
            },
            {
                "id": "ev2",
                "pubkey": "pub2",
                "created_at": 1700000001,
                "kind": 38400,
                "content": "Service B",
                "tags": [["d", "svc-b"], ["s", "translation"]],
                "sig": "",
            },
        ]

        mgr = AgentManager()
        with patch.object(mgr, "_query_relays", new_callable=AsyncMock) as mock_query:
            mock_query.return_value = sample_events
            caps = await mgr.discover(categories=["ai"])

        assert len(caps) == 2
        assert caps[0].service_id == "svc-a"
        assert caps[1].service_id == "svc-b"

    @pytest.mark.asyncio
    async def test_discover_empty(self):
        mgr = AgentManager()
        with patch.object(mgr, "_query_relays", new_callable=AsyncMock) as mock_query:
            mock_query.return_value = []
            caps = await mgr.discover()

        assert caps == []

    @pytest.mark.asyncio
    async def test_discover_with_hashtags(self):
        mgr = AgentManager()
        with patch.object(mgr, "_query_relays", new_callable=AsyncMock) as mock_query:
            mock_query.return_value = []
            await mgr.discover(hashtags=["ml", "vision"])

            call_args = mock_query.call_args[0][0]
            assert "#t" in call_args[0]
            assert call_args[0]["#t"] == ["ml", "vision"]


class TestAgentManagerPublish:
    @pytest.mark.asyncio
    async def test_publish_capability(self):
        mgr = AgentManager()
        cap = AgentCapability(
            service_id="test-svc",
            categories=["ai"],
            content="Test service",
            pricing=[AgentPricing(amount=10, unit="sats", model="per-request")],
        )

        with patch.object(
            mgr, "_publish_to_relays", new_callable=AsyncMock
        ) as mock_pub:
            mock_pub.return_value = "event_id_123"
            event_id = await mgr.publish_capability(cap)

        assert event_id == "event_id_123"
        # Verify the event was created with correct kind
        call_args = mock_pub.call_args[0][0]
        assert call_args["kind"] == 38400


class TestAgentManagerRequestService:
    @pytest.mark.asyncio
    async def test_request_service(self):
        mgr = AgentManager()

        with patch.object(
            mgr, "_publish_to_relays", new_callable=AsyncMock
        ) as mock_pub:
            mock_pub.return_value = "req_event_id"
            req = await mgr.request_service(
                capability_event_id="cap_ev",
                provider_pubkey="provider_pub",
                budget_sats=500,
                params={"lang": "es"},
                content="Translate this",
            )

        assert req.event_id == "req_event_id"
        assert req.capability_event_id == "cap_ev"
        assert req.provider_pubkey == "provider_pub"
        assert req.budget_sats == 500
        assert req.params == {"lang": "es"}

        # Verify event was created with correct kind
        call_args = mock_pub.call_args[0][0]
        assert call_args["kind"] == 38401


class TestAgentManagerSettle:
    @pytest.mark.asyncio
    async def test_settle_no_endpoint_raises(self):
        mgr = AgentManager()
        agreement = AgentServiceAgreement(l402_endpoint="")

        with pytest.raises(ValueError, match="no L402 endpoint"):
            await mgr.settle(agreement)

    @pytest.mark.asyncio
    async def test_settle_via_l402_no_endpoint_raises(self):
        mgr = AgentManager()
        cap = AgentCapability(l402_endpoint=None)

        with pytest.raises(ValueError, match="no L402 endpoint"):
            await mgr.settle_via_l402(cap)


class TestAgentManagerQueryRelays:
    @pytest.mark.asyncio
    async def test_deduplicates_events(self):
        same_event = {
            "id": "dup1",
            "pubkey": "pub",
            "created_at": 1,
            "kind": 38400,
            "content": "",
            "tags": [],
        }

        mgr = AgentManager(relay_urls=["wss://r1", "wss://r2"])

        async def fake_query(url, filters, timeout):
            return [same_event]

        with patch.object(mgr, "_query_relay", side_effect=fake_query):
            events = await mgr._query_relays([{}])

        # Should deduplicate
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_handles_relay_failures(self):
        mgr = AgentManager(relay_urls=["wss://r1", "wss://r2"])

        async def fake_query(url, filters, timeout):
            if "r1" in url:
                raise ConnectionError("fail")
            return [{"id": "ev1", "pubkey": "p", "created_at": 1, "kind": 1, "content": "", "tags": []}]

        with patch.object(mgr, "_query_relay", side_effect=fake_query):
            events = await mgr._query_relays([{}])

        assert len(events) == 1
