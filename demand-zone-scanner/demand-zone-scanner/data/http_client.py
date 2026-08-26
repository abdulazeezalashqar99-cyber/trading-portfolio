"""
Generic retrying HTTP GET, shared by the CoinMarketCap and Binance
clients. Centralizing this means retry/backoff/rate-limit behavior is
implemented and tested once, not duplicated (and possibly inconsistently
re-implemented) per provider.

No external dependency (e.g. tenacity) - just requests + manual backoff.
Kept intentionally simple: exponential backoff with jitter, a small fixed
number of attempts, and explicit handling for the two response codes that
actually need different treatment (429 rate limit vs 5xx transient).
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Dict, Optional

import requests

from .exceptions import (
    AuthenticationError,
    RateLimitError,
    TransientProviderError,
)

logger = logging.getLogger(__name__)


def get_with_retry(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
    max_attempts: int = 4,
    base_backoff: float = 1.0,
) -> requests.Response:
    """
    GETs a URL with retry on transient failures.

    - 401/403 -> AuthenticationError, no retry (a bad key won't fix itself)
    - 429     -> RateLimitError if attempts exhausted, else sleeps and retries,
                 respecting a Retry-After header when the provider sends one
    - 5xx / network error -> retried with exponential backoff + jitter
    - other 4xx -> raised immediately via raise_for_status(), no retry
      (a malformed request won't fix itself either)
    """
    return _request_with_retry(
        lambda: requests.get(url, params=params, headers=headers, timeout=timeout),
        url, max_attempts, base_backoff,
    )


def post_with_retry(
    url: str,
    json: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
    max_attempts: int = 4,
    base_backoff: float = 1.0,
) -> requests.Response:
    """POSTs with the same retry/backoff/error semantics as get_with_retry."""
    return _request_with_retry(
        lambda: requests.post(url, json=json, data=data, headers=headers, timeout=timeout),
        url, max_attempts, base_backoff,
    )


def _request_with_retry(make_request, url: str, max_attempts: int, base_backoff: float) -> requests.Response:
    last_exception: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = make_request()
        except requests.exceptions.RequestException as e:
            last_exception = e
            if attempt == max_attempts:
                raise TransientProviderError(
                    f"Network error calling {url} after {max_attempts} attempts: {e}"
                ) from e
            _sleep_backoff(attempt, base_backoff)
            continue

        if response.status_code in (401, 403):
            raise AuthenticationError(
                f"Authentication failed calling {url} (HTTP {response.status_code}). "
                "Check that the API key/token is set and valid."
            )

        if response.status_code == 429:
            retry_after = _parse_retry_after(response)
            if attempt == max_attempts:
                raise RateLimitError(
                    f"Rate limited calling {url} after {max_attempts} attempts",
                    retry_after=retry_after,
                )
            _sleep_backoff(attempt, base_backoff, floor=retry_after)
            continue

        if 500 <= response.status_code < 600:
            last_exception = TransientProviderError(
                f"Server error {response.status_code} calling {url}"
            )
            if attempt == max_attempts:
                raise last_exception
            _sleep_backoff(attempt, base_backoff)
            continue

        response.raise_for_status()
        return response

    # unreachable in practice, but keeps type-checkers happy
    raise last_exception or TransientProviderError(f"Failed calling {url}")


def _parse_retry_after(response: requests.Response) -> Optional[float]:
    header = response.headers.get("Retry-After")
    if header is None:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def _sleep_backoff(attempt: int, base: float, floor: Optional[float] = None) -> None:
    backoff = base * (2 ** (attempt - 1))
    backoff += random.uniform(0, base)  # jitter, avoids thundering-herd retries
    if floor is not None:
        backoff = max(backoff, floor)
    logger.info("Retrying after %.1fs (attempt %d)", backoff, attempt)
    time.sleep(backoff)
