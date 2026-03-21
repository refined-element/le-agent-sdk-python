"""Tests for L402 client — challenge parsing, MPP support, and HTTP flow."""

import pytest
import httpx
from unittest.mock import AsyncMock, patch

from le_agent_sdk.l402.client import (
    L402Challenge,
    L402Client,
    L402ProducerClient,
    L402VerifyResponse,
    MppChallenge,
    parse_l402_challenge,
    parse_mpp_challenge,
    parse_payment_challenge,
)


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


class TestL402ClientAccessFlow:
    """Tests for the 402 -> pay -> retry flow in L402Client.access()."""

    @pytest.mark.asyncio
    async def test_access_l402_402_pay_retry(self):
        """L402 challenge: 402 response, pay invoice, retry with L402 Authorization header."""
        fake_preimage = "ab" * 32

        # First call returns 402 with L402 challenge
        response_402 = httpx.Response(
            402,
            headers={"WWW-Authenticate": 'L402 macaroon="mac_test", invoice="lnbc100n1pjtest"'},
            request=httpx.Request("GET", "https://api.example.com/resource"),
        )
        # Second call returns 200 (after auth)
        response_200 = httpx.Response(
            200,
            json={"data": "success"},
            request=httpx.Request("GET", "https://api.example.com/resource"),
        )

        pay_callback = AsyncMock(return_value=fake_preimage)

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock, side_effect=[response_402, response_200]) as mock_request:
            async with L402Client(pay_invoice_callback=pay_callback) as client:
                result = await client.access("https://api.example.com/resource")

            assert result.status_code == 200
            pay_callback.assert_awaited_once_with("lnbc100n1pjtest")
            # Verify the retry request used the correct L402 Authorization header
            retry_call = mock_request.call_args_list[1]
            retry_headers = retry_call.kwargs.get("headers") or retry_call[1].get("headers", {})
            assert retry_headers["Authorization"] == f"L402 mac_test:{fake_preimage}"

    @pytest.mark.asyncio
    async def test_access_mpp_402_pay_retry(self):
        """MPP challenge: 402 response, pay invoice, retry with Payment Authorization header."""
        fake_preimage = "cd" * 32

        # First call returns 402 with MPP challenge
        response_402 = httpx.Response(
            402,
            headers={"WWW-Authenticate": 'Payment method="lightning", invoice="lnbc200n1pjmpptest"'},
            request=httpx.Request("GET", "https://api.example.com/mpp-resource"),
        )
        # Second call returns 200 (after auth)
        response_200 = httpx.Response(
            200,
            json={"data": "mpp-success"},
            request=httpx.Request("GET", "https://api.example.com/mpp-resource"),
        )

        pay_callback = AsyncMock(return_value=fake_preimage)

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock, side_effect=[response_402, response_200]) as mock_request:
            async with L402Client(pay_invoice_callback=pay_callback) as client:
                result = await client.access("https://api.example.com/mpp-resource")

            assert result.status_code == 200
            pay_callback.assert_awaited_once_with("lnbc200n1pjmpptest")
            # Verify the retry request used the correct Payment Authorization header
            retry_call = mock_request.call_args_list[1]
            retry_headers = retry_call.kwargs.get("headers") or retry_call[1].get("headers", {})
            assert retry_headers["Authorization"] == f'Payment method="lightning", preimage="{fake_preimage}"'

    @pytest.mark.asyncio
    async def test_access_no_callback_returns_402(self):
        """Without a pay callback, 402 responses are returned as-is."""
        response_402 = httpx.Response(
            402,
            headers={"WWW-Authenticate": 'L402 macaroon="mac_test", invoice="lnbc100n1pjtest"'},
            request=httpx.Request("GET", "https://api.example.com/resource"),
        )

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=response_402):
            async with L402Client() as client:
                result = await client.access("https://api.example.com/resource")

            assert result.status_code == 402


