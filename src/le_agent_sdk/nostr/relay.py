"""WebSocket relay client for Nostr (NIP-01 protocol)."""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator, Optional

import websockets
import websockets.client


class RelayClient:
    """WebSocket client for communicating with Nostr relays.

    Supports connecting, publishing events, subscribing with filters,
    and listening for incoming messages.
    """

    def __init__(self) -> None:
        self._ws: Optional[websockets.client.WebSocketClientProtocol] = None
        self._url: str = ""
        self._subscriptions: dict[str, list[dict[str, Any]]] = {}

    @property
    def is_connected(self) -> bool:
        """True if the WebSocket connection is open."""
        return self._ws is not None and self._ws.open

    async def connect(self, url: str) -> None:
        """Connect to a Nostr relay.

        Args:
            url: WebSocket URL (e.g. wss://relay.example.com).
        """
        self._url = url
        self._ws = await websockets.connect(url, open_timeout=10)

    async def close(self) -> None:
        """Close the WebSocket connection."""
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def reconnect(self) -> None:
        """Reconnect to the last connected relay URL.

        Closes the existing connection (if any) and opens a new one.
        Resubscribes to all active subscriptions.

        Raises:
            ConnectionError: If no URL has been set (never connected before).
        """
        if not self._url:
            raise ConnectionError("Cannot reconnect: no previous URL")

        saved_subscriptions = dict(self._subscriptions)
        await self.close()
        self._ws = await websockets.connect(self._url, open_timeout=10)

        # Resubscribe to all previous subscriptions
        for sub_id, filters in saved_subscriptions.items():
            message = json.dumps(["REQ", sub_id] + filters)
            await self._ws.send(message)
            self._subscriptions[sub_id] = filters

    async def publish(self, event: dict[str, Any], timeout: float = 10.0) -> bool:
        """Publish a Nostr event to the relay.

        Sends ["EVENT", <event>] and waits for the OK response for this specific event.
        Ignores NOTICE and other messages while waiting for the OK.

        Args:
            event: Complete Nostr event dict (with id, sig, etc.).
            timeout: Maximum time to wait for OK response in seconds.

        Returns:
            True if the relay accepted the event.
        """
        import asyncio

        if not self._ws:
            raise ConnectionError("Not connected to a relay")

        event_id = event.get("id", "")
        message = json.dumps(["EVENT", event])
        await self._ws.send(message)

        # Wait for OK response matching this specific event ID
        try:
            async with asyncio.timeout(timeout):
                while True:
                    raw = await self._ws.recv()
                    response = json.loads(raw)
                    if not isinstance(response, list) or len(response) < 2:
                        continue
                    if response[0] == "OK" and len(response) >= 3:
                        # Check if this OK is for our event
                        if response[1] == event_id:
                            return bool(response[2])
                    # Ignore NOTICE and other message types; keep waiting
        except (asyncio.TimeoutError, TimeoutError):
            return False
        except Exception:
            return False

    async def subscribe(
        self,
        filters: list[dict[str, Any]],
        subscription_id: Optional[str] = None,
    ) -> str:
        """Subscribe to events matching the given filters.

        Sends ["REQ", <sub_id>, <filter1>, <filter2>, ...].

        Args:
            filters: List of Nostr filter objects.
            subscription_id: Optional custom subscription ID.

        Returns:
            The subscription ID.
        """
        if not self._ws:
            raise ConnectionError("Not connected to a relay")

        sub_id = subscription_id or uuid.uuid4().hex[:16]
        self._subscriptions[sub_id] = filters

        message = json.dumps(["REQ", sub_id] + filters)
        await self._ws.send(message)

        return sub_id

    async def unsubscribe(self, subscription_id: str) -> None:
        """Close a subscription.

        Sends ["CLOSE", <sub_id>].
        """
        if not self._ws:
            raise ConnectionError("Not connected to a relay")

        message = json.dumps(["CLOSE", subscription_id])
        await self._ws.send(message)
        self._subscriptions.pop(subscription_id, None)

    async def listen(self) -> AsyncIterator[tuple[str, Any]]:
        """Yield incoming relay messages as (message_type, payload) tuples.

        Message types:
            - "EVENT": payload is (subscription_id, event_dict)
            - "EOSE": payload is subscription_id
            - "OK": payload is (event_id, accepted, message)
            - "NOTICE": payload is message string
        """
        if not self._ws:
            raise ConnectionError("Not connected to a relay")

        async for raw in self._ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if not isinstance(msg, list) or len(msg) < 2:
                continue

            msg_type = msg[0]

            if msg_type == "EVENT" and len(msg) >= 3:
                yield ("EVENT", (msg[1], msg[2]))
            elif msg_type == "EOSE" and len(msg) >= 2:
                yield ("EOSE", msg[1])
            elif msg_type == "OK" and len(msg) >= 4:
                yield ("OK", (msg[1], msg[2], msg[3]))
            elif msg_type == "NOTICE" and len(msg) >= 2:
                yield ("NOTICE", msg[1])

    async def collect_events(
        self,
        filters: list[dict[str, Any]],
        timeout: float = 5.0,
    ) -> list[dict[str, Any]]:
        """Subscribe, collect events until EOSE, then unsubscribe.

        Convenience method for one-shot queries.

        Args:
            filters: Nostr filter objects.
            timeout: Maximum time to wait in seconds.

        Returns:
            List of event dicts received before EOSE.
        """
        import asyncio

        sub_id = await self.subscribe(filters)
        events: list[dict[str, Any]] = []

        try:
            async with asyncio.timeout(timeout):
                async for msg_type, payload in self.listen():
                    if msg_type == "EVENT":
                        recv_sub_id, event = payload
                        if recv_sub_id == sub_id:
                            events.append(event)
                    elif msg_type == "EOSE":
                        if payload == sub_id:
                            break
        except (asyncio.TimeoutError, TimeoutError):
            pass
        finally:
            try:
                await self.unsubscribe(sub_id)
            except Exception:
                pass

        return events

    async def __aenter__(self) -> RelayClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
