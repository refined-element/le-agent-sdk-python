"""
NostrWolfe MCP Integration Demo

Simulates what Claude (or any LLM agent) would do via MCP tools to interact
with the ASA protocol. This is illustrative -- it shows the flow without
needing actual MCP server infrastructure.

The MCP tool pattern maps to three agent operations:
    1. discover_agent_services  -> Find capabilities on the Nostr network
    2. request_agent_service    -> Negotiate terms with a provider
    3. settle_agent_service     -> Pay via L402 and receive the result

Usage:
    python demo_mcp_integration.py
    python demo_mcp_integration.py --relay-url ws://localhost:8765
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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from le_agent_sdk.models.capability import AgentCapability, AgentPricing
from le_agent_sdk.models.request import AgentServiceRequest
from le_agent_sdk.models.agreement import AgentServiceAgreement


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]


def mock_pubkey(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def mock_event_id(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def section(title: str) -> None:
    print(f"\n{'─'*70}")
    print(f"  {title}")
    print(f"{'─'*70}\n")


# ---------------------------------------------------------------------------
# Simulated MCP Tool Definitions
# ---------------------------------------------------------------------------

MCP_TOOL_DEFINITIONS = [
    {
        "name": "discover_agent_services",
        "description": "Search the Nostr network for agent capabilities matching given criteria.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "categories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Service categories to filter by (e.g., 'translation', 'ai')",
                },
                "hashtags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Hashtags to filter by (e.g., 'multilingual')",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results",
                    "default": 10,
                },
            },
        },
    },
    {
        "name": "request_agent_service",
        "description": "Send a service request to an agent provider, beginning negotiation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "capability_event_id": {
                    "type": "string",
                    "description": "Event ID of the capability to request",
                },
                "provider_pubkey": {
                    "type": "string",
                    "description": "Provider's Nostr public key",
                },
                "budget_sats": {
                    "type": "integer",
                    "description": "Maximum budget in satoshis",
                },
                "params": {
                    "type": "object",
                    "description": "Key-value parameters for the service",
                },
                "content": {
                    "type": "string",
                    "description": "Human-readable request message",
                },
            },
            "required": ["capability_event_id", "provider_pubkey", "budget_sats"],
        },
    },
    {
        "name": "settle_agent_service",
        "description": "Settle an agreement via L402 Lightning payment and receive the result.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agreement_event_id": {
                    "type": "string",
                    "description": "Event ID of the service agreement",
                },
                "l402_endpoint": {
                    "type": "string",
                    "description": "L402 endpoint URL from the agreement",
                },
            },
            "required": ["agreement_event_id", "l402_endpoint"],
        },
    },
]


# ---------------------------------------------------------------------------
# Simulated MCP Tool Handlers
# ---------------------------------------------------------------------------

# Pre-populated mock data representing what a real relay would return
MOCK_PROVIDER_PUBKEY = mock_pubkey("provider-agent-42")
MOCK_CAP_EVENT_ID = mock_event_id("translate-v1-capability")

MOCK_CAPABILITIES = [
    {
        "service_id": "translate-v1",
        "event_id": MOCK_CAP_EVENT_ID,
        "pubkey": MOCK_PROVIDER_PUBKEY,
        "content": "AI-powered translation service. Supports 50+ languages.",
        "categories": ["ai", "translation"],
        "pricing": [{"amount": 10, "unit": "sats", "model": "per-request"}],
        "l402_endpoint": "https://api.lightningenable.com/l402/proxy/translate-demo",
        "hashtags": ["translation", "ai", "multilingual"],
    },
    {
        "service_id": "summarize-v1",
        "event_id": mock_event_id("summarize-v1-capability"),
        "pubkey": mock_pubkey("provider-agent-99"),
        "content": "Text summarization service. Condense long documents into key points.",
        "categories": ["ai", "summarization"],
        "pricing": [{"amount": 5, "unit": "sats", "model": "per-request"}],
        "l402_endpoint": "https://api.lightningenable.com/l402/proxy/summarize-demo",
        "hashtags": ["summarization", "ai", "nlp"],
    },
]


def handle_discover(params: dict[str, Any]) -> dict[str, Any]:
    """Simulate discover_agent_services MCP tool."""
    categories = params.get("categories", [])
    hashtags = params.get("hashtags", [])
    limit = params.get("limit", 10)

    # Filter mock capabilities
    results = []
    for cap in MOCK_CAPABILITIES:
        if categories:
            if not any(c in cap["categories"] for c in categories):
                continue
        if hashtags:
            if not any(h in cap["hashtags"] for h in hashtags):
                continue
        results.append(cap)

    return {
        "status": "success",
        "count": len(results[:limit]),
        "capabilities": results[:limit],
    }


def handle_request(params: dict[str, Any]) -> dict[str, Any]:
    """Simulate request_agent_service MCP tool."""
    req_event_id = mock_event_id(f"request-{time.time()}")

    # Simulate the provider responding with an agreement
    agr_event_id = mock_event_id(f"agreement-{time.time()}")

    return {
        "status": "success",
        "request_event_id": req_event_id,
        "agreement": {
            "event_id": agr_event_id,
            "request_event_id": req_event_id,
            "capability_event_id": params["capability_event_id"],
            "provider_pubkey": params["provider_pubkey"],
            "agreed_price_sats": min(params.get("budget_sats", 100), 10),
            "l402_endpoint": "https://api.lightningenable.com/l402/proxy/translate-demo",
            "terms": "Max 10 requests per minute. Results in JSON format.",
            "expires_at": int(time.time()) + 3600,
        },
    }


def handle_settle(params: dict[str, Any]) -> dict[str, Any]:
    """Simulate settle_agent_service MCP tool."""
    mock_preimage = hashlib.sha256(f"payment-{time.time()}".encode()).hexdigest()

    return {
        "status": "success",
        "payment": {
            "preimage": mock_preimage,
            "amount_sats": 10,
            "invoice": "lnbc100n1p...(mock)",
        },
        "result": {
            "http_status": 200,
            "body": {
                "translation": "Hola, como estas hoy?",
                "source_lang": "en",
                "target_lang": "es",
                "confidence": 0.97,
            },
        },
    }


MCP_HANDLERS = {
    "discover_agent_services": handle_discover,
    "request_agent_service": handle_request,
    "settle_agent_service": handle_settle,
}


def call_mcp_tool(name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Simulate calling an MCP tool (what the Claude MCP runtime does)."""
    handler = MCP_HANDLERS.get(name)
    if handler is None:
        return {"status": "error", "message": f"Unknown tool: {name}"}
    return handler(params)


