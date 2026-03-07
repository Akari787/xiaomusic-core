from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ApiResponse(BaseModel):
    code: int
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    request_id: str
