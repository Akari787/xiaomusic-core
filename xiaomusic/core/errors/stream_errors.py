from xiaomusic.core.errors.base import CoreValidationError


class ExpiredStreamError(CoreValidationError):
    """Raised when a stream URL is expired or near expiry. Coordinator should retry resolve."""


class UndeliverableStreamError(CoreValidationError):
    """Raised when a stream URL is structurally undeliverable (bad scheme, etc). Do not retry."""
