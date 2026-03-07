from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """Normalized API exception carrying final response fields."""

    def __init__(
        self,
        *,
        code: int,
        message: str,
        data: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = int(code)
        self.message = str(message)
        self.data = dict(data or {})
        self.request_id = request_id
