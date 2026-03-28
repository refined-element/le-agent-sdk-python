"""Agent Attestation model — Nostr kind 38403."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AgentAttestation:
    """Agent Attestation / Review (Nostr kind 38403).

    Published by any agent or human after a completed agreement to build
    on-protocol reputation. References the agreement event and the agent
    being reviewed.
    """

    event_id: str = ""
    pubkey: str = ""           # Who wrote the review
    created_at: int = 0
    attestation_id: str = ""   # d-tag
    subject_pubkey: str = ""   # Agent being reviewed
    agreement_id: str = ""     # Agreement this is for
    rating: int = 0            # 1-5
    content: str = ""          # Review text
    proof: Optional[str] = None  # Payment proof hash (l402 preimage hash)

    KIND: int = 38403

    @classmethod
    def from_nostr_event(cls, event: dict[str, Any]) -> AgentAttestation:
        """Parse an AgentAttestation from a raw Nostr event dict."""
        tags = event.get("tags", [])
        att = cls(
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
                att.attestation_id = tag[1]
            elif key == "p" and len(tag) > 1:
                # Look for subject marker
                marker = tag[3] if len(tag) > 3 else ""
                if marker == "subject":
                    att.subject_pubkey = tag[1]
                elif not att.subject_pubkey:
                    # Fallback: first p-tag without marker is the subject
                    att.subject_pubkey = tag[1]
            elif key == "e" and len(tag) > 1:
                marker = tag[3] if len(tag) > 3 else ""
                if marker == "agreement":
                    att.agreement_id = tag[1]
                elif not att.agreement_id:
                    # Fallback: first e-tag is the agreement
                    att.agreement_id = tag[1]
            elif key == "rating" and len(tag) > 1:
                try:
                    att.rating = int(tag[1])
                except (ValueError, TypeError):
                    att.rating = 0
            elif key == "proof" and len(tag) > 1:
                att.proof = tag[1]

        return att

    def to_nostr_tags(self) -> list[list[str]]:
        """Convert to Nostr event tags."""
        tags: list[list[str]] = []

        if self.attestation_id:
            tags.append(["d", self.attestation_id])

        if self.subject_pubkey:
            tags.append(["p", self.subject_pubkey, "", "subject"])

        if self.agreement_id:
            tags.append(["e", self.agreement_id, "", "agreement"])

        if self.rating > 0:
            tags.append(["rating", str(self.rating)])

        # NIP-32 label namespace
        tags.append(["L", "nostr.agent.attestation"])
        tags.append(["l", "completed", "nostr.agent.attestation"])
        tags.append(["l", "commerce.service_completion", "nostr.agent.attestation"])

        if self.proof:
            tags.append(["proof", self.proof])

        return tags
