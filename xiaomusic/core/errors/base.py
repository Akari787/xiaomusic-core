class CoreError(Exception):
    """Base class for all core architecture errors."""


class CoreValidationError(CoreError):
    """Validation failure in core pipeline."""


class InvalidRequestError(CoreValidationError):
    """Raised when request payload is invalid for core operations."""
