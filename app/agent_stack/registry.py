from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass
class _BaseRegistry(Generic[T]):
    _items: dict[str, T] = field(default_factory=dict)

    def register(self, key: str, value: T) -> None:
        if key in self._items:
            raise ValueError(f"Duplicate registry key: {key}")
        self._items[key] = value

    def get(self, key: str) -> T:
        if key not in self._items:
            raise KeyError(f"Unknown registry key: {key}")
        return self._items[key]

    def has(self, key: str) -> bool:
        return key in self._items

    def items(self) -> Iterator[tuple[str, T]]:
        return iter(self._items.items())


class ToolRegistry(_BaseRegistry[T]):
    pass


class AgentRegistry(_BaseRegistry[T]):
    pass
