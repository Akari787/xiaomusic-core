from __future__ import annotations


class TransportPolicy:
    """Action transport priorities used by router."""

    def __init__(self, action_map: dict[str, list[str]] | None = None) -> None:
        self._actions = action_map or {
            "play": ["mina"],
            "previous": ["mina"],
            "next": ["mina"],
            "volume": ["mina"],
            "stop": ["mina"],
            "pause": ["mina"],
            "resume": ["mina"],
            "probe": ["mina"],
        }

    def get(self, action: str) -> list[str]:
        return list(self._actions.get(action, []))
