"""L402 HTTP client for agent service settlement.

Wraps httpx with automatic L402 challenge handling. If `l402-requests` is
installed, delegates to its AsyncL402Client for full wallet integration.
Otherwise provides a basic implementation that extracts challenges.

Also provides Producer API methods for agents acting as service providers:
- create_challenge: Create an L402 invoice+macaroon for a requester to pay
- verify_payment: Verify an L402 token to confirm payment before delivering service
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class L402Challenge:
    """Parsed L402 challenge from a WWW-Authenticate header."""

    macaroon: str
    invoice: str

    @property
    def authorization_header(self) -> str:
        """Format as L402 Authorization header value (needs preimage appended)."""
        return f"L402 {self.macaroon}"


@dataclass(frozen=True)
class MppChallenge:
    """MPP challenge from Payment WWW-Authenticate header."""

    invoice: str
    amount: Optional[str] = None
    realm: Optional[str] = None


# Pattern for parsing L402/LSAT challenges
_CHALLENGE_RE = re.compile(
    r'(?:L402|LSAT)\s+'
    r'macaroon\s*=\s*"?(?P<macaroon>[^",\s]+)"?\s*,\s*'
    r'invoice\s*=\s*"?(?P<invoice>[^",\s]+)"?',
    re.IGNORECASE,
)

# Patterns for parsing MPP (Machine Payments Protocol) challenges
# _AUTH_SCHEME_SPLIT splits a WWW-Authenticate value into individual challenges
# by detecting auth-scheme token boundaries (e.g. "Bearer ...", "Payment ...").
_AUTH_SCHEME_SPLIT = re.compile(
    r'(?:^|,\s*)(?=[A-Za-z][A-Za-z0-9!#$&\-^_`|~]*\s)',
)
# Match parameters inside a Payment challenge's parameter list, ensuring we
# only match full parameter names at proper boundaries (start-of-string,
# whitespace, or comma — covers the full RFC 7230 tchar set).
# Also supports both quoted and unquoted (bare token) auth-param values
# per HTTP auth header grammar.
_MPP_INVOICE_RE = re.compile(
    r'(?:^|[\s,])invoice\s*=\s*"?(?P<invoice>[^",\s]+)"?',
    re.IGNORECASE,
)
_MPP_METHOD_RE = re.compile(
    r'(?:^|[\s,])method\s*=\s*"?lightning"?(?=$|[\s,])',
    re.IGNORECASE,
)
_MPP_AMOUNT_RE = re.compile(
    r'(?:^|[\s,])amount\s*=\s*"?(?P<amount>[^",\s]+)"?',
    re.IGNORECASE,
)
_MPP_REALM_RE = re.compile(
    r'(?:^|[\s,])realm\s*=\s*"?(?P<realm>[^",\s]+)"?',
    re.IGNORECASE,
)


def parse_l402_challenge(headers: dict[str, str]) -> Optional[L402Challenge]:
    """Extract an L402 challenge from response headers.

    Args:
        headers: HTTP response headers dict.

    Returns:
        Parsed challenge or None.
    """
    lower_headers = {k.lower(): v for k, v in headers.items()}
    www_auth = lower_headers.get("www-authenticate", "")
    if not www_auth:
        return None

    match = _CHALLENGE_RE.search(www_auth)
    if not match:
        return None

    return L402Challenge(
        macaroon=match.group("macaroon").strip(),
        invoice=match.group("invoice").strip(),
    )


def _extract_payment_segment(header: str) -> Optional[str]:
    """Extract only the Payment challenge segment from a WWW-Authenticate value.

    Splits the header at auth-scheme boundaries so that parameters from
    other schemes (e.g. Bearer realm=...) are never included.
    """
    # Split into individual challenge segments at auth-scheme boundaries
    segments = _AUTH_SCHEME_SPLIT.split(header)
    for segment in segments:
        stripped = segment.strip().rstrip(",").strip()
        if stripped.upper().startswith("PAYMENT "):
            return stripped
    return None


def parse_mpp_challenge(header: str) -> MppChallenge:
    """Parse a Payment (MPP) challenge from a WWW-Authenticate header value.

    Args:
        header: The WWW-Authenticate header value string.

    Returns:
        Parsed MppChallenge.

    Raises:
        ValueError: If the header is not a valid MPP challenge.
    """
    payment_segment = _extract_payment_segment(header)
    if payment_segment is None:
        raise ValueError(f"Invalid MPP challenge: {header[:80]}")

    # Verify method="lightning" within the Payment segment
    if not _MPP_METHOD_RE.search(payment_segment):
        raise ValueError(f"Invalid MPP challenge: {header[:80]}")

    invoice_match = _MPP_INVOICE_RE.search(payment_segment)
    if not invoice_match:
        raise ValueError(f"Invalid MPP challenge: {header[:80]}")

    invoice = invoice_match.group("invoice").strip()
    amount_match = _MPP_AMOUNT_RE.search(payment_segment)
    realm_match = _MPP_REALM_RE.search(payment_segment)

    return MppChallenge(
        invoice=invoice,
        amount=amount_match.group("amount").strip() if amount_match else None,
        realm=realm_match.group("realm").strip() if realm_match else None,
    )


def parse_payment_challenge(
    headers: dict[str, str],
) -> L402Challenge | MppChallenge:
    """Parse WWW-Authenticate headers, trying L402 first then MPP.

    Prefers L402 when available; falls back to MPP (Machine Payments Protocol).

    Args:
        headers: HTTP response headers dict.

    Returns:
        Parsed L402Challenge or MppChallenge.

    Raises:
        ValueError: If no valid L402 or MPP challenge is found.
    """
    lower_headers = {k.lower(): v for k, v in headers.items()}
    if "www-authenticate" not in lower_headers:
        raise ValueError("No WWW-Authenticate header found")
    www_auth = lower_headers["www-authenticate"]
    if not www_auth:
        raise ValueError("Empty WWW-Authenticate header")

    # Try L402 first (preferred)
    l402 = parse_l402_challenge(headers)
    if l402 is not None:
        return l402

    # Try MPP fallback
    try:
        return parse_mpp_challenge(www_auth)
    except ValueError:
        pass

    raise ValueError(f"No valid L402 or MPP challenge: {www_auth[:80]}")


class L402Client:
    """Async HTTP client with L402 payment support.

    For full auto-payment, configure with a wallet callback. Otherwise,
    challenges are returned for external payment handling.
    """

    def __init__(
        self,
        pay_invoice_callback: Optional[Any] = None,
        preimage_cache: Optional[dict[str, str]] = None,
        max_amount_sats: Optional[int] = None,
        **httpx_kwargs: Any,
    ) -> None:
        """
        Args:
            pay_invoice_callback: Async callable(invoice: str) -> preimage: str.
                If provided, invoices are paid automatically.
            preimage_cache: Optional dict mapping macaroon -> preimage for reuse.
            max_amount_sats: Maximum payment amount in satoshis. If an invoice
                exceeds this limit, the payment is rejected. None means no limit.
            **httpx_kwargs: Additional kwargs passed to httpx.AsyncClient.
        """
        self._pay_callback = pay_invoice_callback
        self._cache: dict[str, str] = preimage_cache or {}
        self._max_amount_sats = max_amount_sats
        self._httpx_kwargs = httpx_kwargs
        self._client: Optional[httpx.AsyncClient] = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(**self._httpx_kwargs)
        return self._client

    @staticmethod
    def _decode_invoice_amount_sats(invoice: str) -> Optional[int]:
        """Extract the amount in satoshis from a BOLT-11 invoice string.

        Returns None if the amount cannot be parsed.
        """
        # BOLT-11: starts with "ln" then network (bc/tb/etc), then optional amount
        # Amount is encoded as: <number><multiplier> where multipliers are:
        # m=milli, u=micro, n=nano, p=pico (of BTC)
        inv_lower = invoice.lower()
        # Strip "lightning:" prefix if present
        if inv_lower.startswith("lightning:"):
            inv_lower = inv_lower[10:]

        match = re.match(r"ln\w+?(\d+)([munp])1", inv_lower)
        if not match:
            return None

        amount_num = int(match.group(1))
        multiplier = match.group(2)
        # Convert to satoshis (1 BTC = 100_000_000 sats)
        multiplier_map = {
            "m": 100_000_00,     # milli-BTC = 100,000 sats (0.001 BTC)
            "u": 100_00,         # micro-BTC = 100 sats (0.000001 BTC)
            "n": 0.01,           # nano-BTC = 0.01 sats
            "p": 0.00001,        # pico-BTC = 0.00001 sats
        }
        # milli = 10^-3 BTC = 10^5 sats
        btc_multipliers = {"m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12}
        btc_amount = amount_num * btc_multipliers[multiplier]
        sats = int(btc_amount * 1e8)
        return sats

    @staticmethod
    def _validate_preimage(preimage: str) -> bool:
        """Validate that a preimage is a 64-character hex string."""
        if not isinstance(preimage, str) or len(preimage) != 64:
            return False
        try:
            bytes.fromhex(preimage)
            return True
        except ValueError:
            return False

    async def access(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[dict[str, str]] = None,
        max_amount_sats: Optional[int] = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Access an L402-protected resource.

        If a cached credential exists, it is used. If a 402 is received and
        a pay callback is configured, the invoice is paid and the request retried.

        Args:
            url: Target URL.
            method: HTTP method.
            headers: Optional request headers.
            max_amount_sats: Override max payment amount for this request.
                Falls back to the instance-level max_amount_sats.
            **kwargs: Additional httpx request kwargs.

        Returns:
            The HTTP response (either direct or after L402 payment).

        Raises:
            ValueError: If the invoice amount exceeds max_amount_sats.
        """
        headers = dict(headers or {})
        client = self._ensure_client()

        response = await client.request(method, url, headers=headers, **kwargs)

        if response.status_code != 402:
            return response

        # Try L402 first, then MPP fallback
        resp_headers = dict(response.headers)
        try:
            challenge = parse_payment_challenge(resp_headers)
        except ValueError:
            return response

        if self._pay_callback is None:
            # No auto-pay; return the 402 so caller can handle it
            return response

        # Check invoice amount against limit
        effective_max = max_amount_sats if max_amount_sats is not None else self._max_amount_sats
        if effective_max is not None:
            invoice_sats = self._decode_invoice_amount_sats(challenge.invoice)
            if invoice_sats is not None and invoice_sats > effective_max:
                raise ValueError(
                    f"Invoice amount ({invoice_sats} sats) exceeds maximum allowed "
                    f"({effective_max} sats). Invoice: {challenge.invoice[:40]}..."
                )

        # Pay the invoice with error handling on the callback
        try:
            preimage = await self._pay_callback(challenge.invoice)
        except Exception as exc:
            logger.error("pay_invoice_callback failed: %s", exc)
            raise RuntimeError(f"Payment callback failed: {exc}") from exc

        # Validate preimage format
        if not self._validate_preimage(preimage):
            logger.error(
                "Invalid preimage returned from pay callback: expected 64-char hex, "
                "got %r (length=%d)",
                preimage[:20] if isinstance(preimage, str) else type(preimage),
                len(preimage) if isinstance(preimage, str) else 0,
            )
            raise ValueError(
                f"Invalid preimage from payment callback: expected 64-character hex string, "
                f"got length {len(preimage) if isinstance(preimage, str) else 'N/A'}"
            )

        # Build the correct Authorization header based on challenge type
        if isinstance(challenge, MppChallenge):
            auth_header = f'Payment method="lightning", preimage="{preimage}"'
            logger.info("MPP payment succeeded for %s", url)
            logger.debug("MPP preimage (first 8 chars): %.8s...", preimage)
        else:
            self._cache[challenge.macaroon] = preimage
            auth_header = f"L402 {challenge.macaroon}:{preimage}"
            logger.info("L402 payment succeeded for %s", url)
            logger.debug("L402 preimage (first 8 chars): %.8s...", preimage)

        # Retry the request with credentials, with retry+backoff
        headers["Authorization"] = auth_header
        max_retries = 3
        last_exc: Optional[Exception] = None

        for attempt in range(max_retries):
            try:
                retry_response = await client.request(method, url, headers=headers, **kwargs)
                return retry_response
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Authenticated retry attempt %d/%d failed: %s. "
                    "Preimage prefix for recovery: %.8s...",
                    attempt + 1, max_retries, exc, preimage,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (2 ** attempt))

        # All retries exhausted — log preimage prefix for recovery identification
        logger.error(
            "All %d authenticated retries failed after payment. "
            "Preimage prefix for recovery: %.8s... (full value never logged for security)",
            max_retries, preimage,
        )
        raise RuntimeError(
            f"Payment succeeded (preimage prefix: {preimage[:8]}...) but all {max_retries} "
            f"authenticated retries failed: {last_exc}"
        )

    async def pay_and_access(
        self,
        url: str,
        pay_invoice_callback: Any,
        method: str = "GET",
        headers: Optional[dict[str, str]] = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Full L402 flow: request, get 402, pay invoice, retry with token.

        Args:
            url: Target URL.
            pay_invoice_callback: Async callable(invoice: str) -> preimage: str.
            method: HTTP method.
            headers: Optional request headers.
            **kwargs: Additional httpx request kwargs.

        Returns:
            The final HTTP response after payment.
        """
        headers = dict(headers or {})
        client = self._ensure_client()

        response = await client.request(method, url, headers=headers, **kwargs)

        if response.status_code != 402:
            return response

        # Try L402 first, then MPP fallback
        resp_headers = dict(response.headers)
        try:
            challenge = parse_payment_challenge(resp_headers)
        except ValueError:
            return response

        try:
            preimage = await pay_invoice_callback(challenge.invoice)
        except Exception as exc:
            logger.error(
                "Error in pay_invoice_callback during pay_and_access for URL %r: %s",
                url,
                exc,
                exc_info=True,
            )
            raise RuntimeError(
                "Payment callback failed during pay_and_access; see logs for details"
            ) from exc

        # Validate preimage format before constructing credentials
        if not self._validate_preimage(preimage):
            logger.error(
                "Invalid preimage returned from pay callback in pay_and_access: "
                "expected 64-char hex, got %r (length=%d)",
                preimage[:20] if isinstance(preimage, str) else type(preimage),
                len(preimage) if isinstance(preimage, str) else 0,
            )
            raise ValueError(
                f"Invalid preimage from payment callback: expected 64-character hex string, "
                f"got length {len(preimage) if isinstance(preimage, str) else 'N/A'}"
            )

        # Build the correct Authorization header based on challenge type
        if isinstance(challenge, MppChallenge):
            headers["Authorization"] = f'Payment method="lightning", preimage="{preimage}"'
        else:
            self._cache[challenge.macaroon] = preimage
            headers["Authorization"] = f"L402 {challenge.macaroon}:{preimage}"

        retry_response = await client.request(method, url, headers=headers, **kwargs)
        return retry_response

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> L402Client:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


@dataclass(frozen=True)
class L402ChallengeResponse:
    """Response from the Lightning Enable Producer API create_challenge endpoint."""

    success: bool
    invoice: Optional[str] = None
    macaroon: Optional[str] = None
    payment_hash: Optional[str] = None
    expires_at: Optional[str] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class L402VerifyResponse:
    """Response from the Lightning Enable Producer API verify endpoint."""

    success: bool
    valid: bool = False
    resource: Optional[str] = None
    error: Optional[str] = None


class L402ProducerClient:
    """Client for the Lightning Enable Producer API.

    Enables agents to act as service providers by creating L402 challenges
    (invoices) and verifying payments. This is the provider/seller side
    of the L402 protocol.

    Requires a Lightning Enable API key with an Agentic Commerce subscription.
    """

    def __init__(
        self,
        le_api_key: str,
        le_api_base_url: str = "https://api.lightningenable.com",
        **httpx_kwargs: Any,
    ) -> None:
        """
        Args:
            le_api_key: Lightning Enable merchant API key (X-Api-Key header).
            le_api_base_url: Base URL for the Lightning Enable API.
            **httpx_kwargs: Additional kwargs passed to httpx.AsyncClient.
        """
        self._api_key = le_api_key
        self._base_url = le_api_base_url.rstrip("/")
        self._httpx_kwargs = httpx_kwargs
        self._client: Optional[httpx.AsyncClient] = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {
                "X-Api-Key": self._api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "LE-Agent-SDK-Python/0.1.0",
            }
            self._client = httpx.AsyncClient(headers=headers, **self._httpx_kwargs)
        return self._client

    async def create_challenge(
        self,
        resource: str,
        price_sats: int,
        description: Optional[str] = None,
    ) -> L402ChallengeResponse:
        """Create an L402 challenge (Lightning invoice + macaroon) for a resource.

        The provider calls this to generate an invoice at the negotiated price.
        The resulting invoice and macaroon are shared with the requester (e.g., via
        Nostr DM or in the agreement event) for payment.

        Args:
            resource: Resource identifier (URL, service name, or description).
            price_sats: Price in satoshis to charge.
            description: Optional description shown on the Lightning invoice.

        Returns:
            L402ChallengeResponse with invoice, macaroon, and payment_hash.
        """
        if price_sats <= 0:
            return L402ChallengeResponse(
                success=False,
                error="Price must be greater than 0 sats",
            )

        client = self._ensure_client()
        body = {"resource": resource, "priceSats": price_sats}
        if description:
            body["description"] = description

        try:
            response = await client.post(
                f"{self._base_url}/api/l402/challenges",
                json=body,
            )

            if response.status_code != 200:
                error_msg = f"API returned {response.status_code}"
                try:
                    data = response.json()
                    error_msg = data.get("message") or data.get("error") or error_msg
                except Exception:
                    pass
                return L402ChallengeResponse(success=False, error=error_msg)

            data = response.json()
            return L402ChallengeResponse(
                success=True,
                invoice=data.get("invoice"),
                macaroon=data.get("macaroon"),
                payment_hash=data.get("paymentHash"),
                expires_at=data.get("expiresAt"),
            )
        except httpx.TimeoutException:
            return L402ChallengeResponse(success=False, error="Request timed out")
        except httpx.HTTPError as exc:
            return L402ChallengeResponse(success=False, error=f"HTTP error: {exc}")

    async def verify_payment(
        self,
        macaroon: Optional[str] = None,
        preimage: Optional[str] = None,
    ) -> L402VerifyResponse:
        """Verify an L402 or MPP token to confirm payment.

        For L402 verification, provide both macaroon and preimage.
        For MPP verification, only the preimage is required (macaroon is None).

        The provider calls this after receiving a token from the requester
        to validate that the invoice has been paid before delivering the service.

        Args:
            macaroon: Base64-encoded macaroon from the L402 token. Optional for
                MPP payments where only a preimage is provided. Pass None to use
                MPP verification without a macaroon.
            preimage: Hex-encoded preimage (proof of payment). Required.

        Returns:
            L402VerifyResponse indicating whether the payment is valid.

        Raises:
            ValueError: If preimage is not provided, or if macaroon is provided
                but empty/whitespace.
        """
        if not preimage or not isinstance(preimage, str) or not preimage.strip():
            raise ValueError(
                "preimage is required; pass a non-empty hex-encoded preimage string"
            )

        client = self._ensure_client()

        payload: dict[str, str] = {"preimage": preimage.strip()}

        # Distinguish MPP (macaroon is None) from an explicitly provided but
        # empty/whitespace macaroon, which should be treated as an error.
        if macaroon is not None:
            macaroon_stripped = macaroon.strip()
            if not macaroon_stripped:
                raise ValueError(
                    "macaroon must be a non-empty string when provided; "
                    "use None to request MPP verification without a macaroon."
                )
            payload["macaroon"] = macaroon_stripped

        try:
            response = await client.post(
                f"{self._base_url}/api/l402/challenges/verify",
                json=payload,
            )

            if response.status_code != 200:
                error_msg = f"API returned {response.status_code}"
                try:
                    data = response.json()
                    error_msg = data.get("message") or data.get("error") or error_msg
                except Exception:
                    pass
                return L402VerifyResponse(success=False, error=error_msg)

            data = response.json()
            return L402VerifyResponse(
                success=True,
                valid=data.get("valid", False),
                resource=data.get("resource"),
            )
        except httpx.TimeoutException:
            return L402VerifyResponse(success=False, error="Request timed out")
        except httpx.HTTPError as exc:
            return L402VerifyResponse(success=False, error=f"HTTP error: {exc}")

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> L402ProducerClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
