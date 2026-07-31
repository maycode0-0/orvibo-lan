"""Shared Home Assistant entity behavior for Orvibo devices."""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import OrviboLanCoordinator


class OrviboLanEntity(CoordinatorEntity[OrviboLanCoordinator]):
    """Apply transport-aware availability consistently across platforms."""

    _device_id: str

    @property
    def available(self) -> bool:
        return self.coordinator.is_device_available(self._device_id)
