"""Tests for Nostr event building, serialization, and ID computation."""

import hashlib
import json

from le_agent_sdk.nostr.event import NostrEvent


class TestNostrEventSerialization:
    def test_serialize_for_id_format(self):
        """ID serialization must follow NIP-01: [0, pubkey, created_at, kind, tags, content]."""
        event = {
            "pubkey": "aabbccdd",
            "created_at": 1700000000,
            "kind": 1,
            "tags": [["t", "test"]],
            "content": "hello world",
        }
        serialized = NostrEvent.serialize_for_id(event)
        parsed = json.loads(serialized)
        assert parsed[0] == 0
        assert parsed[1] == "aabbccdd"
        assert parsed[2] == 1700000000
        assert parsed[3] == 1
        assert parsed[4] == [["t", "test"]]
        assert parsed[5] == "hello world"

    def test_serialize_no_whitespace(self):
        event = {
            "pubkey": "ab",
            "created_at": 1,
            "kind": 1,
            "tags": [],
            "content": "",
        }
        serialized = NostrEvent.serialize_for_id(event)
        assert " " not in serialized
        assert "\n" not in serialized

    def test_compute_id_deterministic(self):
        event = {
            "pubkey": "aabbccdd",
            "created_at": 1700000000,
            "kind": 1,
            "tags": [],
            "content": "test",
        }
        id1 = NostrEvent.compute_id(event)
        id2 = NostrEvent.compute_id(event)
        assert id1 == id2

    def test_compute_id_is_sha256(self):
        event = {
            "pubkey": "aabbccdd",
            "created_at": 1700000000,
            "kind": 1,
            "tags": [],
            "content": "test",
        }
        serialized = NostrEvent.serialize_for_id(event)
        expected = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        assert NostrEvent.compute_id(event) == expected

    def test_different_content_different_id(self):
        event_a = {
            "pubkey": "ab",
            "created_at": 1,
            "kind": 1,
            "tags": [],
            "content": "hello",
        }
        event_b = {
            "pubkey": "ab",
            "created_at": 1,
            "kind": 1,
            "tags": [],
            "content": "world",
        }
        assert NostrEvent.compute_id(event_a) != NostrEvent.compute_id(event_b)

    def test_different_kind_different_id(self):
        event_a = {
            "pubkey": "ab",
            "created_at": 1,
            "kind": 1,
            "tags": [],
            "content": "same",
        }
        event_b = {
            "pubkey": "ab",
            "created_at": 1,
            "kind": 38400,
            "tags": [],
            "content": "same",
        }
        assert NostrEvent.compute_id(event_a) != NostrEvent.compute_id(event_b)

    def test_tags_affect_id(self):
        base = {
            "pubkey": "ab",
            "created_at": 1,
            "kind": 1,
            "content": "same",
        }
        event_a = {**base, "tags": []}
        event_b = {**base, "tags": [["t", "tag1"]]}
        assert NostrEvent.compute_id(event_a) != NostrEvent.compute_id(event_b)


class TestNostrEventCreate:
    def test_create_unsigned(self):
        event = NostrEvent.create_unsigned(
            kind=38400,
            content="test content",
            tags=[["d", "svc-1"]],
            pubkey="aabbccdd",
            created_at=1700000000,
        )
        assert event["kind"] == 38400
        assert event["content"] == "test content"
        assert event["tags"] == [["d", "svc-1"]]
        assert event["pubkey"] == "aabbccdd"
        assert event["created_at"] == 1700000000
        assert event["sig"] == ""
        assert len(event["id"]) == 64  # SHA-256 hex

    def test_create_sets_id(self):
        event = NostrEvent.create_unsigned(
            kind=1,
            content="hello",
            tags=[],
            pubkey="ff",
            created_at=1,
        )
        expected_id = NostrEvent.compute_id(event)
        assert event["id"] == expected_id

    def test_create_with_none_private_key(self):
        event = NostrEvent.create(
            kind=1,
            content="test",
            tags=[],
            private_key=None,
            pubkey="mypub",
            created_at=100,
        )
        assert event["pubkey"] == "mypub"
        assert event["sig"] == ""

    def test_unicode_content(self):
        event = NostrEvent.create_unsigned(
            kind=1,
            content="Hello world",
            tags=[],
            pubkey="ab",
            created_at=1,
        )
        assert event["content"] == "Hello world"
        assert len(event["id"]) == 64