class TestL402ClientPayAndAccessFlow:
    """Tests for the 402 -> pay -> retry flow in L402Client.pay_and_access()."""

    @pytest.mark.asyncio
    async def test_pay_and_access_l402_flow(self):
        """L402: pay_and_access should pay invoice and retry with L402 Authorization."""
        fake_preimage = "ef" * 32

        response_402 = httpx.Response(
            402,
            headers={"WWW-Authenticate": 'L402 macaroon="mac_paa", invoice="lnbc300n1pjpaatest"'},
            request=httpx.Request("GET", "https://api.example.com/l402-resource"),
        )
        response_200 = httpx.Response(
            200,
            json={"data": "l402-paid"},
            request=httpx.Request("GET", "https://api.example.com/l402-resource"),
        )

        pay_callback = AsyncMock(return_value=fake_preimage)

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock, side_effect=[response_402, response_200]) as mock_request:
            async with L402Client() as client:
                result = await client.pay_and_access(
                    "https://api.example.com/l402-resource",
                    pay_invoice_callback=pay_callback,
                )

            assert result.status_code == 200
            pay_callback.assert_awaited_once_with("lnbc300n1pjpaatest")
            retry_call = mock_request.call_args_list[1]
            retry_headers = retry_call.kwargs.get("headers") or retry_call[1].get("headers", {})
            assert retry_headers["Authorization"] == f"L402 mac_paa:{fake_preimage}"

    @pytest.mark.asyncio
    async def test_pay_and_access_mpp_flow(self):
        """MPP: pay_and_access should pay invoice and retry with Payment Authorization."""
        fake_preimage = "01" * 32

        response_402 = httpx.Response(
            402,
            headers={"WWW-Authenticate": 'Payment method="lightning", invoice="lnbc400n1pjmpppaa"'},
            request=httpx.Request("GET", "https://api.example.com/mpp-resource"),
        )
        response_200 = httpx.Response(
            200,
            json={"data": "mpp-paid"},
            request=httpx.Request("GET", "https://api.example.com/mpp-resource"),
        )

        pay_callback = AsyncMock(return_value=fake_preimage)

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock, side_effect=[response_402, response_200]) as mock_request:
            async with L402Client() as client:
                result = await client.pay_and_access(
                    "https://api.example.com/mpp-resource",
                    pay_invoice_callback=pay_callback,
                )

            assert result.status_code == 200
            pay_callback.assert_awaited_once_with("lnbc400n1pjmpppaa")
            retry_call = mock_request.call_args_list[1]
            retry_headers = retry_call.kwargs.get("headers") or retry_call[1].get("headers", {})
            assert retry_headers["Authorization"] == f'Payment method="lightning", preimage="{fake_preimage}"'

    @pytest.mark.asyncio
    async def test_pay_and_access_callback_failure_raises(self):
        """Payment callback failure should raise RuntimeError with context."""
        response_402 = httpx.Response(
            402,
            headers={"WWW-Authenticate": 'L402 macaroon="mac_fail", invoice="lnbc100n1pjfail"'},
            request=httpx.Request("GET", "https://api.example.com/resource"),
        )

        pay_callback = AsyncMock(side_effect=ConnectionError("wallet offline"))

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=response_402):
            async with L402Client() as client:
                with pytest.raises(RuntimeError, match="Payment callback failed"):
                    await client.pay_and_access(
                        "https://api.example.com/resource",
                        pay_invoice_callback=pay_callback,
                    )

    @pytest.mark.asyncio
    async def test_pay_and_access_non_402_returns_directly(self):
        """Non-402 responses should be returned without attempting payment."""
        response_200 = httpx.Response(
            200,
            json={"data": "free"},
            request=httpx.Request("GET", "https://api.example.com/free"),
        )

        pay_callback = AsyncMock()

        with patch("httpx.AsyncClient.request", new_callable=AsyncMock, return_value=response_200):
            async with L402Client() as client:
                result = await client.pay_and_access(
                    "https://api.example.com/free",
                    pay_invoice_callback=pay_callback,
                )

            assert result.status_code == 200
            pay_callback.assert_not_awaited()


class TestMppRegexBoundary:
    """Tests for MPP regex parameter-boundary matching."""

    def test_no_false_positive_on_substring_method(self):
        """A parameter like some_method='lightning' should not be matched as method='lightning'."""
        header = 'Payment some_method="lightning", invoice="lnbc100n1pjtest", method="lightning"'
        result = parse_mpp_challenge(header)
        # Should still parse correctly because real method="lightning" is present
        assert result.invoice == "lnbc100n1pjtest"

    def test_reject_only_prefixed_method(self):
        """If only a prefixed 'method' exists (no real 'method'), it should fail."""
        header = 'Payment xmethod="lightning", invoice="lnbc100n1pjtest"'
        with pytest.raises(ValueError):
            parse_mpp_challenge(header)


