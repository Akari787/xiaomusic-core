from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from xiaomusic.core.models.media import MediaRequest, ResolvedMedia


class SourcePlugin(ABC):
    name = "base"

    def can_resolve(self, request: MediaRequest) -> bool:
        return False

    @abstractmethod
    async def resolve(self, request: MediaRequest) -> ResolvedMedia:
        raise NotImplementedError

    async def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def browse(self, path: str, page: int = 1, size: int = 50) -> list[dict[str, Any]]:
        raise NotImplementedError
