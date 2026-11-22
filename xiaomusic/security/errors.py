class SecurityError(Exception):
    """Base class for security-related errors."""


class OutboundBlockedError(SecurityError):
    pass


class SelfUpdateDisabledError(SecurityError):
    pass
