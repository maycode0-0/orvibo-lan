"""Coordinator production-path tests with dependency fakes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.orvibo_lan.coordinator import OrviboLanCoordinator
from custom_components.orvibo_lan.models import StateSource


class FakeHass:
    """Small Home Assistant task surface used by the coordinator."""

    def async_create_background_task(self, coroutine, name: str):
        del name
        import asyncio

        return asyncio.create_task(coroutine)


def cloud_result(
    *,
    status: dict[str, object] | None = None,
) -> tuple[
    list[dict[str, object]],
    dict[str, dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    dict[str, str],
    object,
]:
    device = {
        "deviceId": "device-1",
        "deviceType": 1,
        "uid": "gateway-1",
    }
    return (
        [device],
        {"device-1": status or {"deviceId": "device-1", "value1": 1}},
        [],
        [],
        {"gateway-1": "192.168.1.2"},
        object(),
    )


@pytest.mark.asyncio
async def test_cloud_and_lan_updates_share_state_store() -> None:
    coordinator = OrviboLanCoordinator(FakeHass(), MagicMock(), "user", "password")
    coordinator.cloud_client.fetch_devices = AsyncMock(return_value=cloud_result())

    await coordinator._refresh_devices_from_cloud()
    await coordinator._on_status_update(
        {"deviceId": "device-1", "value1": 0, "properties": {"level": 5}}
    )

    state = coordinator.get_device_state("device-1")
    assert state is not None
    assert state["value1"] == 0
    assert state["properties"] == {"level": 5}
    assert coordinator._state_store is not None
    snapshot = coordinator._state_store.snapshot("device-1")
    assert snapshot.values["value1"] == 0


@pytest.mark.asyncio
async def test_cloud_snapshot_removes_missing_device() -> None:
    coordinator = OrviboLanCoordinator(FakeHass(), MagicMock(), "user", "password")
    coordinator.cloud_client.fetch_devices = AsyncMock(return_value=cloud_result())
    await coordinator._refresh_devices_from_cloud()

    coordinator.cloud_client.fetch_devices = AsyncMock(return_value=([], {}, [], [], {}, object()))
    await coordinator._refresh_devices_from_cloud()

    assert coordinator.devices == {}
    assert coordinator.device_states == {}
    assert coordinator._state_store is None


@pytest.mark.asyncio
async def test_lan_push_notifies_listeners_once_after_debounce() -> None:
    coordinator = OrviboLanCoordinator(FakeHass(), MagicMock(), "user", "password")
    coordinator.cloud_client.fetch_devices = AsyncMock(return_value=cloud_result())
    await coordinator._refresh_devices_from_cloud()
    notifications = 0

    def listener() -> None:
        nonlocal notifications
        notifications += 1

    coordinator.async_add_listener(listener)
    await coordinator._on_status_update({"deviceId": "device-1", "value1": 0})
    await coordinator._notify_task

    assert notifications == 1


@pytest.mark.asyncio
async def test_older_cloud_snapshot_does_not_roll_back_lan() -> None:
    coordinator = OrviboLanCoordinator(FakeHass(), MagicMock(), "user", "password")
    coordinator.cloud_client.fetch_devices = AsyncMock(return_value=cloud_result())
    await coordinator._refresh_devices_from_cloud()
    await coordinator._on_status_update({"deviceId": "device-1", "value1": 0})

    coordinator.cloud_client.fetch_devices = AsyncMock(
        return_value=cloud_result(status={"deviceId": "device-1", "value1": 1})
    )
    await coordinator._refresh_devices_from_cloud()

    state = coordinator.get_device_state("device-1")
    assert state is not None
    assert state["value1"] == 0


@pytest.mark.asyncio
async def test_control_rejection_raises_home_assistant_error() -> None:
    coordinator = OrviboLanCoordinator(FakeHass(), MagicMock(), "user", "password")
    coordinator.devices["device-1"] = {
        "uid": "gateway-1",
        "_orvibo_read_only": False,
    }
    coordinator.gateway_manager = SimpleNamespace(send=AsyncMock(return_value={"status": 7}))

    with pytest.raises(Exception, match="rejected"):
        await coordinator.async_control_device("device-1", {"cmd": 15})


@pytest.mark.asyncio
async def test_cleanup_closes_manager_once_and_clears_state() -> None:
    coordinator = OrviboLanCoordinator(FakeHass(), MagicMock(), "user", "password")
    manager = SimpleNamespace(close=AsyncMock())
    coordinator.gateway_manager = manager
    coordinator.devices["device-1"] = {}

    await coordinator.async_cleanup()
    await coordinator.async_cleanup()

    manager.close.assert_awaited_once()
    assert coordinator.gateway_manager is None
    assert coordinator.devices == {}


def test_profile_source_marks_lan_state() -> None:
    """Keep the state source contract explicit for integration tests."""

    assert StateSource.LAN.value == "lan"


def test_availability_uses_gateway_for_lan_and_cloud_for_read_only() -> None:
    coordinator = OrviboLanCoordinator(FakeHass(), MagicMock(), "user", "password")
    coordinator.devices = {
        "lan": {"uid": "gateway-1", "_orvibo_read_only": False},
        "cloud": {"uid": "", "_orvibo_read_only": True},
    }
    coordinator.device_states = {
        "lan": {"online": True},
        "cloud": {"online": True},
    }
    coordinator.gateway_manager = SimpleNamespace(is_connected=lambda uid: uid == "gateway-1")
    coordinator.last_update_success = True

    assert coordinator.is_device_available("lan")
    assert coordinator.is_device_available("cloud")
    coordinator.last_update_success = False
    assert not coordinator.is_device_available("cloud")
    coordinator.device_states["lan"]["online"] = False
    assert not coordinator.is_device_available("lan")
