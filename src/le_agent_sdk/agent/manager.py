"""Agent Manager — main orchestrator for ASA protocol operations.

Provides a high-level API for discovering capabilities, publishing services,
sending requests, negotiating agreements, and settling via L402.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Optional

from le_agent_sdk.l402.client import L402Client, L402ProducerClient
from le_agent_sdk.models.agreement import AgentServiceAgreement
from le_agent_sdk.models.attestation import AgentAttestation
from le_agent_sdk.models.capability import AgentCapability
from le_agent_sdk.models.request import AgentServiceRequest
from le_agent_sdk.nostr.event import NostrEvent
from le_agent_sdk.nostr.relay import RelayClient
from le_agent_sdk.nostr.tags import TagParser


class AgentManager:
    """Main entry point for agent operations.

    Handles the full lifecycle: discover services, publish capabilities,
    send requests, negotiate agreements, and settle payments via L402.
    """

    def __init__(
        self,
        private_key: Optional[str] = None,
        relay_urls: Optional[list[str]] = None,
        pay_invoice_callback: Optional[Any] = None,
        le_api_key: Optional[str] = None,
        le_api_base_url: str = "https://api.lightningenable.com",
    ) -> None:
        """
        Args:
            private_key: Hex-encoded 32-byte Nostr private key.
                If not provided, events must be signed externally.
            relay_urls: List of Nostr relay WebSocket URLs.
            pay_invoice_callback: Async callable(invoice: str) -> preimage: str.
                Used for L402 auto-payment (consumer/requester side).
            le_api_key: Lightning Enable merchant API key. Required for
                producer/provider operations (create_challenge, verify_payment).
                Requires an Agentic Commerce subscription.
            le_api_base_url: Base URL for the Lightning Enable API.
                Defaults to https://api.lightningenable.com.
        """
        self.private_key = private_key
        self.relay_urls = relay_urls or ["wss://agents.lightningenable.com"]
        self._pay_callback = pay_invoice_callback
        self._le_api_key = le_api_key
        self._le_api_base_url = le_api_base_url
        self._pubkey: Optional[str] = None
        self._producer_client: Optional[L402ProducerClient] = None

    @property
    def pubkey(self) -> str:
        """Derive and cache the public key from the private key."""
        if self._pubkey is None:
            if self.private_key is None:
                raise ValueError("No private key configured; cannot derive pubkey")
            self._pubkey = NostrEvent.pubkey_from_private_key(self.private_key)
        return self._pubkey

    async def _publish_to_relays(self, event: dict[str, Any]) -> str:
        """Publish an event to all configured relays.

        Args:
            event: Signed Nostr event dict.

        Returns:
            The event ID.
        """
        tasks = []
        for url in self.relay_urls:
            tasks.append(self._publish_to_relay(url, event))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Return event ID if at least one relay accepted
        any_accepted = False
        for result in results:
            if result is True:
                any_accepted = True
                break

        if not any_accepted:
            raise RuntimeError(
                f"Event {event['id']} was not accepted by any relay. "
                f"Tried {len(self.relay_urls)} relay(s): {', '.join(self.relay_urls)}"
            )

        return event["id"]

    async def _publish_to_relay(self, url: str, event: dict[str, Any]) -> bool:
        """Publish an event to a single relay."""
        relay = RelayClient()
        try:
            await relay.connect(url)
            return await relay.publish(event)
        except Exception:
            return False
        finally:
            await relay.close()

    async def _query_relays(
        self, filters: list[dict[str, Any]], timeout: float = 5.0
    ) -> list[dict[str, Any]]:
        """Query all configured relays and merge results.

        Deduplicates events by ID.

        Args:
            filters: Nostr filter objects.
            timeout: Timeout per relay in seconds.

        Returns:
            Deduplicated list of event dicts.
        """
        tasks = []
        for url in self.relay_urls:
            tasks.append(self._query_relay(url, filters, timeout))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        seen_ids: set[str] = set()
        events: list[dict[str, Any]] = []
        for result in results:
            if isinstance(result, list):
                for event in result:
                    event_id = event.get("id", "")
                    if event_id and event_id not in seen_ids:
                        seen_ids.add(event_id)
                        events.append(event)

        return events

    async def _query_relay(
        self, url: str, filters: list[dict[str, Any]], timeout: float
    ) -> list[dict[str, Any]]:
        """Query a single relay for events."""
        relay = RelayClient()
        try:
            await relay.connect(url)
            return await relay.collect_events(filters, timeout=timeout)
        except Exception:
            return []
        finally:
            await relay.close()

    async def discover(
        self,
        categories: Optional[list[str]] = None,
        hashtags: Optional[list[str]] = None,
        limit: int = 20,
        timeout: float = 5.0,
    ) -> list[AgentCapability]:
        """Query relays for agent capabilities.

        Args:
            categories: Filter by service categories (s-tags).
            hashtags: Filter by hashtags (t-tags).
            limit: Maximum number of results.
            timeout: Query timeout in seconds.

        Returns:
            List of AgentCapability objects.
        """
        tags: Optional[dict[str, list[str]]] = None
        if categories or hashtags:
            tags = {}
            if categories:
                tags["s"] = categories
            if hashtags:
                tags["t"] = hashtags

        nostr_filter = TagParser.build_filter(
            kinds=[AgentCapability.KIND],
            limit=limit,
            tags=tags,
        )

        raw_events = await self._query_relays([nostr_filter], timeout=timeout)
        return [AgentCapability.from_nostr_event(e) for e in raw_events]

    async def publish_capability(self, capability: AgentCapability) -> str:
        """Publish a capability advertisement to relays.

        Args:
            capability: The capability to publish.

        Returns:
            The Nostr event ID.
        """
        tags = capability.to_nostr_tags()
        event = NostrEvent.create(
            kind=AgentCapability.KIND,
            content=capability.content,
            tags=tags,
            private_key=self.private_key,
        )
        return await self._publish_to_relays(event)

    async def request_service(
        self,
        capability_event_id: str,
        provider_pubkey: str,
        budget_sats: int = 0,
        params: Optional[dict[str, str]] = None,
        content: str = "",
    ) -> AgentServiceRequest:
        """Send a service request to a provider.

        Args:
            capability_event_id: Event ID of the capability being requested.
            provider_pubkey: Provider's Nostr pubkey.
            budget_sats: Maximum budget in sats.
            params: Optional key-value parameters.
            content: Optional request message.

        Returns:
            The published AgentServiceRequest.
        """
        request = AgentServiceRequest(
            capability_event_id=capability_event_id,
            provider_pubkey=provider_pubkey,
            budget_sats=budget_sats,
            params=params or {},
            content=content,
        )

        tags = request.to_nostr_tags()
        event = NostrEvent.create(
            kind=AgentServiceRequest.KIND,
            content=request.content,
            tags=tags,
            private_key=self.private_key,
        )

        event_id = await self._publish_to_relays(event)
        request.event_id = event_id
        request.pubkey = event.get("pubkey", "")
        request.created_at = event.get("created_at", 0)
        return request

    async def listen_requests(
        self,
        timeout: float = 0,
    ) -> AsyncIterator[AgentServiceRequest]:
        """Listen for incoming service requests addressed to this agent.

        Args:
            timeout: Time to listen in seconds. 0 means indefinitely.

        Yields:
            AgentServiceRequest objects as they arrive.
        """
        nostr_filter = TagParser.build_filter(
            kinds=[AgentServiceRequest.KIND],
            tags={"p": [self.pubkey]},
        )

        # Connect to all relays for streaming, with reconnection
        relays: list[RelayClient] = []
        for url in self.relay_urls:
            relay = RelayClient()
            try:
                await relay.connect(url)
                await relay.subscribe([nostr_filter])
                relays.append(relay)
            except Exception:
                await relay.close()

        if not relays:
            raise ConnectionError(
                f"Could not connect to any relay. Tried: {', '.join(self.relay_urls)}"
            )

        seen_ids: set[str] = set()
        max_reconnect_attempts = 5
        backoff_base = 1.0

        try:
            # Listen on the first connected relay, reconnect on failure
            active_relay = relays[0]
            reconnect_attempts = 0

            while True:
                try:
                    async for msg_type, payload in active_relay.listen():
                        if msg_type == "EVENT":
                            _, event_data = payload
                            event_id = event_data.get("id", "")
                            if event_id and event_id not in seen_ids:
                                seen_ids.add(event_id)
                                reconnect_attempts = 0  # Reset on successful message
                                yield AgentServiceRequest.from_nostr_event(event_data)
                except Exception:
                    reconnect_attempts += 1
                    if reconnect_attempts > max_reconnect_attempts:
                        raise ConnectionError(
                            f"Lost connection to relay after {max_reconnect_attempts} reconnect attempts"
                        )
                    wait = backoff_base * (2 ** (reconnect_attempts - 1))
                    await asyncio.sleep(min(wait, 30.0))

                    # Try to reconnect to any relay
                    reconnected = False
                    for url in self.relay_urls:
                        try:
                            new_relay = RelayClient()
                            await new_relay.connect(url)
                            await new_relay.subscribe([nostr_filter])
                            active_relay = new_relay
                            reconnected = True
                            break
                        except Exception:
                            await new_relay.close()

                    if not reconnected:
                        raise ConnectionError(
                            f"Could not reconnect to any relay after attempt {reconnect_attempts}"
                        )
        finally:
            for r in relays:
                await r.close()

    async def publish_agreement(
        self,
        request_event_id: str,
        capability_event_id: str,
        requester_pubkey: str,
        agreed_price_sats: int,
        l402_endpoint: str = "",
        terms: str = "",
        expires_at: Optional[int] = None,
        content: str = "",
    ) -> AgentServiceAgreement:
        """Publish a service agreement.

        Args:
            request_event_id: The service request event ID.
            capability_event_id: The capability event ID.
            requester_pubkey: The requester's pubkey.
            agreed_price_sats: Agreed price in sats.
            l402_endpoint: L402 endpoint URL for settlement.
            terms: Agreement terms text.
            expires_at: Optional expiration timestamp.
            content: Optional agreement message.

        Returns:
            The published AgentServiceAgreement.
        """
        agreement = AgentServiceAgreement(
            request_event_id=request_event_id,
            capability_event_id=capability_event_id,
            provider_pubkey=self.pubkey,
            requester_pubkey=requester_pubkey,
            agreed_price_sats=agreed_price_sats,
            l402_endpoint=l402_endpoint,
            terms=terms,
            expires_at=expires_at,
            content=content,
        )

        tags = agreement.to_nostr_tags()
        event = NostrEvent.create(
            kind=AgentServiceAgreement.KIND,
            content=agreement.content,
            tags=tags,
            private_key=self.private_key,
        )

        event_id = await self._publish_to_relays(event)
        agreement.event_id = event_id
        agreement.pubkey = event.get("pubkey", "")
        agreement.created_at = event.get("created_at", 0)
        return agreement

    async def settle(
        self,
        agreement: AgentServiceAgreement,
        method: str = "GET",
        headers: Optional[dict[str, str]] = None,
        **kwargs: Any,
    ) -> Any:
        """Execute L402 settlement for an agreement.

        Args:
            agreement: The agreement containing the L402 endpoint.
            method: HTTP method for the L402 request.
            headers: Optional additional headers.
            **kwargs: Additional httpx request kwargs.

        Returns:
            The HTTP response from the L402 endpoint.

        Raises:
            ValueError: If agreement has no L402 endpoint.
        """
        if not agreement.l402_endpoint:
            raise ValueError("Agreement has no L402 endpoint configured")

        async with L402Client(pay_invoice_callback=self._pay_callback) as client:
            return await client.access(
                url=agreement.l402_endpoint,
                method=method,
                headers=headers,
                **kwargs,
            )

    async def settle_via_l402(
        self,
        capability: AgentCapability,
        method: str = "GET",
        headers: Optional[dict[str, str]] = None,
        **kwargs: Any,
    ) -> Any:
        """Directly settle via a capability's L402 endpoint (skip negotiation).

        Args:
            capability: The capability with an L402 endpoint.
            method: HTTP method.
            headers: Optional additional headers.
            **kwargs: Additional httpx request kwargs.

        Returns:
            The HTTP response.

        Raises:
            ValueError: If capability has no L402 endpoint.
        """
        if not capability.l402_endpoint:
            raise ValueError("Capability has no L402 endpoint configured")

        async with L402Client(pay_invoice_callback=self._pay_callback) as client:
            return await client.access(
                url=capability.l402_endpoint,
                method=method,
                headers=headers,
                **kwargs,
            )

    # --- Attestation / Reputation methods ---

    async def publish_attestation(
        self,
        subject_pubkey: str,
        agreement_id: str,
        rating: int,
        content: str = "",
        proof: Optional[str] = None,
    ) -> AgentAttestation:
        """Publish an attestation/review for an agent (kind 38403).

        Args:
            subject_pubkey: Pubkey of the agent being reviewed.
            agreement_id: Event ID of the agreement this review is for.
            rating: Rating from 1-5.
            content: Free-text review.
            proof: Optional hash of L402 payment preimage as proof of transaction.

        Returns:
            The published AgentAttestation.

        Raises:
            ValueError: If rating is not between 1 and 5.
        """
        if not 1 <= rating <= 5:
            raise ValueError(f"Rating must be between 1 and 5, got {rating}")

        import time

        attestation_id = f"att-{agreement_id[:16]}-{int(time.time())}"

        attestation = AgentAttestation(
            attestation_id=attestation_id,
            subject_pubkey=subject_pubkey,
            agreement_id=agreement_id,
            rating=rating,
            content=content,
            proof=proof,
        )

        tags = attestation.to_nostr_tags()
        event = NostrEvent.create(
            kind=AgentAttestation.KIND,
            content=attestation.content,
            tags=tags,
            private_key=self.private_key,
        )

        event_id = await self._publish_to_relays(event)
        attestation.event_id = event_id
        attestation.pubkey = event.get("pubkey", "")
        attestation.created_at = event.get("created_at", 0)
        return attestation

    async def get_attestations(
        self,
        pubkey: str,
        limit: int = 20,
        timeout: float = 5.0,
    ) -> list[AgentAttestation]:
        """Query relays for attestations about an agent.

        Args:
            pubkey: The agent's pubkey to get attestations for.
            limit: Maximum number of results.
            timeout: Query timeout in seconds.

        Returns:
            List of AgentAttestation objects.
        """
        nostr_filter = TagParser.build_filter(
            kinds=[AgentAttestation.KIND],
            limit=limit,
            tags={"p": [pubkey]},
        )

        raw_events = await self._query_relays([nostr_filter], timeout=timeout)
        return [AgentAttestation.from_nostr_event(e) for e in raw_events]

    async def get_reputation_score(
        self,
        pubkey: str,
        limit: int = 50,
        timeout: float = 5.0,
    ) -> Optional[float]:
        """Compute average rating from attestations about an agent.

        Args:
            pubkey: The agent's pubkey.
            limit: Maximum attestations to consider.
            timeout: Query timeout in seconds.

        Returns:
            Average rating (1.0-5.0), or None if no attestations found.
        """
        attestations = await self.get_attestations(pubkey, limit=limit, timeout=timeout)
        rated = [a for a in attestations if 1 <= a.rating <= 5]
        if not rated:
            return None
        return sum(a.rating for a in rated) / len(rated)

    # --- Producer / Provider API methods ---

    def _get_producer_client(self) -> L402ProducerClient:
        """Get or create the L402 Producer API client.

        Raises:
            ValueError: If no le_api_key is configured.
        """
        if not self._le_api_key:
            raise ValueError(
                "Lightning Enable API key (le_api_key) is required for producer operations. "
                "Pass le_api_key to AgentManager or set LIGHTNING_ENABLE_API_KEY env var."
            )
        if self._producer_client is None:
            self._producer_client = L402ProducerClient(
                le_api_key=self._le_api_key,
                le_api_base_url=self._le_api_base_url,
            )
        return self._producer_client

    async def create_challenge(
        self,
        agreement: AgentServiceAgreement,
        price_sats: Optional[int] = None,
        description: Optional[str] = None,
    ) -> AgentServiceAgreement:
        """Create an L402 challenge for the requester to pay (provider side).

        Calls the Lightning Enable Producer API to generate a Lightning invoice
        and macaroon at the negotiated price. The agreement is updated in-place
        with the invoice, macaroon, and payment_hash fields.

        After creating the challenge, the provider shares the invoice with the
        requester (e.g., via Nostr DM or embedded in the agreement event).

        Args:
            agreement: The agreement to create a challenge for.
            price_sats: Override price in sats. Defaults to agreement.agreed_price_sats.
            description: Optional invoice description.

        Returns:
            The updated agreement with invoice, macaroon, and payment_hash set.

        Raises:
            ValueError: If no le_api_key is configured.
            RuntimeError: If the challenge creation fails.
        """
        producer = self._get_producer_client()
        effective_price = price_sats if price_sats is not None else agreement.agreed_price_sats

        resource = (
            f"asa:{agreement.event_id or agreement.capability_event_id}"
            f":{agreement.requester_pubkey[:16]}"
        )
        desc = description or f"ASA settlement: {agreement.terms or agreement.capability_event_id}"

        result = await producer.create_challenge(
            resource=resource,
            price_sats=effective_price,
            description=desc,
        )

        if not result.success:
            raise RuntimeError(f"Failed to create L402 challenge: {result.error}")

        # Update the agreement with challenge details
        agreement.invoice = result.invoice
        agreement.macaroon = result.macaroon
        agreement.payment_hash = result.payment_hash
        agreement.settlement_mode = "producer"

        return agreement

    async def verify_payment(
        self,
        macaroon: str,
        preimage: str,
    ) -> bool:
        """Verify an L402 token to confirm payment (provider side).

        The provider calls this after receiving an L402 token (macaroon:preimage)
        from the requester. Returns True if the payment is valid and the provider
        should deliver the service.

        Args:
            macaroon: Base64-encoded macaroon from the L402 token.
            preimage: Hex-encoded preimage (proof of payment).

        Returns:
            True if the payment is valid, False otherwise.

        Raises:
            ValueError: If no le_api_key is configured.
            RuntimeError: If the verification API call fails.
        """
        producer = self._get_producer_client()

        result = await producer.verify_payment(
            macaroon=macaroon,
            preimage=preimage,
        )

        if not result.success:
            raise RuntimeError(f"Failed to verify L402 payment: {result.error}")

        return result.valid