class TestMppChallengeParsing:
    def test_parse_valid_mpp_header(self):
        header = 'Payment realm="api.example.com", method="lightning", invoice="lnbc100n1pjtest", amount="100", currency="sat"'
        result = parse_mpp_challenge(header)
        assert isinstance(result, MppChallenge)
        assert result.invoice == "lnbc100n1pjtest"
        assert result.amount == "100"
        assert result.realm == "api.example.com"

    def test_parse_non_lightning_raises(self):
        with pytest.raises(ValueError):
            parse_mpp_challenge('Payment method="stripe", invoice="lnbc100n1pjtest"')

    def test_parse_missing_invoice_raises(self):
        with pytest.raises(ValueError):
            parse_mpp_challenge('Payment method="lightning", amount="100"')

    def test_parse_minimal_header(self):
        result = parse_mpp_challenge(
            'Payment method="lightning", invoice="lnbc100n1pjtest"'
        )
        assert result.invoice == "lnbc100n1pjtest"
        assert result.amount is None
        assert result.realm is None

    def test_parse_case_insensitive(self):
        header = 'PAYMENT METHOD="LIGHTNING", INVOICE="lnbc100n1pjtest", AMOUNT="50"'
        result = parse_mpp_challenge(header)
        assert result.invoice == "lnbc100n1pjtest"
        assert result.amount == "50"

    def test_mpp_challenge_frozen(self):
        c = MppChallenge(invoice="inv1", amount="100", realm="example.com")
        with pytest.raises(AttributeError):
            c.invoice = "changed"

    def test_realm_scoped_to_payment_segment(self):
        """Realm from a different scheme (Bearer) must not leak into MPP."""
        header = (
            'Bearer realm="other-service.com", '
            'Payment method="lightning", invoice="lnbc100n1pjtest"'
        )
        result = parse_mpp_challenge(header)
        assert result.invoice == "lnbc100n1pjtest"
        # The Bearer realm must NOT be captured
        assert result.realm is None

    def test_realm_scoped_with_trailing_scheme(self):
        """Realm from a trailing scheme must not leak into a Payment challenge."""
        header = (
            'Payment method="lightning", invoice="lnbc100n1pjtest", '
            'Bearer realm="other-service.com"'
        )
        result = parse_mpp_challenge(header)
        assert result.invoice == "lnbc100n1pjtest"
        # The trailing Bearer realm must NOT be captured
        assert result.realm is None


class TestParsePaymentChallenge:
    def test_l402_preferred(self):
        headers = {
            "WWW-Authenticate": 'L402 macaroon="abc", invoice="lnbc100n1pjtest"'
        }
        result = parse_payment_challenge(headers)
        assert isinstance(result, L402Challenge)
        assert result.macaroon == "abc"
        assert result.invoice == "lnbc100n1pjtest"

    def test_mpp_fallback(self):
        headers = {
            "WWW-Authenticate": 'Payment method="lightning", invoice="lnbc100n1pjtest"'
        }
        result = parse_payment_challenge(headers)
        assert isinstance(result, MppChallenge)
        assert result.invoice == "lnbc100n1pjtest"

    def test_invalid_raises(self):
        headers = {"WWW-Authenticate": "Bearer token123"}
        with pytest.raises(ValueError):
            parse_payment_challenge(headers)

    def test_no_header_raises(self):
        headers = {"Content-Type": "application/json"}
        with pytest.raises(ValueError):
            parse_payment_challenge(headers)

    def test_empty_header_raises(self):
        headers = {"WWW-Authenticate": ""}
        with pytest.raises(ValueError, match="Empty WWW-Authenticate header"):
            parse_payment_challenge(headers)

    def test_l402_with_both_present(self):
        """When both L402 and MPP headers exist (combined), L402 is preferred."""
        headers = {
            "WWW-Authenticate": (
                'L402 macaroon="mac1", invoice="lnbc100n1pjl402" '
                'Payment method="lightning", invoice="lnbc100n1pjmpp"'
            )
        }
        result = parse_payment_challenge(headers)
        assert isinstance(result, L402Challenge)
        assert result.macaroon == "mac1"


