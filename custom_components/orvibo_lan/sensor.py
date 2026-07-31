"""ORVIBO LAN sensor platform."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import TypeAlias

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MANUFACTURER
from .coordinator import OrviboLanCoordinator
from .device_profiles import (
    PROPERTY_BATTERY_POWER,
    property_percentage,
    property_value,
    state_properties,
    supports_platform,
)
from .entity import OrviboLanEntity

State: TypeAlias = Mapping[str, object]
Device: TypeAlias = Mapping[str, object]


def _safe_float(value: object) -> float | None:
    """Convert one scalar to a finite float, rejecting booleans."""

    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _safe_percentage(value: object) -> int | None:
    """Convert one scalar to an integer percentage."""

    number = _safe_float(value)
    if number is None:
        return None
    percentage = int(number)
    if percentage < 0 or percentage > 100:
        return None
    return percentage


def _get_battery(state: State) -> int | None:
    """Read a battery percentage from known property and legacy shapes."""

    properties = state_properties(state)
    for property_key in (PROPERTY_BATTERY_POWER, "battery"):
        property_battery = property_percentage(properties, property_key)
        if property_battery is not None:
            return property_battery
    for key in ("battery", "batteryLevel", "powerLevel"):
        value = _safe_percentage(state.get(key))
        if value is not None:
            return value
    return _safe_percentage(state.get("value4"))


def _get_measurement(state: State, key: str, fallback: str) -> float | None:
    """Read normalized, ThingModel, then legacy measurement fields."""

    value = _safe_float(state.get(key))
    if value is not None:
        return value
    value = _safe_float(property_value(state_properties(state), key))
    return value if value is not None else _safe_float(state.get(fallback))


def _device_name(device: Device, device_id: str) -> str:
    value = device.get("deviceName")
    return str(value) if value not in (None, "") else f"Sensor {device_id[:8]}"


def _device_info(device_id: str, device: Device, model: str) -> dict[str, object]:
    """Build the Home Assistant device registry payload."""

    info: dict[str, object] = {
        "identifiers": {(DOMAIN, f"device_{device_id}")},
        "name": _device_name(device, device_id),
        "manufacturer": MANUFACTURER,
        "model": model,
    }
    uid = device.get("uid")
    if isinstance(uid, str) and uid and device.get("_orvibo_lan_capable"):
        info["via_device"] = (DOMAIN, f"gateway_{uid}")
    return info


class OrviboLanBatterySensor(OrviboLanEntity, SensorEntity):
    """Generic battery sensor."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(
        self,
        coordinator: OrviboLanCoordinator,
        device_id: str,
        device: Device,
        model: str,
        unique_suffix: str = "battery",
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{DOMAIN}_{unique_suffix}_{device_id}"
        self._attr_name = "电量"
        self._attr_device_info = _device_info(device_id, device, model)

    @property
    def native_value(self) -> int | None:
        return _get_battery(self.coordinator.get_device_state(self._device_id) or {})


class OrviboLanTemperatureSensor(OrviboLanEntity, SensorEntity):
    """Temperature sensor."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: OrviboLanCoordinator,
        device_id: str,
        device: Device,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{DOMAIN}_temperature_{device_id}"
        self._attr_name = f"{_device_name(device, device_id)} 温度"
        self._attr_device_info = _device_info(
            device_id,
            device,
            "Orvibo Temp/Humidity Sensor",
        )

    @property
    def native_value(self) -> float | None:
        state = self.coordinator.get_device_state(self._device_id)
        return _get_measurement(state, "temperature", "value1") if state else None


class OrviboLanHumiditySensor(OrviboLanEntity, SensorEntity):
    """Humidity sensor."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(
        self,
        coordinator: OrviboLanCoordinator,
        device_id: str,
        device: Device,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{DOMAIN}_humidity_{device_id}"
        self._attr_name = f"{_device_name(device, device_id)} 湿度"
        self._attr_device_info = _device_info(
            device_id,
            device,
            "Orvibo Temp/Humidity Sensor",
        )

    @property
    def native_value(self) -> float | None:
        state = self.coordinator.get_device_state(self._device_id)
        if not state:
            return None
        value = _get_measurement(state, "humidity", "value2")
        if value is None or value < 0 or value > 100:
            return None
        return value


