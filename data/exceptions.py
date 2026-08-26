"""Exceptions raised by the data provider clients (CoinMarketCap, Binance).

Kept as distinct types (rather than generic RuntimeError) so calling code
can decide per-failure-type what to do: retry, alert, or skip the symbol.
"""


class DataProviderError(Exception):
    """Base class for all data provider failures."""


class AuthenticationError(DataProviderError):
    """API key missing, invalid, or expired (HTTP 401/403)."""


class RateLimitError(DataProviderError):
    """Provider rate limit hit (HTTP 429). Includes retry_after if known."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class TransientProviderError(DataProviderError):
    """5xx / network-level failure that a retry may resolve."""


class SymbolNotFoundError(DataProviderError):
    """The requested trading pair doesn't exist on this provider."""
