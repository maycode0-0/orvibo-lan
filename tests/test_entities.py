"""Production-path tests for Home Assistant entity adapters."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.orvibo_lan import (
    OrviboLanRuntimeData,
    binary_sensor,
    climate,
    cover,
    fan,
    light,
    sensor,
)
from custom_components.orvibo_lan.const import DOMAIN
from custom_components.orvibo_lan.coordinator import OrviboLanCoordinator


def entity_context():
    coordinator = OrviboLanCoordinator(
        SimpleNamespace(),
        MagicMock(),
        "user",
        "password",
    )
    coordinator.devices = {
        "light": {
            "deviceId": "light",
            "deviceType": 503,
            "uid": "gateway",
            "deviceName": "Desk light",
            "_orvibo_lan_capable": True,
            "_orvibo_read_only": False,
        },
        "cover": {
            "deviceId": "cover",
            "deviceType": 34,
            "uid": "gateway",
            "_orvibo_lan_capable": True,
            "_orvibo_read_only": False,
        },
        "fan": {
            "deviceId": "fan",
            "deviceType": 516,
            "uid": "gateway",
            "_orvibo_lan_capable": True,
            "_orvibo_read_only": False,
        },
        "climate": {
            "deviceId": "climate",
            "deviceType": 36,
            "uid": "gateway",
            "_orvibo_lan_capable": True,
            "_orvibo_read_only": False,
        },
        "environment": {
            "deviceId": "environment",
            "deviceType": 22,
            "uid": "gateway",
            "_orvibo_lan_capable": True,
            "_orvibo_read_only": False,
        },
        "door": {
            "deviceId": "door",
            "deviceType": 46,
            "uid": "gateway",
            "_orvibo_lan_capable": True,
            "_orvibo_read_only": False,
        },
    }
    coordinator.device_types = {
        device_id: int(device["deviceType"]) for device_id, device in coordinator.devices.items()
    }
    coordinator.device_states = {
        "light": {
            "online": True,
            "properties": {
                "onoff": {"status": "on"},
                "brightness": {"percent": 50},
                "colorTemp": {"value": 4000},
            },
        },
        "cover": {"online": True, "value1": 40},
        "fan": {
            "online": True,
            "properties": {
                "onoff": {"status": "on"},
                "fanSpeed": {"value": 75},
            },
        },
        "climate": {
            "online": True,
            "value1": 0,
            "value2": 3,
            "value3": 2,
            "value4": (2200 << 16) | 2150,
        },
        "environment": {
            "online": True,
            "temperature": 21.5,
            "humidity": 48,
            "battery": 87,
        },
        "door": {
            "online": True,
            "door_state": True,
            "battery": 70,
        },
    }
    coordinator.gateway_manager = SimpleNamespace(is_connected=lambda uid: uid == "gateway")
    coordinator.last_update_success = True
    coordinator.async_control_device = AsyncMock(return_value=True)
    runtime = OrviboLanRuntimeData(coordinator, MagicMock())
    entry = SimpleNamespace(entry_id="entry", options={})
    hass = SimpleNamespace(data={DOMAIN: {"entry": runtime}})
    return hass, entry, coordinator


@pytest.mark.asyncio
async def test_platform_entities_parse_state_and_send_commands() -> None:
    hass, entry, coordinator = entity_context()
    platform_entities: dict[str, list[object]] = {}

    for name, platform in (
        ("light", light),
        ("cover", cover),
        ("fan", fan),
        ("climate", climate),
        ("sensor", sensor),
        ("binary_sensor", binary_sensor),
    ):
        entities: list[object] = []
        await platform.async_setup_entry(hass, entry, entities.extend)
        platform_entities[name] = entities

    light_entity = platform_entities["light"][0]
    assert light_entity.available
    assert light_entity.is_on
    assert light_entity.brightness == 127
    assert light_entity.color_temp_kelvin == 4000
    await light_entity.async_turn_off()

    cover_entity = platform_entities["cover"][0]
    assert cover_entity.current_cover_position == 40
    assert cover_entity.is_closed is False
    await cover_entity.async_set_cover_position(position=75)

    fan_entity = platform_entities["fan"][0]
    assert fan_entity.is_on
    assert fan_entity.percentage == 75
    await fan_entity.async_set_percentage(33)

    climate_entity = platform_entities["climate"][0]
    assert climate_entity.hvac_mode == "cool"
    assert climate_entity.target_temperature == 22
    assert climate_entity.current_temperature == 21.5
    assert climate_entity.fan_mode == "medium"
    await climate_entity.async_set_temperature(temperature=23)

    sensor_values = {
        entity._attr_unique_id: entity.native_value
        for entity in platform_entities["sensor"]
        if entity._device_id == "environment"
    }
    assert set(sensor_values.values()) == {21.5, 48.0, 87}

    door_entity = next(
        entity for entity in platform_entities["binary_sensor"] if entity._device_id == "door"
    )
    assert door_entity.is_on is True
    assert coordinator.async_control_device.await_count == 4