class OrviboLanDryBatterySensor(OrviboLanBatterySensor):
    """Door lock dry-cell battery sensor."""

    def __init__(
        self,
        coordinator: OrviboLanCoordinator,
        device_id: str,
        device: Device,
    ) -> None:
        super().__init__(
            coordinator,
            device_id,
            device,
            model="Orvibo Door Lock",
            unique_suffix="dry_battery",
        )
        self._attr_name = "干电池电量"

    @property
    def native_value(self) -> int | None:
        state = self.coordinator.get_device_state(self._device_id)
        if not state:
            return None
        value = state.get("dry_battery_level")
        return _get_battery(state) if value is None else _safe_percentage(value)


class OrviboLanLithiumBatterySensor(OrviboLanBatterySensor):
    """Door lock lithium battery sensor."""

    def __init__(
        self,
        coordinator: OrviboLanCoordinator,
        device_id: str,
        device: Device,
    ) -> None:
        super().__init__(
            coordinator,
            device_id,
            device,
            model="Orvibo Door Lock",
            unique_suffix="lithium_battery",
        )
        self._attr_name = "锂电池电量"

    @property
    def native_value(self) -> int | None:
        state = self.coordinator.get_device_state(self._device_id)
        if not state:
            return None
        value = state.get("lithium_battery_level")
        return _get_battery(state) if value is None else _safe_percentage(value)


SensorFactory: TypeAlias = Callable[
    [OrviboLanCoordinator, str, Device],
    list[SensorEntity],
]


def _environment_sensors(
    coordinator: OrviboLanCoordinator,
    device_id: str,
    device: Device,
) -> list[SensorEntity]:
    model = "Orvibo Temp/Humidity Sensor"
    return [
        OrviboLanTemperatureSensor(coordinator, device_id, device),
        OrviboLanHumiditySensor(coordinator, device_id, device),
        OrviboLanBatterySensor(
            coordinator,
            device_id,
            device,
            model,
            f"th_battery_{device_id}",
        ),
    ]


def _battery_sensor_factory(model: str, suffix: str) -> SensorFactory:
    def create(
        coordinator: OrviboLanCoordinator,
        device_id: str,
        device: Device,
    ) -> list[SensorEntity]:
        return [
            OrviboLanBatterySensor(
                coordinator,
                device_id,
                device,
                model,
                f"{suffix}_{device_id}",
            )
        ]

    return create


def _lock_sensors(
    coordinator: OrviboLanCoordinator,
    device_id: str,
    device: Device,
) -> list[SensorEntity]:
    return [
        OrviboLanDryBatterySensor(coordinator, device_id, device),
        OrviboLanLithiumBatterySensor(coordinator, device_id, device),
    ]


_SENSOR_FACTORIES: dict[int, SensorFactory] = {
    22: _environment_sensors,
    23: _environment_sensors,
    25: _battery_sensor_factory("Orvibo Gas Sensor", "gas_battery"),
    26: _battery_sensor_factory("Orvibo Motion Sensor", "motion_battery"),
    27: _battery_sensor_factory("Orvibo Smoke Sensor", "smoke_battery"),
    46: _battery_sensor_factory("Orvibo Door/Window Sensor", "door_battery"),
    54: _battery_sensor_factory("Orvibo Water Leak Sensor", "water_battery"),
    56: _battery_sensor_factory("Orvibo Emergency Button", "emergency_battery"),
    107: _lock_sensors,
    522: _lock_sensors,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities for selected devices."""

    from . import get_runtime_data
    from .selection import selected_device_ids

    coordinator = get_runtime_data(hass, entry).coordinator
    selected_ids = selected_device_ids(entry.options, coordinator.devices)
    entities: list[SensorEntity] = []

    for device_id, device in coordinator.devices.items():
        if device_id not in selected_ids:
            continue
        device_type = coordinator.device_types.get(device_id, 0)
        state = coordinator.get_device_state(device_id) or {}
        if not supports_platform(device, state, "sensor"):
            continue

        factory = _SENSOR_FACTORIES.get(device_type)
        if factory is not None:
            entities.extend(factory(coordinator, device_id, device))

        properties = state_properties(state)
        if device_type == 300:
            if property_value(properties, "temperature") is not None:
                entities.append(OrviboLanTemperatureSensor(coordinator, device_id, device))
            if property_value(properties, "humidity") is not None:
                entities.append(OrviboLanHumiditySensor(coordinator, device_id, device))

        has_factory_battery = factory is not None
        if not has_factory_battery and _get_battery(state) is not None:
            model_value = device.get("model")
            model = str(model_value) if model_value not in (None, "") else "Orvibo Property Device"
            entities.append(
                OrviboLanBatterySensor(
                    coordinator,
                    device_id,
                    device,
                    model,
                    "property_battery",
                )
            )

    if entities:
        async_add_entities(entities)