class TestL402ProducerClientVerifyPayment:
    """Tests for L402ProducerClient.verify_payment() covering both L402 and MPP flows."""

    @pytest.mark.asyncio
    async def test_verify_with_macaroon_sends_both_fields(self):
        """When macaroon is provided, payload should include both macaroon and preimage."""
        mock_response = httpx.Response(
            200,
            json={"valid": True, "resource": "/api/data"},
            request=httpx.Request("POST", "https://api.lightningenable.com/api/l402/challenges/verify"),
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            async with L402ProducerClient(le_api_key="test-key") as client:
                result = await client.verify_payment("mac123", preimage="aa" * 32)

            assert result.success is True
            assert result.valid is True
            assert result.resource == "/api/data"
            call_kwargs = mock_post.call_args
            payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
            assert payload["macaroon"] == "mac123"
            assert payload["preimage"] == "aa" * 32

    @pytest.mark.asyncio
    async def test_verify_without_macaroon_sends_preimage_only(self):
        """MPP flow: when macaroon is omitted, payload should only contain preimage."""
        mock_response = httpx.Response(
            200,
            json={"valid": True, "resource": "/api/mpp-data"},
            request=httpx.Request("POST", "https://api.lightningenable.com/api/l402/challenges/verify"),
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            async with L402ProducerClient(le_api_key="test-key") as client:
                result = await client.verify_payment(preimage="bb" * 32)

            assert result.success is True
            assert result.valid is True
            assert result.resource == "/api/mpp-data"
            call_kwargs = mock_post.call_args
            payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
            assert "macaroon" not in payload
            assert payload["preimage"] == "bb" * 32

    @pytest.mark.asyncio
    async def test_verify_without_macaroon_none_explicit(self):
        """Explicitly passing macaroon=None should omit it from payload."""
        mock_response = httpx.Response(
            200,
            json={"valid": False},
            request=httpx.Request("POST", "https://api.lightningenable.com/api/l402/challenges/verify"),
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            async with L402ProducerClient(le_api_key="test-key") as client:
                result = await client.verify_payment(None, preimage="cc" * 32)

            call_kwargs = mock_post.call_args
            payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
            assert "macaroon" not in payload

    @pytest.mark.asyncio
    async def test_verify_missing_preimage_raises_value_error(self):
        """Calling verify_payment() without preimage should raise ValueError."""
        async with L402ProducerClient(le_api_key="test-key") as client:
            with pytest.raises(ValueError, match="preimage is required"):
                await client.verify_payment("mac123")

    @pytest.mark.asyncio
    async def test_verify_no_args_raises_value_error(self):
        """Calling verify_payment() with no arguments should raise ValueError."""
        async with L402ProducerClient(le_api_key="test-key") as client:
            with pytest.raises(ValueError, match="preimage is required"):
                await client.verify_payment()

    @pytest.mark.asyncio
    async def test_verify_empty_macaroon_raises_value_error(self):
        """Passing an empty string as macaroon should raise ValueError, not silently switch to MPP."""
        async with L402ProducerClient(le_api_key="test-key") as client:
            with pytest.raises(ValueError, match="macaroon must be a non-empty string"):
                await client.verify_payment("", preimage="aa" * 32)

    @pytest.mark.asyncio
    async def test_verify_whitespace_macaroon_raises_value_error(self):
        """Passing whitespace-only macaroon should raise ValueError."""
        async with L402ProducerClient(le_api_key="test-key") as client:
            with pytest.raises(ValueError, match="macaroon must be a non-empty string"):
                await client.verify_payment("   ", preimage="aa" * 32)

    @pytest.mark.asyncio
    async def test_verify_positional_backward_compat(self):
        """Calling verify_payment(macaroon, preimage) positionally should still work."""
        mock_response = httpx.Response(
            200,
            json={"valid": True, "resource": "/api/data"},
            request=httpx.Request("POST", "https://api.lightningenable.com/api/l402/challenges/verify"),
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            async with L402ProducerClient(le_api_key="test-key") as client:
                result = await client.verify_payment("mac123", "aa" * 32)

            assert result.success is True
            assert result.valid is True
            call_kwargs = mock_post.call_args
            payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
            assert payload["macaroon"] == "mac123"
            assert payload["preimage"] == "aa" * 32

    @pytest.mark.asyncio
    async def test_verify_api_error_returns_failure(self):
        """Non-200 response should return a failure result."""
        mock_response = httpx.Response(
            401,
            json={"error": "Invalid API key"},
            request=httpx.Request("POST", "https://api.lightningenable.com/api/l402/challenges/verify"),
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
            async with L402ProducerClient(le_api_key="bad-key") as client:
                result = await client.verify_payment(preimage="dd" * 32)

            assert result.success is False
            assert "Invalid API key" in result.error

    @pytest.mark.asyncio
    async def test_verify_strips_whitespace(self):
        """Macaroon and preimage values should be stripped of whitespace."""
        mock_response = httpx.Response(
            200,
            json={"valid": True},
            request=httpx.Request("POST", "https://api.lightningenable.com/api/l402/challenges/verify"),
        )
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
            async with L402ProducerClient(le_api_key="test-key") as client:
                result = await client.verify_payment("  mac123  ", preimage="  " + "ee" * 32 + "  ")

            call_kwargs = mock_post.call_args
            payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
            assert payload["macaroon"] == "mac123"
            assert payload["preimage"] == "ee" * 32
