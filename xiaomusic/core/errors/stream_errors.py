from xiaomusic.core.errors.base import CoreValidationError


class ExpiredStreamError(CoreValidationError):
    """Raised when a stream URL is expired or unsafe to dispatch."""
