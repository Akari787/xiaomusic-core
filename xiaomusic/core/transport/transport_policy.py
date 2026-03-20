from __future__ import annotations


class TransportPolicy:
    """Action transport priorities used by router."""

    def __init__(self, action_map: dict[str, list[str]] | None = None) -> None:
        self._actions = action_map or {
            "play": ["mina"],
            "previous": ["miio", "mina"],
            "next": ["miio", "mina"],
            "tts": ["miio", "mina"],
            "volume": ["miio", "mina"],
            "stop": ["miio", "mina"],
            "pause": ["miio", "mina"],
            "probe": ["miio", "mina"],
        }

    def get(self, action: str) -> list[str]:
        return list(self._actions.get(action, []))
