"""Lightning Enable Agent SDK — discover, request, and settle Agent Service Agreements."""

from le_agent_sdk.models.capability import AgentCapability, AgentPricing
from le_agent_sdk.models.request import AgentServiceRequest
from le_agent_sdk.models.agreement import AgentServiceAgreement
from le_agent_sdk.models.attestation import AgentAttestation
from le_agent_sdk.nostr.event import (
    CryptoBackendUnavailableError,
    NostrEvent,
    Secp256k1UnavailableError,
)
from le_agent_sdk.nostr.relay import RelayClient
from le_agent_sdk.nostr.tags import TagParser
from le_agent_sdk.l402.client import L402Client, L402ProducerClient
from le_agent_sdk.agent.manager import AgentManager

__all__ = [
    "AgentCapability",
    "AgentPricing",
    "AgentServiceRequest",
    "AgentServiceAgreement",
    "AgentAttestation",
    "NostrEvent",
    "CryptoBackendUnavailableError",
    # Pre-0.4.0 alias of CryptoBackendUnavailableError, kept for callers
    # catching it by name.
    "Secp256k1UnavailableError",
    "RelayClient",
    "TagParser",
    "L402Client",
    "L402ProducerClient",
    "AgentManager",
]

__version__ = "0.4.1"
