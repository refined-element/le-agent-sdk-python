"""Agent Service Agreement model — Nostr kind 38402."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AgentServiceAgreement:
    """Agent Service Agreement (Nostr kind 38402).

    Bilateral contract between requester and provider.
    """

    request_event_id: str = ""
    capability_event_id: str = ""
    provider_pubkey: str = ""
    requester_pubkey: str = ""
    agreed_price_sats: int = 0
    l402_endpoint: str = ""
    terms: str = ""
    content: str = ""
    expires_at: Optional[int] = None
    # L402 Producer API challenge fields (set after create_challenge)
    invoice: Optional[str] = None
    macaroon: Optional[str] = None
    # Dual-purpose field:
    #   1. L402 Producer API: populated by create_challenge() with the Lightning payment hash.
    #   2. NIP-A5 event tag: emitted as a ["payment_hash", ...] tag on completed
    #      agreements (kind 38402, status="completed") to provide proof of Lightning settlement.
    payment_hash: Optional[str] = None
    # Settlement mode: "proxy" (static L402 proxy) or "producer" (dynamic via Producer API)
    settlement_mode: str = "proxy"
    # Agreement lifecycle status: proposed, active, completed, disputed, expired
    status: str = "proposed"
    # Set by relay / event parsing
    event_id: str = ""
    pubkey: str = ""
    created_at: int = 0

    KIND: int = 38402

    @classmethod
    def from_nostr_event(cls, event: dict[str, Any]) -> AgentServiceAgreement:
        """Parse an AgentServiceAgreement from a raw Nostr event dict."""
        tags = event.get("tags", [])
        agr = cls(
            event_id=event.get("id", ""),
            pubkey=event.get("pubkey", ""),
            created_at=event.get("created_at", 0),
            content=event.get("content", ""),
        )

        # Collect e-tags and p-tags for marker-based and fallback parsing
        e_tags: list[list[str]] = []
        p_tags: list[list[str]] = []

        for tag in tags:
            if not tag:
                continue
            key = tag[0]
            if key == "e" and len(tag) > 1:
                e_tags.append(tag)
            elif key == "p" and len(tag) > 1:
                p_tags.append(tag)
            elif key == "price" and len(tag) > 1:
                try:
                    agr.agreed_price_sats = int(tag[1])
                except (ValueError, TypeError):
                    agr.agreed_price_sats = 0
            elif key == "l402" and len(tag) > 1:
                agr.l402_endpoint = tag[1]
            elif key == "terms" and len(tag) > 1:
                agr.terms = tag[1]
            elif key == "expiration" and len(tag) > 1:
                try:
                    agr.expires_at = int(tag[1])
                except (ValueError, TypeError):
                    agr.expires_at = None
            elif key == "status" and len(tag) > 1:
                agr.status = tag[1]
            elif key == "payment_hash" and len(tag) > 1:
                agr.payment_hash = tag[1]

        # Parse e-tags: prefer marker hints (e.g. ["e", "<id>", "", "request"])
        # Fall back to order-based parsing if markers not present
        request_found = False
        capability_found = False
        for etag in e_tags:
            marker = etag[3] if len(etag) > 3 else ""
            if marker == "request":
                agr.request_event_id = etag[1]
                request_found = True
            elif marker == "capability":
                agr.capability_event_id = etag[1]
                capability_found = True

        if not request_found and not capability_found:
            # Fallback: first e-tag is request, second is capability
            if len(e_tags) > 0:
                agr.request_event_id = e_tags[0][1]
            if len(e_tags) > 1:
                agr.capability_event_id = e_tags[1][1]

        # Parse p-tags: prefer marker hints (e.g. ["p", "<pk>", "", "provider"])
        # Fall back to order-based parsing if markers not present
        provider_found = False
        requester_found = False
        for ptag in p_tags:
            marker = ptag[3] if len(ptag) > 3 else ""
            if marker == "provider":
                agr.provider_pubkey = ptag[1]
                provider_found = True
            elif marker == "requester":
                agr.requester_pubkey = ptag[1]
                requester_found = True

        if not provider_found and not requester_found:
            # Fallback: first p-tag is provider, second is requester
            if len(p_tags) > 0:
                agr.provider_pubkey = p_tags[0][1]
            if len(p_tags) > 1:
                agr.requester_pubkey = p_tags[1][1]

        return agr

    def to_nostr_tags(self) -> list[list[str]]:
        """Convert to Nostr event tags."""
        tags: list[list[str]] = []

        if self.request_event_id:
            tags.append(["e", self.request_event_id, "", "request"])

        if self.capability_event_id:
            tags.append(["e", self.capability_event_id, "", "capability"])

        if self.provider_pubkey:
            tags.append(["p", self.provider_pubkey, "", "provider"])

        if self.requester_pubkey:
            tags.append(["p", self.requester_pubkey, "", "requester"])

        if self.agreed_price_sats > 0:
            tags.append(["price", str(self.agreed_price_sats)])

        if self.l402_endpoint:
            tags.append(["l402", self.l402_endpoint])

        if self.terms:
            tags.append(["terms", self.terms])

        if self.expires_at is not None:
            tags.append(["expiration", str(self.expires_at)])

        if self.status:
            tags.append(["status", self.status])

        if self.status == "completed" and self.payment_hash:
            tags.append(["payment_hash", self.payment_hash])

        return tags
