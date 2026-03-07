from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from xiaomusic.core.models.media import PreparedStream


class Transport(ABC):
    name = "base"

    @abstractmethod
    async def play_url(self, device_id: str, prepared: PreparedStream) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def stop(self, device_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def pause(self, device_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def tts(self, device_id: str, text: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def set_volume(self, device_id: str, volume: int) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def probe(self, device_id: str) -> dict[str, Any]:
        raise NotImplementedError
