"""Agent Service Request model — Nostr kind 38401."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AgentServiceRequest:
    """Agent Service Request (Nostr kind 38401).

    Sent by a requester to indicate interest in a capability.
    """

    capability_event_id: str = ""
    provider_pubkey: str = ""
    budget_sats: int = 0
    content: str = ""
    params: dict[str, str] = field(default_factory=dict)
    # Set by relay / event parsing
    event_id: str = ""
    pubkey: str = ""
    created_at: int = 0

    KIND: int = 38401

    @classmethod
    def from_nostr_event(cls, event: dict[str, Any]) -> AgentServiceRequest:
        """Parse an AgentServiceRequest from a raw Nostr event dict."""
        tags = event.get("tags", [])
        req = cls(
            event_id=event.get("id", ""),
            pubkey=event.get("pubkey", ""),
            created_at=event.get("created_at", 0),
            content=event.get("content", ""),
        )

        for tag in tags:
            if not tag:
                continue
            key = tag[0]
            if key == "e" and len(tag) > 1:
                req.capability_event_id = tag[1]
            elif key == "p" and len(tag) > 1:
                req.provider_pubkey = tag[1]
            elif key == "budget" and len(tag) > 1:
                try:
                    req.budget_sats = int(tag[1])
                except (ValueError, TypeError):
                    req.budget_sats = 0
            elif key == "param" and len(tag) > 2:
                req.params[tag[1]] = tag[2]

        return req

    def to_nostr_tags(self) -> list[list[str]]:
        """Convert to Nostr event tags."""
        tags: list[list[str]] = []

        if self.capability_event_id:
            tags.append(["e", self.capability_event_id])

        if self.provider_pubkey:
            tags.append(["p", self.provider_pubkey])

        if self.budget_sats > 0:
            tags.append(["budget", str(self.budget_sats)])

        for k, v in self.params.items():
            tags.append(["param", k, v])

        return tags
