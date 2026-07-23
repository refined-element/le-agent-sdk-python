"""Port-drift conformance tests (python port).

Runs the shared golden vectors in ``conformance/vectors/`` through THIS port's
own implementation. The same vectors run in the .NET and TypeScript ports; any
port that diverges from the golden fails its own CI, so drift between the three
ports is caught automatically instead of by manual cross-reading.

See ``conformance/README.md`` for the design, the sync mechanism, and how to
extend the suite.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import pytest

from le_agent_sdk.agent.manager import AgentManager
from le_agent_sdk.models.capability import AgentCapability
from le_agent_sdk.nostr.event import NostrEvent

# --- Vector loading ---------------------------------------------------------

_CONFORMANCE_DIR = Path(__file__).resolve().parent.parent / "conformance"
_VECTORS_DIR = _CONFORMANCE_DIR / "vectors"


def _load(name: str) -> dict:
    return json.loads((_VECTORS_DIR / name).read_text(encoding="utf-8"))


_PRICE = _load("price-tag.json")
_FLOOR = _load("negotiable-floor.json")
_DISCOVER = _load("discover-resilience.json")


def _capability_from_tags(tags: list[list[str]]) -> AgentCapability:
    """Parse a capability through the public entrypoint the vectors target."""
    event = {
        "id": "conformance",
        "pubkey": "p",
        "created_at": 1,
        "kind": AgentCapability.KIND,
        "content": "",
        "tags": tags,
    }
    return AgentCapability.from_nostr_event(event)


# --- Sync guard -------------------------------------------------------------


def test_vectors_match_shared_checksums():
    """The local vectors must match the shared CHECKSUMS byte-for-byte.

    CHECKSUMS is identical across all three repos, so this transitively pins the
    python copy to the .NET and TypeScript copies. Hashing is over LF-normalized
    bytes so a CRLF checkout (Windows CI) does not spuriously fail.
    """
    expected = {}
    for line in (_CONFORMANCE_DIR / "CHECKSUMS").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        digest, name = line.split()
        expected[name] = digest

    assert expected, "CHECKSUMS is empty"

    for path in sorted(_VECTORS_DIR.glob("*.json")):
        data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        got = hashlib.sha256(data).hexdigest()
        assert got == expected.get(path.name), (
            f"{path.name} does not match shared CHECKSUMS "
            f"(edit the canonical copy + regenerate CHECKSUMS in all repos)"
        )


# --- price-tag parsing ------------------------------------------------------


@pytest.mark.parametrize("vector", _PRICE["vectors"], ids=lambda v: v["name"])
def test_price_tag(vector):
    tags = vector["tags"]
    expect = vector["expect"]
    outcome = expect["outcome"]

    if outcome == "reject":
        with pytest.raises(Exception):
            _capability_from_tags(tags)
        return

    cap = _capability_from_tags(tags)

    if outcome == "no-price":
        assert cap.pricing == [], f"{vector['name']}: expected no price recorded"
        return

    assert outcome == "ok", f"unknown outcome {outcome!r}"
    assert cap.pricing, f"{vector['name']}: expected a parsed price"
    price = cap.pricing[0]
    assert price.amount == expect["priceSats"]
    if "unit" in expect:
        assert price.unit == expect["unit"]
    if "model" in expect:
        assert price.model == expect["model"]


# --- negotiable-floor parsing ----------------------------------------------


@pytest.mark.parametrize("vector", _FLOOR["vectors"], ids=lambda v: v["name"])
def test_negotiable_floor(vector):
    tags = vector["tags"]
    expect = vector["expect"]
    outcome = expect["outcome"]

    if outcome == "reject":
        with pytest.raises(Exception):
            _capability_from_tags(tags)
        return

    assert outcome == "ok", f"unknown outcome {outcome!r}"
    cap = _capability_from_tags(tags)
    assert cap.negotiable is expect["negotiable"]
    assert cap.min_price_sats == expect["minPriceSats"]


# --- discover() batch resilience (ledger #41) -------------------------------

_PRIV_A = "11" * 32
_PRIV_B = "22" * 32
_PRIV_POISON = "33" * 32


def _signed(d_tag: str, price: str, priv: str) -> dict:
    return NostrEvent.create(
        kind=AgentCapability.KIND,
        content="valid",
        tags=[["d", d_tag], ["price", price]],
        private_key=priv,
    )


def _build_batch(scenario_name: str) -> list:
    """Realize a [valid, malformed, valid] batch for a shared scenario.

    Valid events are genuinely signed so the real authenticity check passes them.
    The malformed payload is dropped by the real pipeline at the layer this port
    first meets it.
    """
    valid_a = _signed("svc-a", "100", _PRIV_A)
    valid_b = _signed("svc-b", "200", _PRIV_B)

    if scenario_name == "bad-price":
        malformed = _signed("svc-poison", "abc", _PRIV_POISON)
    elif scenario_name == "missing-committed-field":
        # Missing pubkey/created_at/tags/content -> NostrEvent.verify() subscripts
        # them while computing the id and raises -> dropped as unauthenticatable.
        malformed = {"id": "bad-missing", "kind": AgentCapability.KIND}
    elif scenario_name == "non-dict-payload":
        malformed = "not-a-dict"
    else:  # pragma: no cover - guards against an unhandled new scenario
        raise AssertionError(f"unhandled scenario {scenario_name!r}")

    return [valid_a, malformed, valid_b]


async def _run_discover(payloads: list) -> list[AgentCapability]:
    """Inject a raw per-relay payload list and run the real discover pipeline."""
    mgr = AgentManager()

    async def fake_query_relay(url, filters, timeout):
        return list(payloads)

    from unittest.mock import patch

    with patch.object(mgr, "_query_relay", side_effect=fake_query_relay):
        return await mgr.discover()


def test_discover_resilience_scenarios_are_covered():
    """Every scenario in the shared manifest must be exercised below."""
    names = {s["name"] for s in _DISCOVER["scenarios"]}
    assert names == {"bad-price", "missing-committed-field", "non-dict-payload"}
    assert _DISCOVER["expectedSurvivors"] == 2


@pytest.mark.parametrize(
    "scenario", _DISCOVER["scenarios"], ids=lambda s: s["name"]
)
@pytest.mark.asyncio
async def test_discover_resilience(scenario, caplog):
    expected_survivors = _DISCOVER["expectedSurvivors"]
    batch = _build_batch(scenario["name"])

    with caplog.at_level(logging.WARNING):
        caps = await _run_discover(batch)

    assert len(caps) == expected_survivors, (
        f"{scenario['name']}: one malformed payload aborted the batch"
    )
    assert {c.service_id for c in caps} == {"svc-a", "svc-b"}
    # Fail closed, LOUDLY: the malformed payload's skip must be logged.
    assert any(
        record.levelno == logging.WARNING for record in caplog.records
    ), f"{scenario['name']}: malformed payload was skipped silently"