# ---------------------------------------------------------------------------
# Demo: Simulating Claude's MCP tool usage
# ---------------------------------------------------------------------------

async def run_demo() -> None:
    """Simulate an LLM agent using MCP tools for the ASA flow."""

    print("\n" + "#" * 70)
    print("#  NOSTRWOLFE MCP INTEGRATION DEMO")
    print("#  Simulating Claude's tool calls for the ASA protocol")
    print("#" + "#" * 69)

    # Show available MCP tools
    section("Available MCP Tools")
    for tool in MCP_TOOL_DEFINITIONS:
        props = tool["inputSchema"].get("properties", {})
        param_names = list(props.keys())
        print(f"  {tool['name']}")
        print(f"    {tool['description']}")
        print(f"    params: {', '.join(param_names)}")
        print()

    # -----------------------------------------------------------------------
    # Claude's thought: "The user wants to translate text. Let me find a
    # translation service on the network."
    # -----------------------------------------------------------------------
    section("MCP Tool Call 1: discover_agent_services")

    discover_params = {
        "categories": ["translation"],
        "hashtags": ["ai"],
        "limit": 5,
    }

    print(f"  Claude calls: discover_agent_services")
    print(f"  Parameters:   {json.dumps(discover_params, indent=2)}")
    print()

    discover_result = call_mcp_tool("discover_agent_services", discover_params)

    print(f"  Result:")
    print(f"    Found {discover_result['count']} service(s):")
    for cap in discover_result["capabilities"]:
        print(f"      [{cap['service_id']}] {cap['content'][:50]}...")
        print(f"        Price: {cap['pricing'][0]['amount']} {cap['pricing'][0]['unit']}/{cap['pricing'][0]['model']}")
        print(f"        L402:  {cap['l402_endpoint']}")
    print()

    # Claude picks the first service
    chosen = discover_result["capabilities"][0]
    print(f"  Claude selects: {chosen['service_id']} (best match for translation)")

    # -----------------------------------------------------------------------
    # Claude's thought: "I found a translation service at 10 sats per request.
    # Let me send a service request with my budget."
    # -----------------------------------------------------------------------
    section("MCP Tool Call 2: request_agent_service")

    request_params = {
        "capability_event_id": chosen["event_id"],
        "provider_pubkey": chosen["pubkey"],
        "budget_sats": 100,
        "params": {
            "source_lang": "en",
            "target_lang": "es",
        },
        "content": "Please translate: Hello, how are you today?",
    }

    print(f"  Claude calls: request_agent_service")
    print(f"  Parameters:   {json.dumps(request_params, indent=2)}")
    print()

    request_result = call_mcp_tool("request_agent_service", request_params)

    print(f"  Result:")
    agr = request_result["agreement"]
    print(f"    Request event:    {request_result['request_event_id'][:24]}...")
    print(f"    Agreement event:  {agr['event_id'][:24]}...")
    print(f"    Agreed price:     {agr['agreed_price_sats']} sats")
    print(f"    L402 endpoint:    {agr['l402_endpoint']}")
    print(f"    Terms:            {agr['terms']}")

    # -----------------------------------------------------------------------
    # Claude's thought: "The provider agreed to 10 sats. Let me settle
    # the payment via L402 and get the translation."
    # -----------------------------------------------------------------------
    section("MCP Tool Call 3: settle_agent_service")

    settle_params = {
        "agreement_event_id": agr["event_id"],
        "l402_endpoint": agr["l402_endpoint"],
    }

    print(f"  Claude calls: settle_agent_service")
    print(f"  Parameters:   {json.dumps(settle_params, indent=2)}")
    print()

    settle_result = call_mcp_tool("settle_agent_service", settle_params)

    print(f"  Result:")
    print(f"    Payment preimage: {settle_result['payment']['preimage'][:24]}...")
    print(f"    Amount paid:      {settle_result['payment']['amount_sats']} sats")
    print(f"    HTTP status:      {settle_result['result']['http_status']}")
    print(f"    Response body:    {json.dumps(settle_result['result']['body'], indent=6)}")

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    section("Demo Summary")

    print("  This demo showed how Claude uses 3 MCP tools for the full ASA flow:\n")
    print("  1. discover_agent_services")
    print("     -> Queries Nostr relays for kind 38400 capability events")
    print("     -> Returns matching services with pricing and L402 endpoints\n")
    print("  2. request_agent_service")
    print("     -> Publishes kind 38401 service request to the provider")
    print("     -> Receives kind 38402 agreement with negotiated terms\n")
    print("  3. settle_agent_service")
    print("     -> Hits L402 endpoint, receives 402 challenge")
    print("     -> Pays Lightning invoice, retries with L402 token")
    print("     -> Returns the service result\n")
    print("  In production, these MCP tools wrap AgentManager methods:")
    print("    discover_agent_services  -> AgentManager.discover()")
    print("    request_agent_service    -> AgentManager.request_service() + listen for agreement")
    print("    settle_agent_service     -> AgentManager.settle()")
    print()

    # Show what a real MCP server definition looks like
    section("MCP Server Configuration (for reference)")

    mcp_config = {
        "mcpServers": {
            "nostrwolfe": {
                "command": "python",
                "args": ["-m", "le_agent_sdk.mcp_server"],
                "env": {
                    "NOSTR_PRIVATE_KEY": "<hex_private_key>",
                    "RELAY_URLS": "wss://agents.lightningenable.com",
                    "LE_API_KEY": "<lightning_enable_api_key>",
                },
            }
        }
    }

    print(f"  claude_desktop_config.json:")
    print(f"  {json.dumps(mcp_config, indent=4)}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NostrWolfe MCP Integration Demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--relay-url",
        default=None,
        help="(Unused in this demo -- illustrative only)",
    )
    args = parser.parse_args()

    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
