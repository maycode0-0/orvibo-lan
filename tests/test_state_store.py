"""Pure unit tests for source-aware state merging."""

from types import MappingProxyType
from typing import Any, Mapping

import pytest

from custom_components.orvibo_lan.exceptions import (
    StaleGenerationError,
    StateStoreError,
    UnknownDeviceError,
)
from custom_components.orvibo_lan.models import StateSource, StateUpdate
from custom_components.orvibo_lan.state_store import StateStore


def update(
    values: Mapping[str, Any],
    *,
    source: StateSource = StateSource.CLOUD,
    monotonic: float = 1.0,
    generation: int = 1,
) -> StateUpdate:
    return StateUpdate("device-1", values, source, monotonic, generation)


def test_rejects_device_outside_whitelist() -> None:
    store = StateStore(["device-1"])

    with pytest.raises(UnknownDeviceError):
        store.apply(StateUpdate("other", {"online": True}, StateSource.LAN, 1, 1))


def test_merges_top_level_and_nested_fields() -> None:
    store = StateStore(["device-1"])
    store.apply(update({"online": True, "properties": {"onoff": 0, "level": 10}}))

    assert store.apply(
        update(
            {"properties": {"onoff": 1}},
            source=StateSource.LAN,
            monotonic=2,
        )
    )
    assert dict(store.snapshot("device-1").values["properties"]) == {
        "onoff": 1,
        "level": 10,
    }


def test_rejects_older_generation_for_same_source() -> None:
    store = StateStore(["device-1"])
    store.apply(update({"online": True}, generation=3))

    with pytest.raises(StaleGenerationError):
        store.apply(update({"online": False}, generation=2, monotonic=2))


def test_invalid_update_does_not_advance_generation() -> None:
    store = StateStore(["device-1"])

    with pytest.raises(StateStoreError):
        store.apply(update({"": True}, generation=3))
    assert store.apply(update({"online": True}, generation=1))


def test_older_cloud_cannot_roll_back_lan_field() -> None:
    store = StateStore(["device-1"])
    store.apply(
        update(
            {"properties": {"onoff": 1}},
            source=StateSource.LAN,
            monotonic=20,
        )
    )

    assert store.apply(update({"properties": {"onoff": 0}, "online": True}, monotonic=10))
    snapshot = store.snapshot("device-1").values
    assert snapshot["properties"]["onoff"] == 1
    assert snapshot["online"] is True


def test_cloud_can_update_unrelated_field_after_lan() -> None:
    store = StateStore(["device-1"])
    store.apply(
        update(
            {"properties": {"onoff": 1}},
            source=StateSource.LAN,
            monotonic=20,
        )
    )

    assert store.apply(update({"online": True}, monotonic=10))
    assert store.snapshot("device-1").values["online"] is True


def test_equal_timestamp_prefers_lan() -> None:
    store = StateStore(["device-1"])
    store.apply(update({"value1": 0}, monotonic=5))

    assert store.apply(update({"value1": 1}, source=StateSource.LAN, monotonic=5))
    assert not store.apply(update({"value1": 0}, monotonic=5, generation=2))
    assert store.snapshot("device-1").values["value1"] == 1


def test_snapshot_is_detached_and_recursively_immutable() -> None:
    source = {"properties": {"levels": [1, 2]}}
    store = StateStore(["device-1"])
    store.apply(update(source))
    source["properties"]["levels"].append(3)

    snapshot = store.snapshot("device-1")
    assert isinstance(snapshot.values, MappingProxyType)
    assert isinstance(snapshot.values["properties"], MappingProxyType)
    assert snapshot.values["properties"]["levels"] == (1, 2)
    with pytest.raises(TypeError):
        snapshot.values["online"] = True


def test_retain_devices_removes_state_and_versions() -> None:
    store = StateStore(["device-1", "device-2"])
    store.apply(update({"online": True}))

    store.retain_devices({"device-2"})

    assert store.device_ids == frozenset({"device-2"})
    with pytest.raises(UnknownDeviceError):
        store.snapshot("device-1")
