from xiaomusic.core.errors.base import CoreError


class DeviceNotFoundError(CoreError):
    """Raised when target device is missing from registry."""
