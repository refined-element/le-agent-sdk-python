"""Agent Capability model — Nostr kind 38400."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AgentPricing:
    """Pricing information for an agent capability."""

    amount: int
    unit: str = "sats"
    model: str = "per-request"

    def to_tag(self) -> list[str]:
        """Convert to a Nostr 'price' tag."""
        return ["price", str(self.amount), self.unit, self.model]

    @classmethod
    def from_tag(cls, tag: list[str]) -> AgentPricing:
        """Parse from a Nostr 'price' tag: ['price', amount, unit, model]."""
        if len(tag) < 2:
            raise ValueError(f"Invalid price tag: {tag}")
        amount = int(tag[1])
        unit = tag[2] if len(tag) > 2 else "sats"
        model = tag[3] if len(tag) > 3 else "per-request"
        return cls(amount=amount, unit=unit, model=model)


@dataclass
class AgentCapability:
    """Agent Capability advertisement (Nostr kind 38400).

    Addressable/replaceable event (NIP-33 style) keyed by `d` tag (service_id).
    """

    service_id: str = ""
    categories: list[str] = field(default_factory=list)
    content: str = ""
    pricing: list[AgentPricing] = field(default_factory=list)
    l402_endpoint: Optional[str] = None
    api_endpoint: Optional[str] = None
    api_method: Optional[str] = None
    schema_url: Optional[str] = None
    hashtags: list[str] = field(default_factory=list)
    negotiable: bool = True
    min_price_sats: Optional[int] = None
    # Set by relay / event parsing
    event_id: str = ""
    pubkey: str = ""
    created_at: int = 0

    KIND: int = 38400

    @classmethod
    def from_nostr_event(cls, event: dict[str, Any]) -> AgentCapability:
        """Parse an AgentCapability from a raw Nostr event dict."""
        tags = event.get("tags", [])
        cap = cls(
            event_id=event.get("id", ""),
            pubkey=event.get("pubkey", ""),
            created_at=event.get("created_at", 0),
            content=event.get("content", ""),
        )

        for tag in tags:
            if not tag:
                continue
            key = tag[0]
            if key == "d" and len(tag) > 1:
                cap.service_id = tag[1]
            elif key == "s" and len(tag) > 1:
                cap.categories.append(tag[1])
            elif key == "price" and len(tag) > 1:
                cap.pricing.append(AgentPricing.from_tag(tag))
            elif key == "l402" and len(tag) > 1:
                cap.l402_endpoint = tag[1]
            elif key == "api_endpoint" and len(tag) > 1:
                cap.api_endpoint = tag[1]
            elif key == "api_method" and len(tag) > 1:
                cap.api_method = tag[1]
            elif key == "schema" and len(tag) > 1:
                cap.schema_url = tag[1]
            elif key == "t" and len(tag) > 1:
                cap.hashtags.append(tag[1])
            elif key == "negotiable" and len(tag) > 1:
                if tag[1] == "false":
                    cap.negotiable = False
                elif tag[1] == "true":
                    cap.negotiable = True
                elif tag[1] == "floor" and len(tag) > 2:
                    cap.negotiable = True
                    cap.min_price_sats = int(tag[2])

        return cap

    def to_nostr_tags(self) -> list[list[str]]:
        """Convert to Nostr event tags."""
        tags: list[list[str]] = []

        if self.service_id:
            tags.append(["d", self.service_id])

        for cat in self.categories:
            tags.append(["s", cat])

        for p in self.pricing:
            tags.append(p.to_tag())

        if self.l402_endpoint:
            tags.append(["l402", self.l402_endpoint])

        if self.api_endpoint:
            tags.append(["api_endpoint", self.api_endpoint])

        if self.api_method:
            tags.append(["api_method", self.api_method])

        if self.schema_url:
            tags.append(["schema", self.schema_url])

        for ht in self.hashtags:
            tags.append(["t", ht])

        if self.min_price_sats is not None:
            tags.append(["negotiable", "floor", str(self.min_price_sats)])
        elif not self.negotiable:
            tags.append(["negotiable", "false"])
        else:
            tags.append(["negotiable", "true"])

        return tags
