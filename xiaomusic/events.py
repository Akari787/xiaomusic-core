"""事件系统模块

提供简单的事件发布-订阅机制，用于模块间的解耦通信。
"""

import asyncio
import inspect
from collections.abc import Callable

# 事件类型常量
CONFIG_CHANGED = "config_changed"
DEVICE_CONFIG_CHANGED = "device_config_changed"
PLAYER_STATE_CHANGED = "player_state_changed"


class EventBus:
    """事件总线类

    实现简单的发布-订阅模式，支持同步和异步回调的发布。
    """

    def __init__(self):
        """初始化事件总线"""
        self._subscribers: dict[str, list[Callable]] = {}

    def subscribe(self, event_type: str, callback: Callable) -> None:
        """订阅事件

        Args:
            event_type: 事件类型
            callback: 回调函数（同步或异步）
        """
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        if callback not in self._subscribers[event_type]:
            self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: Callable) -> None:
        """取消订阅事件

        Args:
            event_type: 事件类型
            callback: 回调函数
        """
        if event_type in self._subscribers:
            if callback in self._subscribers[event_type]:
                self._subscribers[event_type].remove(callback)

    def publish(self, event_type: str, **kwargs) -> None:
        """发布事件

        Args:
            event_type: 事件类型
            **kwargs: 事件参数

        支持同步和异步回调。异步回调会被安全调度执行。
        """
        if event_type not in self._subscribers:
            return
        for callback in self._subscribers[event_type]:
            try:
                result = callback(**kwargs)
                if result is not None and inspect.isawaitable(result):
                    self._schedule_async_callback(result)
            except Exception as e:
                print(f"Error in event callback for {event_type}: {e}")

    def _schedule_async_callback(self, coro) -> None:
        """安全调度异步回调"""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
        except RuntimeError:
            try:
                asyncio.run(coro)
            except Exception:
                pass
