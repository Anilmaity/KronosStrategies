class OandaTickError(Exception):
    """Base exception for oanda_tick_lib."""


class OandaAuthError(OandaTickError):
    """API key rejected (HTTP 401/403). Do not retry — fix credentials."""


class OandaAPIError(OandaTickError):
    """Non-retryable or exhausted-retry OANDA API error."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        super().__init__(f"OANDA API {status_code}: {body}")


class CacheReadError(OandaTickError):
    """Cache file exists but cannot be read or parsed."""
