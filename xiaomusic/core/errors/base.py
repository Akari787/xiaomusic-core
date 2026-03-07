class CoreError(Exception):
    """Base class for all core architecture errors."""


class CoreValidationError(CoreError):
    """Validation failure in core pipeline."""
