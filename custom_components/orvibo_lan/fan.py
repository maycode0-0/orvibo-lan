"""Orvibo LAN Fan 平台（新风系统）。"""

import logging
from collections.abc import Mapping
from typing import Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MANUFACTURER
from .coordinator import OrviboLanCoordinator
from .device_profiles import supports_platform
from .entity import OrviboLanEntity
from .lib import device_control as dc

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    # 延迟导入，避免 HA 2026 import_module 阻塞检测
    from homeassistant.components.fan import FanEntity, FanEntityFeature

    class OrviboLanFan(OrviboLanEntity, FanEntity):
        """Orvibo 新风实体。"""

        _attr_has_entity_name = True

        def __init__(self, coordinator, device_id, device, device_type):
            super().__init__(coordinator)
            self._device_id = device_id
            self._device = device
            self._device_type = device_type

            name = device.get("deviceName", f"Fan {device_id[:8]}")
            self._attr_unique_id = f"{DOMAIN}_fan_{device_id}"
            self._attr_name = name
            self._attr_supported_features = (
                FanEntityFeature.SET_SPEED | FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
            )
            self._attr_speed_count = 4  # low/medium/high/auto

            # 每个设备独立注册为 HA 设备，via_device 指向网关
            uid = device.get("uid", "")
            dev_info = {
                "identifiers": {(DOMAIN, f"device_{device_id}")},
                "name": device.get("deviceName", f"Fan {device_id[:8]}"),
                "manufacturer": MANUFACTURER,
                "model": "Orvibo Fan",
            }
            if uid and device.get("_orvibo_lan_capable"):
                dev_info["via_device"] = (DOMAIN, f"gateway_{uid}")
            self._attr_device_info = dev_info

        @property
        def is_on(self) -> bool:
            st = self.coordinator.get_device_state(self._device_id)
            if not st:
                return False
            return self._parse_on(st)

        @property
        def percentage(self) -> Optional[int]:
            st = self.coordinator.get_device_state(self._device_id)
            if not st:
                return None
            return self._parse_speed(st)

        def _parse_on(self, st: dict) -> bool:
            if self._device_type == 516:
                props = st.get("properties", {}) or {}
                onoff = props.get("onoff", {})
                if isinstance(onoff, Mapping):
                    status = onoff.get("status")
                    if status is not None:
                        return status == "on"
                # Legacy ventilation_control uses 50=stop, 0=slow, 100=fast.
                value1 = st.get("value1")
                return value1 is not None and int(value1) != 50
            # type 81: value2 表示档位, >0 表示开启
            v2 = st.get("value2")
            if v2 is not None:
                return int(v2) > 0
            return False

        def _parse_speed(self, st: dict) -> Optional[int]:
            if self._device_type == 516:
                props = st.get("properties", {}) or {}
                fan_speed = props.get("fanSpeed", {})
                if isinstance(fan_speed, Mapping):
                    speed_value = fan_speed.get("value")
                    if speed_value is not None:
                        return int(speed_value) * 100 // 100
                value1 = st.get("value1")
                if value1 is not None:
                    value1 = int(value1)
                    if value1 == 50:
                        return 0
                    if value1 == 0:
                        return 33
                    if value1 == 100:
                        return 100
                return None
            # type 81: value2 = 0(off)/1(low)/2(mid)/3(high)
            v2 = st.get("value2")
            if v2 is not None:
                v2 = int(v2)
                if v2 == 0:
                    return 0
                return int(v2 * 100 / 3)
            return None

        async def async_turn_on(
            self,
            speed: Optional[str] = None,
            percentage: Optional[int] = None,
            **kwargs: object,
        ) -> None:
            if percentage is not None:
                await self.async_set_percentage(percentage)
            else:
                payload = dc.fan_on(
                    self._device_id,
                    self._device.get("uid", ""),
                    self._device_type,
                    username=self.coordinator.username,
                )
                await self.coordinator.async_control_device(self._device_id, payload)

        async def async_turn_off(self, **kwargs):
            payload = dc.fan_off(
                self._device_id,
                self._device.get("uid", ""),
                self._device_type,
                username=self.coordinator.username,
            )
            await self.coordinator.async_control_device(self._device_id, payload)

        async def async_set_percentage(self, percentage: int) -> None:
            payload = dc.fan_set_speed(
                self._device_id,
                self._device.get("uid", ""),
                self._device_type,
                percentage,
                username=self.coordinator.username,
            )
            await self.coordinator.async_control_device(self._device_id, payload)

    from . import get_runtime_data

    coordinator: OrviboLanCoordinator = get_runtime_data(hass, entry).coordinator
    from .selection import selected_device_ids

    selected_ids = selected_device_ids(entry.options, coordinator.devices)
    entities = []

    from .const import HIDDEN_TYPES

    for did, device in coordinator.devices.items():
        if did not in selected_ids:
            continue
        dt = coordinator.device_types.get(did, 0)
        if not supports_platform(
            device,
            coordinator.get_device_state(did),
            "fan",
        ):
            continue
        if dt in HIDDEN_TYPES:
            continue

        entities.append(OrviboLanFan(coordinator, did, device, dt))

    if entities:
        async_add_entities(entities)
