"""Example: Agent that provides a translation service.

This demonstrates how to:
1. Publish an agent capability to Nostr relays
2. Listen for incoming service requests
3. Create agreements with requesters

Usage:
    python simple_provider.py
"""

import asyncio

from le_agent_sdk import AgentCapability, AgentManager, AgentPricing


async def main():
    # Initialize the agent manager with your Nostr private key
    manager = AgentManager(
        private_key="<your_hex_private_key>",
        relay_urls=["wss://agents.lightningenable.com"],
    )

    # Define a capability to advertise
    cap = AgentCapability(
        service_id="translate-v1",
        categories=["ai", "translation"],
        content="AI translation service. Supports 50+ languages. "
        "Send text with source/target language params.",
        pricing=[
            AgentPricing(amount=10, unit="sats", model="per-request"),
            AgentPricing(amount=1, unit="sats", model="per-token"),
        ],
        l402_endpoint="https://api.lightningenable.com/l402/proxy/translate-abc123",
        api_endpoint="https://api.example.com/translate",
        api_method="POST",
        schema_url="https://api.example.com/schema/translate.json",
        hashtags=["translation", "ai", "multilingual"],
    )

    # Publish the capability to relays
    event_id = await manager.publish_capability(cap)
    print(f"Published capability: {event_id}")
    print(f"Service ID: {cap.service_id}")
    print(f"Categories: {cap.categories}")
    print(f"Pricing: {cap.pricing[0].amount} {cap.pricing[0].unit}/{cap.pricing[0].model}")
    print()

    # Listen for incoming service requests
    print("Listening for service requests...")
    async for request in manager.listen_requests():
        print(f"Received request from {request.pubkey}")
        print(f"  Budget: {request.budget_sats} sats")
        print(f"  Params: {request.params}")
        print(f"  Content: {request.content}")

        # Create an agreement
        agreement = await manager.publish_agreement(
            request_event_id=request.event_id,
            capability_event_id=event_id,
            requester_pubkey=request.pubkey,
            agreed_price_sats=min(request.budget_sats, cap.pricing[0].amount),
            l402_endpoint=cap.l402_endpoint or "",
            terms="Max 10 requests per minute. Results in JSON.",
        )
        print(f"  Published agreement: {agreement.event_id}")


if __name__ == "__main__":
    asyncio.run(main())
