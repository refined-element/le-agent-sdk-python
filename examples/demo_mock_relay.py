"""
Mock Nostr Relay for testing ASA agent flows.

A minimal asyncio WebSocket server that implements the Nostr relay protocol:
- Accepts EVENT messages and stores them in memory
- Responds to REQ messages by returning matching stored events
- Sends EOSE after each subscription
- Handles CLOSE messages to tear down subscriptions

Runs on localhost:8765 by default.

Usage:
    python demo_mock_relay.py
    python demo_mock_relay.py --port 9999
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

try:
    import websockets
    import websockets.server
except ImportError:
    print("ERROR: websockets is required. Install with: pip install websockets")
    sys.exit(1)


class MockRelay:
    """In-memory Nostr relay that stores events and serves subscriptions."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        # Map: websocket -> {sub_id: [filters]}
        self.subscriptions: dict[Any, dict[str, list[dict[str, Any]]]] = {}
        self.event_count = 0

    def _matches_filter(self, event: dict[str, Any], f: dict[str, Any]) -> bool:
        """Check if an event matches a single Nostr filter."""
        # kinds
        if "kinds" in f and event.get("kind") not in f["kinds"]:
            return False

        # authors
        if "authors" in f:
            pubkey = event.get("pubkey", "")
            if not any(pubkey.startswith(a) for a in f["authors"]):
                return False

        # ids
        if "ids" in f:
            eid = event.get("id", "")
            if not any(eid.startswith(i) for i in f["ids"]):
                return False

        # since / until
        created_at = event.get("created_at", 0)
        if "since" in f and created_at < f["since"]:
            return False
        if "until" in f and created_at > f["until"]:
            return False

        # Tag filters (#e, #p, #t, #s, #d, etc.)
        tags = event.get("tags", [])
        for key, values in f.items():
            if key.startswith("#") and isinstance(values, list):
                tag_letter = key[1:]
                event_tag_values = [t[1] for t in tags if len(t) >= 2 and t[0] == tag_letter]
                if not any(v in event_tag_values for v in values):
                    return False

        return True

    def _matches_any_filter(self, event: dict[str, Any], filters: list[dict[str, Any]]) -> bool:
        """Check if an event matches any filter in the list."""
        return any(self._matches_filter(event, f) for f in filters)

    def query(self, filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return stored events matching any of the given filters."""
        limit = None
        for f in filters:
            if "limit" in f:
                fl = f["limit"]
                if limit is None or fl < limit:
                    limit = fl

        matches = [e for e in self.events if self._matches_any_filter(e, filters)]

        # Sort by created_at descending (newest first), apply limit
        matches.sort(key=lambda e: e.get("created_at", 0), reverse=True)
        if limit is not None:
            matches = matches[:limit]

        return matches

    def store(self, event: dict[str, Any]) -> bool:
        """Store an event. Returns True if accepted."""
        eid = event.get("id", "")
        # Deduplicate by ID
        if any(e.get("id") == eid for e in self.events):
            return True  # Already have it, that is OK

        self.events.append(event)
        self.event_count += 1
        return True

    async def handle_client(self, websocket: Any) -> None:
        """Handle a single WebSocket client connection."""
        self.subscriptions[websocket] = {}
        remote = getattr(websocket, "remote_address", ("?", "?"))
        print(f"  [relay] Client connected from {remote}")

        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send(json.dumps(["NOTICE", "invalid JSON"]))
                    continue

                if not isinstance(msg, list) or len(msg) < 2:
                    await websocket.send(json.dumps(["NOTICE", "invalid message format"]))
                    continue

                msg_type = msg[0]

                if msg_type == "EVENT":
                    await self._handle_event(websocket, msg)
                elif msg_type == "REQ":
                    await self._handle_req(websocket, msg)
                elif msg_type == "CLOSE":
                    await self._handle_close(websocket, msg)
                else:
                    await websocket.send(json.dumps(["NOTICE", f"unknown message type: {msg_type}"]))

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.subscriptions.pop(websocket, None)
            print(f"  [relay] Client disconnected from {remote}")

    async def _handle_event(self, websocket: Any, msg: list[Any]) -> None:
        """Handle an EVENT message: store and broadcast to matching subscriptions."""
        if len(msg) < 2:
            return

        event = msg[1]
        eid = event.get("id", "unknown")
        kind = event.get("kind", "?")
        accepted = self.store(event)

        # Send OK response
        ok_msg = json.dumps(["OK", eid, accepted, "" if accepted else "error: rejected"])
        await websocket.send(ok_msg)
        print(f"  [relay] Stored event {eid[:16]}... kind={kind} (total: {self.event_count})")

        # Broadcast to subscribers with matching filters
        for ws, subs in self.subscriptions.items():
            for sub_id, filters in subs.items():
                if self._matches_any_filter(event, filters):
                    event_msg = json.dumps(["EVENT", sub_id, event])
                    try:
                        await ws.send(event_msg)
                    except Exception:
                        pass

    async def _handle_req(self, websocket: Any, msg: list[Any]) -> None:
        """Handle a REQ message: return stored matches then EOSE."""
        if len(msg) < 3:
            return

        sub_id = msg[1]
        filters = msg[2:]

        # Store the subscription
        self.subscriptions[websocket][sub_id] = filters

        # Send matching stored events
        matches = self.query(filters)
        for event in matches:
            event_msg = json.dumps(["EVENT", sub_id, event])
            await websocket.send(event_msg)

        # Send EOSE
        eose_msg = json.dumps(["EOSE", sub_id])
        await websocket.send(eose_msg)
        print(f"  [relay] REQ sub={sub_id[:12]}... sent {len(matches)} events + EOSE")

    async def _handle_close(self, websocket: Any, msg: list[Any]) -> None:
        """Handle a CLOSE message: remove subscription."""
        if len(msg) < 2:
            return

        sub_id = msg[1]
        if websocket in self.subscriptions:
            self.subscriptions[websocket].pop(sub_id, None)
        print(f"  [relay] CLOSE sub={sub_id[:12]}...")


async def run_relay(host: str, port: int) -> None:
    """Start the mock relay server."""
    relay = MockRelay()

    print(f"Mock Nostr Relay starting on ws://{host}:{port}")
    print("Press Ctrl+C to stop.\n")

    async with websockets.serve(relay.handle_client, host, port):
        await asyncio.Future()  # Run forever


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock Nostr Relay for ASA testing")
    parser.add_argument("--host", default="localhost", help="Bind host (default: localhost)")
    parser.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    args = parser.parse_args()

    try:
        asyncio.run(run_relay(args.host, args.port))
    except KeyboardInterrupt:
        print("\nRelay stopped.")


if __name__ == "__main__":
    main()
