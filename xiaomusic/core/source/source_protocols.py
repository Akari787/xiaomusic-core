"""Source-layer protocol definitions (anti-corruption layer).

These protocols decouple adapters from infrastructure details
without introducing hard dependencies on relay / runtime internals.
"""

from __future__ import annotations

from typing import Protocol

from typing_extensions import runtime_checkable


@runtime_checkable
class LinkPreparer(Protocol):
    """播放准备服务接口（防腐层）

    Source 插件通过此接口请求播放准备，而不直接持有 RelayRuntime 引用。
    RelayRuntime 实现此协议以提供实际的 prepare_link 行为。
    """

    def prepare_link(
        self,
        url: str,
        prefer_proxy: bool = False,
        *,
        no_cache: bool = False,
    ) -> dict[str, object]:
        """准备媒体链接

        Args:
            url: 媒体 URL
            prefer_proxy: 是否优先使用代理
            no_cache: 是否跳过缓存

        Returns:
            dict，包含 stream_url/session 等字段
        """
        ...
