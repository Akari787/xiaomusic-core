class SecurityError(Exception):
    """Base class for security-related errors."""


class ExecDisabledError(SecurityError):
    pass


class ExecNotAllowedError(SecurityError):
    pass


class ExecValidationError(SecurityError):
    pass


class OutboundBlockedError(SecurityError):
    pass


class SelfUpdateDisabledError(SecurityError):
    pass
