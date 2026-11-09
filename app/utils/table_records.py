"""Compact row DTO shared by read-only desktop tables."""

from dataclasses import dataclass, field
from typing import Any, Hashable, Mapping


@dataclass(frozen=True)
class TableRecord:
    key: Hashable
    values: tuple[str, ...]
    roles: Mapping[int, Mapping[int, Any]] = field(default_factory=dict)
