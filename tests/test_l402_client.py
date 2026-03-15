"""Tests for L402 client — challenge parsing and HTTP flow."""

import pytest

from le_agent_sdk.l402.client import L402Challenge, L402Client, parse_l402_challenge


class TestParseL402Challenge:
    def test_quoted_format(self):
        headers = {
            "WWW-Authenticate": 'L402 macaroon="mac123", invoice="lnbc1..."'
        }
        challenge = parse_l402_challenge(headers)
        assert challenge is not None
        assert challenge.macaroon == "mac123"
        assert challenge.invoice == "lnbc1..."

    def test_unquoted_format(self):
        headers = {"WWW-Authenticate": "L402 macaroon=mac123, invoice=lnbc1..."}
        challenge = parse_l402_challenge(headers)
        assert challenge is not None
        assert challenge.macaroon == "mac123"
        assert challenge.invoice == "lnbc1..."

    def test_lsat_backward_compat(self):
        headers = {
            "WWW-Authenticate": 'LSAT macaroon="mac_legacy", invoice="lnbc_legacy"'
        }
        challenge = parse_l402_challenge(headers)
        assert challenge is not None
        assert challenge.macaroon == "mac_legacy"
        assert challenge.invoice == "lnbc_legacy"

    def test_case_insensitive_header(self):
        headers = {
            "www-authenticate": 'L402 macaroon="mac_lower", invoice="lnbc_lower"'
        }
        challenge = parse_l402_challenge(headers)
        assert challenge is not None
        assert challenge.macaroon == "mac_lower"

    def test_no_www_authenticate(self):
        headers = {"Content-Type": "application/json"}
        assert parse_l402_challenge(headers) is None

    def test_empty_www_authenticate(self):
        headers = {"WWW-Authenticate": ""}
        assert parse_l402_challenge(headers) is None

    def test_non_l402_challenge(self):
        headers = {"WWW-Authenticate": "Bearer realm=example"}
        assert parse_l402_challenge(headers) is None

    def test_authorization_header_property(self):
        c = L402Challenge(macaroon="mac1", invoice="inv1")
        assert c.authorization_header == "L402 mac1"


class TestL402Client:
    @pytest.mark.asyncio
    async def test_init_defaults(self):
        async with L402Client() as client:
            assert client._pay_callback is None
            assert client._cache == {}

    @pytest.mark.asyncio
    async def test_init_with_cache(self):
        cache = {"mac1": "preimage1"}
        async with L402Client(preimage_cache=cache) as client:
            assert client._cache == {"mac1": "preimage1"}

    @pytest.mark.asyncio
    async def test_close_idempotent(self):
        client = L402Client()
        await client.close()
        await client.close()  # Should not raise
