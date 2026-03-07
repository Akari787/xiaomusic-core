from xiaomusic.core.errors.base import CoreError


class SourceResolveError(CoreError):
    """Raised when source plugin cannot resolve request."""
