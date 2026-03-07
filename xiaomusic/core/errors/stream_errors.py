from xiaomusic.core.errors.base import CoreValidationError


class DeliveryPrepareError(CoreValidationError):
    """Base class for delivery prepare failures."""


class ExpiredStreamError(DeliveryPrepareError):
    """Raised when a stream URL is expired or near expiry. Coordinator should retry resolve."""


class UndeliverableStreamError(DeliveryPrepareError):
    """Raised when a stream URL is structurally undeliverable (bad scheme, etc). Do not retry."""
