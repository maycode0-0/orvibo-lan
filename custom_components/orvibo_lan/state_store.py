"""Versioned, source-aware device state storage."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .exceptions import StaleGenerationError, StateStoreError, UnknownDeviceError
from .models import DeviceSnapshot, StateSource, StateUpdate, immutable_value

_Path = tuple[str, ...]
_MISSING = object()


@dataclass(frozen=True, slots=True)
class _FieldVersion:
    source: StateSource
    monotonic: float
    generation: int


class StateStore:
    """Merge partial cloud and LAN state for an explicit device whitelist."""

    def __init__(self, device_ids: Iterable[str]) -> None:
        whitelist = frozenset(device_ids)
        if not whitelist or any(not isinstance(item, str) or not item for item in whitelist):
            raise StateStoreError("device whitelist must contain non-empty strings")
        self._device_ids = whitelist
        self._values: dict[str, dict[str, Any]] = {item: {} for item in whitelist}
        self._versions: dict[str, dict[_Path, _FieldVersion]] = {item: {} for item in whitelist}
        self._generations: dict[str, dict[StateSource, int]] = {item: {} for item in whitelist}

    @property
    def device_ids(self) -> frozenset[str]:
        """Return the immutable device whitelist."""

        return self._device_ids

    def add_devices(self, device_ids: Iterable[str]) -> None:
        """Extend the whitelist without losing existing field versions."""

        new_ids = frozenset(device_ids) - self._device_ids
        if any(not isinstance(item, str) or not item for item in new_ids):
            raise StateStoreError("device IDs must be non-empty strings")
        if not new_ids:
            return
        self._device_ids = self._device_ids | new_ids
        for device_id in new_ids:
            self._values[device_id] = {}
            self._versions[device_id] = {}
            self._generations[device_id] = {}

    def retain_devices(self, device_ids: Iterable[str]) -> None:
        """Remove devices absent from the latest authoritative snapshot."""

        retained = frozenset(device_ids)
        if any(not isinstance(item, str) or not item for item in retained):
            raise StateStoreError("device IDs must be non-empty strings")
        removed = self._device_ids - retained
        self._device_ids = self._device_ids & retained
        for device_id in removed:
            self._values.pop(device_id, None)
            self._versions.pop(device_id, None)
            self._generations.pop(device_id, None)

    def apply(self, update: StateUpdate) -> bool:
        """Merge accepted fields and report whether visible state changed."""

        self._validate_update(update)
        leaves = list(_iter_leaves(update.values))
        source_generations = self._generations[update.device_id]
        previous_generation = source_generations.get(update.source)
        if previous_generation is not None and update.generation < previous_generation:
            raise StaleGenerationError(
                f"{update.source.value} generation {update.generation} is older "
                f"than {previous_generation}"
            )
        source_generations[update.source] = update.generation
        if not leaves:
            return False
        changed = False
        versions = self._versions[update.device_id]
        state = self._values[update.device_id]
        incoming_version = _FieldVersion(update.source, update.monotonic, update.generation)
        for path, value in leaves:
            current_version = versions.get(path)
            if current_version is not None and not _is_newer(incoming_version, current_version):
                continue
            current = _get_path(state, path)
            detached = deepcopy(value)
            if current is _MISSING or current != detached:
                _set_path(state, path, detached)
                changed = True
            versions[path] = incoming_version
        return changed

    def snapshot(self, device_id: str) -> DeviceSnapshot:
        """Return a recursively immutable, detached snapshot for one device."""

        self._require_device(device_id)
        return DeviceSnapshot(
            device_id=device_id,
            values=immutable_value(self._values[device_id]),
        )

    def snapshot_all(self) -> Mapping[str, DeviceSnapshot]:
        """Return immutable snapshots for every whitelisted device."""

        return MappingProxyType(
            {device_id: self.snapshot(device_id) for device_id in self._device_ids}
        )

    def _validate_update(self, update: StateUpdate) -> None:
        if not isinstance(update, StateUpdate):
            raise StateStoreError("update must be a StateUpdate")
        self._require_device(update.device_id)
        if not isinstance(update.source, StateSource):
            raise StateStoreError("source must be a StateSource")
        if not isinstance(update.values, Mapping):
            raise StateStoreError("values must be a mapping")
        if (
            isinstance(update.generation, bool)
            or not isinstance(update.generation, int)
            or update.generation < 0
        ):
            raise StateStoreError("generation must be a non-negative integer")
        if (
            isinstance(update.monotonic, bool)
            or not isinstance(update.monotonic, (int, float))
            or not math.isfinite(update.monotonic)
            or update.monotonic < 0
        ):
            raise StateStoreError("monotonic must be a finite non-negative number")

    def _require_device(self, device_id: str) -> None:
        if device_id not in self._device_ids:
            raise UnknownDeviceError(device_id)


def _is_newer(incoming: _FieldVersion, current: _FieldVersion) -> bool:
    if incoming.source is not current.source:
        return incoming.source is StateSource.LAN
    if incoming.monotonic != current.monotonic:
        return incoming.monotonic > current.monotonic
    return incoming.generation >= current.generation


def _iter_leaves(values: Mapping[str, Any], prefix: _Path = ()) -> Iterable[tuple[_Path, Any]]:
    for key, value in values.items():
        if not isinstance(key, str) or not key:
            raise StateStoreError("state field names must be non-empty strings")
        path = prefix + (key,)
        if isinstance(value, Mapping):
            yield from _iter_leaves(value, path)
        else:
            yield path, value


def _get_path(state: dict[str, Any], path: _Path) -> Any:
    current: Any = state
    for segment in path:
        if not isinstance(current, dict) or segment not in current:
            return _MISSING
        current = current[segment]
    return current


def _set_path(state: dict[str, Any], path: _Path, value: Any) -> None:
    current = state
    for segment in path[:-1]:
        child = current.get(segment)
        if not isinstance(child, dict):
            child = {}
            current[segment] = child
        current = child
    current[path[-1]] = value
