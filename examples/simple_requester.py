"""Example: Agent that discovers and uses translation services.

This demonstrates how to:
1. Discover available agent capabilities on the network
2. Send a service request
3. Settle via L402 payment

Usage:
    python simple_requester.py
"""

import asyncio

from le_agent_sdk import AgentManager


async def main():
    # Initialize the agent manager with your Nostr private key
    # For L402 auto-payment, provide a pay_invoice_callback
    manager = AgentManager(
        private_key="<your_hex_private_key>",
        relay_urls=["wss://agents.lightningenable.com"],
        # pay_invoice_callback=my_wallet.pay_invoice,  # Uncomment with real wallet
    )

    # Discover translation services
    print("Searching for translation services...")
    capabilities = await manager.discover(
        categories=["translation"],
        hashtags=["ai"],
        limit=10,
    )
    print(f"Found {len(capabilities)} services\n")

    for cap in capabilities:
        print(f"  [{cap.service_id}] {cap.content[:60]}...")
        if cap.pricing:
            print(f"    Price: {cap.pricing[0].amount} {cap.pricing[0].unit}/{cap.pricing[0].model}")
        if cap.l402_endpoint:
            print(f"    L402: {cap.l402_endpoint}")
        print()

    if not capabilities:
        print("No services found. Try publishing a capability first.")
        return

    # Pick the first capability
    chosen = capabilities[0]
    print(f"Using service: {chosen.service_id}")

    # Option A: Direct L402 settlement (skip negotiation)
    if chosen.l402_endpoint:
        print(f"Settling via L402 at {chosen.l402_endpoint}")
        try:
            result = await manager.settle_via_l402(chosen)
            print(f"Result: HTTP {result.status_code}")
            print(f"Body: {result.text[:200]}")
        except Exception as e:
            print(f"Settlement failed: {e}")
            print("(Configure pay_invoice_callback for auto-payment)")
    else:
        # Option B: Send a service request for negotiation
        print("No L402 endpoint; sending service request...")
        request = await manager.request_service(
            capability_event_id=chosen.event_id,
            provider_pubkey=chosen.pubkey,
            budget_sats=100,
            params={"source_lang": "en", "target_lang": "es"},
            content="Please translate: Hello, how are you?",
        )
        print(f"Sent request: {request.event_id}")
        print("Waiting for provider to respond with an agreement...")


if __name__ == "__main__":
    asyncio.run(main())
