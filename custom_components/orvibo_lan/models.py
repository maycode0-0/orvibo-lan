"""Shared immutable models for protocol and state handling."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class StateSource(str, Enum):
    """Origin of a device-state update."""

    CLOUD = "cloud"
    LAN = "lan"


@dataclass(frozen=True, slots=True)
class DecodedPacket:
    """Validated packet contents and routing metadata."""

    packet_type: bytes
    session_id: bytes
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class StateUpdate:
    """A versioned partial state update for one whitelisted device."""

    device_id: str
    values: Mapping[str, Any]
    source: StateSource
    monotonic: float
    generation: int


@dataclass(frozen=True, slots=True)
class DeviceSnapshot:
    """Immutable public view of one device's merged state."""

    device_id: str
    values: Mapping[str, Any]


def immutable_value(value: Any) -> Any:
    """Recursively detach and freeze a value for a public snapshot."""

    if isinstance(value, Mapping):
        return MappingProxyType({key: immutable_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(immutable_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(immutable_value(item) for item in value)
    return value
