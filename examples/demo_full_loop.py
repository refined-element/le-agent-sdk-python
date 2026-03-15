"""
NostrWolfe E2E Demo: Agent Service Agreement Full Loop

Demonstrates the complete ASA protocol lifecycle:
1. Provider publishes capability (kind 38400)
2. Requester discovers capability
3. Requester sends service request (kind 38401)
4. Provider creates agreement (kind 38402)
5. Settlement via L402

Usage:
    python demo_full_loop.py                                    # Live mode (needs relay)
    python demo_full_loop.py --mock                             # Mock mode (no external deps)
    python demo_full_loop.py --relay-url ws://localhost:8765     # Use mock relay
    python demo_full_loop.py --relay-url wss://relay.damus.io   # Use public relay

For live mode with the local mock relay, first start it:
    python demo_mock_relay.py &
    python demo_full_loop.py --relay-url ws://localhost:8765
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

# Add parent directory to path so we can import the SDK
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from le_agent_sdk.models.capability import AgentCapability, AgentPricing
from le_agent_sdk.models.request import AgentServiceRequest
from le_agent_sdk.models.agreement import AgentServiceAgreement
from le_agent_sdk.nostr.event import NostrEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def timestamp() -> str:
    """Return a formatted timestamp for log output."""
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]


def banner(step: int, title: str) -> None:
    """Print a visible step banner."""
    print(f"\n{'='*70}")
    print(f"  STEP {step}: {title}")
    print(f"  [{timestamp()}]")
    print(f"{'='*70}\n")


def generate_throwaway_privkey() -> str:
    """Generate a random 32-byte hex private key for demo purposes."""
    return os.urandom(32).hex()


def mock_pubkey_from_privkey(privkey_hex: str) -> str:
    """Derive a deterministic mock 'pubkey' from a privkey using SHA-256.

    This is NOT a real Nostr pubkey derivation (which requires secp256k1).
    It is used only in --mock mode to produce consistent, deterministic IDs.
    """
    return hashlib.sha256(bytes.fromhex(privkey_hex)).hexdigest()


def mock_event_id(event: dict[str, Any]) -> str:
    """Compute a deterministic event ID from event fields (SHA-256)."""
    commitment = json.dumps(
        [0, event["pubkey"], event["created_at"], event["kind"], event["tags"], event["content"]],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(commitment.encode()).hexdigest()


def mock_create_event(
    kind: int,
    content: str,
    tags: list[list[str]],
    pubkey: str,
    created_at: Optional[int] = None,
) -> dict[str, Any]:
    """Create a mock Nostr event (no real signing)."""
    if created_at is None:
        created_at = int(time.time())

    event: dict[str, Any] = {
        "pubkey": pubkey,
        "created_at": created_at,
        "kind": kind,
        "tags": tags,
        "content": content,
    }
    event["id"] = mock_event_id(event)
    event["sig"] = "mock_signature_" + event["id"][:32]
    return event


# ---------------------------------------------------------------------------
# Mock mode: simulates the entire flow without any network or crypto deps
# ---------------------------------------------------------------------------

async def run_mock_demo() -> None:
    """Run the full ASA loop with mock data -- no relay, no secp256k1 needed."""

    print("\n" + "#" * 70)
    print("#  NOSTRWOLFE E2E DEMO -- MOCK MODE")
    print("#  No external dependencies required")
    print("#" + "#" * 69 + "\n")

    # Generate throwaway keys
    provider_privkey = generate_throwaway_privkey()
    requester_privkey = generate_throwaway_privkey()
    provider_pubkey = mock_pubkey_from_privkey(provider_privkey)
    requester_pubkey = mock_pubkey_from_privkey(requester_privkey)

    print(f"Provider pubkey:  {provider_pubkey[:16]}...")
    print(f"Requester pubkey: {requester_pubkey[:16]}...")

    # -----------------------------------------------------------------------
    # Step 1: Provider publishes a capability (kind 38400)
    # -----------------------------------------------------------------------
    banner(1, "Provider publishes capability (kind 38400)")

    capability = AgentCapability(
        service_id="translate-v1",
        categories=["ai", "translation"],
        content="AI-powered translation service. Supports 50+ languages. "
                "Send text with source/target language params.",
        pricing=[
            AgentPricing(amount=10, unit="sats", model="per-request"),
        ],
        l402_endpoint="https://api.lightningenable.com/l402/proxy/translate-demo",
        api_endpoint="https://api.example.com/translate",
        api_method="POST",
        schema_url="https://api.example.com/schema/translate.json",
        hashtags=["translation", "ai", "multilingual"],
    )

    cap_tags = capability.to_nostr_tags()
    cap_event = mock_create_event(
        kind=AgentCapability.KIND,
        content=capability.content,
        tags=cap_tags,
        pubkey=provider_pubkey,
    )

    capability.event_id = cap_event["id"]
    capability.pubkey = provider_pubkey
    capability.created_at = cap_event["created_at"]

    print(f"  Event ID:    {cap_event['id'][:32]}...")
    print(f"  Kind:        {cap_event['kind']}")
    print(f"  Service ID:  {capability.service_id}")
    print(f"  Categories:  {capability.categories}")
    print(f"  Pricing:     {capability.pricing[0].amount} {capability.pricing[0].unit}/{capability.pricing[0].model}")
    print(f"  L402:        {capability.l402_endpoint}")
    print(f"  Tags count:  {len(cap_tags)}")
    print(f"\n  -> Capability published to mock relay")

    # -----------------------------------------------------------------------
    # Step 2: Requester discovers the capability
    # -----------------------------------------------------------------------
    banner(2, "Requester discovers capability")

    # Simulate discovery by matching categories
    discovered = [capability]  # In mock mode, we just return what was published

    print(f"  Query:       kinds=[{AgentCapability.KIND}], #s=['translation'], #t=['ai']")
    print(f"  Found:       {len(discovered)} service(s)\n")

    for cap in discovered:
        print(f"  [{cap.service_id}] {cap.content[:60]}...")
        print(f"    Provider:  {cap.pubkey[:16]}...")
        if cap.pricing:
            print(f"    Price:     {cap.pricing[0].amount} {cap.pricing[0].unit}/{cap.pricing[0].model}")
        if cap.l402_endpoint:
            print(f"    L402:      {cap.l402_endpoint}")

    chosen = discovered[0]
    print(f"\n  -> Selected: {chosen.service_id}")

    # -----------------------------------------------------------------------
    # Step 3: Requester sends a service request (kind 38401)
    # -----------------------------------------------------------------------
    banner(3, "Requester sends service request (kind 38401)")

    request = AgentServiceRequest(
        capability_event_id=chosen.event_id,
        provider_pubkey=chosen.pubkey,
        budget_sats=100,
        params={"source_lang": "en", "target_lang": "es"},
        content="Please translate: Hello, how are you today?",
    )

    req_tags = request.to_nostr_tags()
    req_event = mock_create_event(
        kind=AgentServiceRequest.KIND,
        content=request.content,
        tags=req_tags,
        pubkey=requester_pubkey,
    )

    request.event_id = req_event["id"]
    request.pubkey = requester_pubkey
    request.created_at = req_event["created_at"]

    print(f"  Event ID:    {req_event['id'][:32]}...")
    print(f"  Kind:        {req_event['kind']}")
    print(f"  Capability:  {request.capability_event_id[:32]}...")
    print(f"  Provider:    {request.provider_pubkey[:16]}...")
    print(f"  Budget:      {request.budget_sats} sats")
    print(f"  Params:      {request.params}")
    print(f"  Content:     {request.content}")
    print(f"\n  -> Service request published to mock relay")

    # -----------------------------------------------------------------------
    # Step 4: Provider creates an agreement (kind 38402)
    # -----------------------------------------------------------------------
    banner(4, "Provider creates agreement (kind 38402)")

    agreed_price = min(request.budget_sats, chosen.pricing[0].amount)

    agreement = AgentServiceAgreement(
        request_event_id=request.event_id,
        capability_event_id=chosen.event_id,
        provider_pubkey=provider_pubkey,
        requester_pubkey=requester_pubkey,
        agreed_price_sats=agreed_price,
        l402_endpoint=chosen.l402_endpoint or "",
        terms="Max 10 requests per minute. Results returned as JSON.",
        content="Agreement for translation service.",
        expires_at=int(time.time()) + 3600,
    )

    agr_tags = agreement.to_nostr_tags()
    agr_event = mock_create_event(
        kind=AgentServiceAgreement.KIND,
        content=agreement.content,
        tags=agr_tags,
        pubkey=provider_pubkey,
    )

    agreement.event_id = agr_event["id"]
    agreement.pubkey = provider_pubkey
    agreement.created_at = agr_event["created_at"]

    print(f"  Event ID:    {agr_event['id'][:32]}...")
    print(f"  Kind:        {agr_event['kind']}")
    print(f"  Request:     {agreement.request_event_id[:32]}...")
    print(f"  Capability:  {agreement.capability_event_id[:32]}...")
    print(f"  Provider:    {agreement.provider_pubkey[:16]}...")
    print(f"  Requester:   {agreement.requester_pubkey[:16]}...")
    print(f"  Price:       {agreement.agreed_price_sats} sats")
    print(f"  L402:        {agreement.l402_endpoint}")
    print(f"  Terms:       {agreement.terms}")
    print(f"  Expires:     {agreement.expires_at}")
    print(f"\n  -> Agreement published to mock relay")

    # -----------------------------------------------------------------------
    # Step 5A: Settlement via L402 Proxy (consumer/requester flow)
    # -----------------------------------------------------------------------
    banner(5, "Settlement via L402 — Proxy mode (consumer pays static endpoint)")

    print(f"  Endpoint:    {agreement.l402_endpoint}")
    print(f"  Amount:      {agreement.agreed_price_sats} sats")
    print(f"  Mode:        proxy (static L402 endpoint)")
    print()

    # Simulate the L402 challenge-response flow
    print("  1. Requester sends GET to L402 endpoint")
    print("     -> Response: HTTP 402 Payment Required")
    print('     -> WWW-Authenticate: L402 macaroon="AgELbGlnaH...", invoice="lnbc100n1p..."')
    print()
    print("  2. Requester pays Lightning invoice")
    mock_preimage = hashlib.sha256(b"mock_payment").hexdigest()
    print(f"     -> Preimage: {mock_preimage[:32]}...")
    print()
    print("  3. Requester retries with L402 token")
    print(f'     -> Authorization: L402 AgELbGlnaH...:{mock_preimage[:32]}...')
    print("     -> Response: HTTP 200 OK")
    print()
    print('  4. Result: {"translation": "Hola, como estas hoy?", "confidence": 0.97}')

    # -----------------------------------------------------------------------
    # Step 5B: Settlement via L402 Producer API (dynamic pricing)
    # -----------------------------------------------------------------------
    banner(6, "Settlement via L402 — Producer mode (dynamic pricing)")

    print("  In Producer mode, the provider creates L402 challenges dynamically")
    print("  at the negotiated price, instead of using a static proxy endpoint.")
    print()

    # Simulate the producer flow
    mock_invoice = "lnbc100n1pMOCKINVOICE..."
    mock_macaroon = "AgELbGlnaHRuaW5nLWVuYWJsZS5jb20..."
    mock_payment_hash = hashlib.sha256(b"mock_payment_hash").hexdigest()

    print(f"  1. Provider calls create_challenge(agreement, price_sats={agreed_price})")
    print(f"     -> POST /api/l402/challenges")
    print(f"     -> Invoice:      {mock_invoice[:40]}...")
    print(f"     -> Macaroon:     {mock_macaroon[:40]}...")
    print(f"     -> Payment Hash: {mock_payment_hash[:32]}...")
    print()

    # Update agreement with challenge details (simulated)
    agreement.invoice = mock_invoice
    agreement.macaroon = mock_macaroon
    agreement.payment_hash = mock_payment_hash
    agreement.settlement_mode = "producer"

    print("  2. Provider shares invoice with requester (via Nostr DM)")
    print(f"     -> DM to {requester_pubkey[:16]}...: 'Pay this invoice: {mock_invoice[:30]}...'")
    print()

    print("  3. Requester pays the Lightning invoice")
    mock_preimage_2 = hashlib.sha256(b"mock_payment_2").hexdigest()
    print(f"     -> Preimage: {mock_preimage_2[:32]}...")
    print()

    print("  4. Requester sends L402 token back to provider")
    print(f"     -> L402 token: {mock_macaroon[:20]}...:{mock_preimage_2[:20]}...")
    print()

    print("  5. Provider calls verify_payment(macaroon, preimage)")
    print(f"     -> POST /api/l402/challenges/verify")
    print(f"     -> Result: valid=True")
    print()

    print('  6. Provider delivers service')
    print('     -> {"translation": "Hola, como estas hoy?", "confidence": 0.97}')

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print(f"\n{'='*70}")
    print(f"  DEMO COMPLETE")
    print(f"  [{timestamp()}]")
    print(f"{'='*70}")
    print()
    print("  Full ASA loop executed successfully (mock mode):")
    print(f"    Step 1:  Capability published  -> kind {AgentCapability.KIND}  (event {cap_event['id'][:12]}...)")
    print(f"    Step 2:  Capability discovered  -> {chosen.service_id}")
    print(f"    Step 3:  Request sent           -> kind {AgentServiceRequest.KIND} (event {req_event['id'][:12]}...)")
    print(f"    Step 4:  Agreement created      -> kind {AgentServiceAgreement.KIND} (event {agr_event['id'][:12]}...)")
    print(f"    Step 5A: Proxy settlement       -> {agreement.agreed_price_sats} sats (static L402 endpoint)")
    print(f"    Step 5B: Producer settlement    -> {agreement.agreed_price_sats} sats (dynamic via Producer API)")
    print()
    print("  Settlement modes:")
    print("    Proxy:    Provider registers static L402 proxy -> requester pays endpoint directly")
    print("    Producer: Provider creates L402 challenges dynamically at negotiated price")
    print("              -> shares invoice -> requester pays -> provider verifies -> delivers service")
    print()


# ---------------------------------------------------------------------------
# Live mode: uses a real Nostr relay and actual signing
# ---------------------------------------------------------------------------

async def run_live_demo(relay_url: str) -> None:
    """Run the full ASA loop against a real Nostr relay."""

    print("\n" + "#" * 70)
    print("#  NOSTRWOLFE E2E DEMO -- LIVE MODE")
    print(f"#  Relay: {relay_url}")
    print("#" + "#" * 69 + "\n")

    # Check for secp256k1
    try:
        from le_agent_sdk.nostr.event import _HAS_SECP256K1
        if not _HAS_SECP256K1:
            print("WARNING: secp256k1 not installed. Events will be unsigned.")
            print("Install with: pip install secp256k1\n")
    except ImportError:
        pass

    from le_agent_sdk import AgentManager

    # Generate throwaway keys for demo
    provider_privkey = generate_throwaway_privkey()
    requester_privkey = generate_throwaway_privkey()

    provider = AgentManager(
        private_key=provider_privkey,
        relay_urls=[relay_url],
    )
    requester = AgentManager(
        private_key=requester_privkey,
        relay_urls=[relay_url],
    )

    print(f"Provider pubkey:  {provider.pubkey[:16]}...")
    print(f"Requester pubkey: {requester.pubkey[:16]}...")

    # -----------------------------------------------------------------------
    # Step 1: Provider publishes a capability
    # -----------------------------------------------------------------------
    banner(1, "Provider publishes capability (kind 38400)")

    capability = AgentCapability(
        service_id="translate-v1",
        categories=["ai", "translation"],
        content="AI-powered translation service. Supports 50+ languages. "
                "Send text with source/target language params.",
        pricing=[
            AgentPricing(amount=10, unit="sats", model="per-request"),
        ],
        l402_endpoint="https://api.lightningenable.com/l402/proxy/translate-demo",
        api_endpoint="https://api.example.com/translate",
        api_method="POST",
        schema_url="https://api.example.com/schema/translate.json",
        hashtags=["translation", "ai", "multilingual"],
    )

    try:
        cap_event_id = await provider.publish_capability(capability)
        capability.event_id = cap_event_id
        capability.pubkey = provider.pubkey
        print(f"  Event ID:    {cap_event_id[:32]}...")
        print(f"  Kind:        {AgentCapability.KIND}")
        print(f"  Service ID:  {capability.service_id}")
        print(f"  Categories:  {capability.categories}")
        print(f"  Pricing:     {capability.pricing[0].amount} {capability.pricing[0].unit}/{capability.pricing[0].model}")
        print(f"  L402:        {capability.l402_endpoint}")
        print(f"\n  -> Capability published to {relay_url}")
    except Exception as e:
        print(f"  ERROR: Failed to publish capability: {e}")
        print(f"  Is the relay running at {relay_url}?")
        return

    # Brief pause to let the relay process
    await asyncio.sleep(0.5)

    # -----------------------------------------------------------------------
    # Step 2: Requester discovers the capability
    # -----------------------------------------------------------------------
    banner(2, "Requester discovers capability")

    try:
        capabilities = await requester.discover(
            categories=["translation"],
            hashtags=["ai"],
            limit=10,
            timeout=5.0,
        )
        print(f"  Query:       kinds=[{AgentCapability.KIND}], #s=['translation'], #t=['ai']")
        print(f"  Found:       {len(capabilities)} service(s)\n")

        for cap in capabilities:
            print(f"  [{cap.service_id}] {cap.content[:60]}...")
            if cap.pricing:
                print(f"    Price:     {cap.pricing[0].amount} {cap.pricing[0].unit}/{cap.pricing[0].model}")
            if cap.l402_endpoint:
                print(f"    L402:      {cap.l402_endpoint}")

        if not capabilities:
            print("  No services found on the relay. The relay may have rejected unsigned events.")
            print("  Falling back to the capability we just published...")
            capabilities = [capability]

        chosen = capabilities[0]
        print(f"\n  -> Selected: {chosen.service_id}")
    except Exception as e:
        print(f"  ERROR: Discovery failed: {e}")
        print("  Falling back to the capability we just published...")
        chosen = capability

    # -----------------------------------------------------------------------
    # Step 3: Requester sends a service request
    # -----------------------------------------------------------------------
    banner(3, "Requester sends service request (kind 38401)")

    try:
        request = await requester.request_service(
            capability_event_id=chosen.event_id,
            provider_pubkey=chosen.pubkey,
            budget_sats=100,
            params={"source_lang": "en", "target_lang": "es"},
            content="Please translate: Hello, how are you today?",
        )
        print(f"  Event ID:    {request.event_id[:32]}...")
        print(f"  Kind:        {AgentServiceRequest.KIND}")
        print(f"  Capability:  {request.capability_event_id[:32]}...")
        print(f"  Provider:    {request.provider_pubkey[:16]}...")
        print(f"  Budget:      {request.budget_sats} sats")
        print(f"  Params:      {request.params}")
        print(f"  Content:     {request.content}")
        print(f"\n  -> Service request published to {relay_url}")
    except Exception as e:
        print(f"  ERROR: Failed to send request: {e}")
        return

    # -----------------------------------------------------------------------
    # Step 4: Provider creates an agreement
    # -----------------------------------------------------------------------
    banner(4, "Provider creates agreement (kind 38402)")

    try:
        agreed_price = min(request.budget_sats, chosen.pricing[0].amount if chosen.pricing else 10)

        agreement = await provider.publish_agreement(
            request_event_id=request.event_id,
            capability_event_id=chosen.event_id,
            requester_pubkey=requester.pubkey,
            agreed_price_sats=agreed_price,
            l402_endpoint=chosen.l402_endpoint or "",
            terms="Max 10 requests per minute. Results returned as JSON.",
            expires_at=int(time.time()) + 3600,
            content="Agreement for translation service.",
        )
        print(f"  Event ID:    {agreement.event_id[:32]}...")
        print(f"  Kind:        {AgentServiceAgreement.KIND}")
        print(f"  Request:     {agreement.request_event_id[:32]}...")
        print(f"  Capability:  {agreement.capability_event_id[:32]}...")
        print(f"  Provider:    {agreement.provider_pubkey[:16]}...")
        print(f"  Requester:   {agreement.requester_pubkey[:16]}...")
        print(f"  Price:       {agreement.agreed_price_sats} sats")
        print(f"  L402:        {agreement.l402_endpoint}")
        print(f"  Terms:       {agreement.terms}")
        print(f"\n  -> Agreement published to {relay_url}")
    except Exception as e:
        print(f"  ERROR: Failed to publish agreement: {e}")
        return

    # -----------------------------------------------------------------------
    # Step 5: Settlement via L402 (simulated)
    # -----------------------------------------------------------------------
    banner(5, "Settlement via L402 (simulated)")

    print(f"  Endpoint:    {agreement.l402_endpoint}")
    print(f"  Amount:      {agreement.agreed_price_sats} sats")
    print()
    print("  Two settlement modes are supported:")
    print()
    print("  A) PROXY mode (consumer pays static endpoint):")
    print("     Configure AgentManager with: pay_invoice_callback=my_wallet.pay_invoice")
    print("     1. GET {endpoint} -> HTTP 402 + WWW-Authenticate header")
    print("     2. Pay Lightning invoice from challenge")
    print("     3. Retry with Authorization: L402 {macaroon}:{preimage}")
    print("     4. Receive HTTP 200 with service result")
    print()
    print("  B) PRODUCER mode (provider creates challenge at negotiated price):")
    print("     Configure AgentManager with: le_api_key='your-api-key'")
    print("     1. Provider: create_challenge(agreement, price_sats=N)")
    print("     2. Provider shares invoice with requester (via Nostr DM)")
    print("     3. Requester pays invoice, gets preimage")
    print("     4. Requester sends L402 token (macaroon:preimage) to provider")
    print("     5. Provider: verify_payment(macaroon, preimage)")
    print("     6. Provider delivers service")

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print(f"\n{'='*70}")
    print(f"  DEMO COMPLETE")
    print(f"  [{timestamp()}]")
    print(f"{'='*70}")
    print()
    print(f"  Full ASA loop executed against {relay_url}:")
    print(f"    Step 1: Capability published  -> kind {AgentCapability.KIND}  (event {capability.event_id[:12]}...)")
    print(f"    Step 2: Capability discovered  -> {chosen.service_id}")
    print(f"    Step 3: Request sent           -> kind {AgentServiceRequest.KIND} (event {request.event_id[:12]}...)")
    print(f"    Step 4: Agreement created      -> kind {AgentServiceAgreement.KIND} (event {agreement.event_id[:12]}...)")
    print(f"    Step 5: L402 settlement        -> {agreement.agreed_price_sats} sats (simulated)")
    print()
    print("  Settlement modes: proxy (static endpoint) | producer (dynamic pricing)")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="NostrWolfe E2E Demo: Agent Service Agreement Full Loop",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--relay-url",
        default="wss://agents.lightningenable.com",
        help="Nostr relay WebSocket URL (default: wss://agents.lightningenable.com)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run in mock mode (no relay or external dependencies needed)",
    )
    args = parser.parse_args()

    if args.mock:
        asyncio.run(run_mock_demo())
    else:
        asyncio.run(run_live_demo(args.relay_url))


if __name__ == "__main__":
    main()
