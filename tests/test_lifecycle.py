"""Pure migration and runtime compatibility tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.orvibo_lan import (
    OrviboLanRuntimeData,
    _async_assign_areas,
    _register_services,
    _schedule_area_assignment,
    _unregister_services_if_unused,
    async_migrate_entry,
    get_runtime_data,
)
from custom_components.orvibo_lan.const import DOMAIN, SERVICE_REFRESH_DEVICES


@pytest.mark.asyncio
async def test_migrate_version_one_moves_selection_to_options() -> None:
    updates: list[dict[str, object]] = []
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_update_entry=lambda entry, **kwargs: updates.append(kwargs)
        )
    )
    entry = SimpleNamespace(
        version=1,
        data={"username": "user", "selected_device_ids": ["a", "b"]},
        options={},
        unique_id="user-id",
    )

    assert await async_migrate_entry(hass, entry)
    assert updates == [
        {
            "data": {"username": "user"},
            "options": {"selected_device_ids": ["a", "b"]},
            "version": 3,
        }
    ]


@pytest.mark.asyncio
async def test_migrate_version_two_scopes_unique_id_to_family() -> None:
    updates: list[dict[str, object]] = []
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_update_entry=lambda entry, **kwargs: updates.append(kwargs)
        )
    )
    entry = SimpleNamespace(
        version=2,
        data={"username": "user", "family_id": "family-1"},
        options={},
        unique_id="user-id",
    )

    assert await async_migrate_entry(hass, entry)
    assert updates == [
        {
            "data": {"username": "user", "family_id": "family-1"},
            "options": {},
            "unique_id": "user-id:family-1",
            "version": 3,
        }
    ]


def test_runtime_data_falls_back_to_hass_data() -> None:
    coordinator = MagicMock()
    runtime = OrviboLanRuntimeData(coordinator, MagicMock())
    hass = SimpleNamespace(data={DOMAIN: {"entry-1": runtime}})
    entry = SimpleNamespace(entry_id="entry-1")

    assert get_runtime_data(hass, entry) is runtime


def test_area_assignment_callback_uses_thread_safe_job_and_clears_listener() -> None:
    jobs: list[tuple[object, ...]] = []
    runtime = OrviboLanRuntimeData(MagicMock(), MagicMock())
    runtime.remove_start_listener = MagicMock()
    hass = SimpleNamespace(add_job=lambda *args: jobs.append(args))
    entry = SimpleNamespace(entry_id="entry-1")
    event = SimpleNamespace()

    _schedule_area_assignment(hass, entry, runtime, event)

    assert runtime.remove_start_listener is None
    assert jobs == [(_async_assign_areas, hass, entry, event)]


class FakeServices:
    def __init__(self) -> None:
        self.handlers: dict[tuple[str, str], object] = {}

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self.handlers

    def async_register(self, domain: str, service: str, handler) -> None:
        self.handlers[(domain, service)] = handler

    def async_remove(self, domain: str, service: str) -> None:
        self.handlers.pop((domain, service), None)


@pytest.mark.asyncio
async def test_refresh_service_updates_all_entries_and_unregisters_last() -> None:
    first = SimpleNamespace(coordinator=SimpleNamespace(async_request_refresh=AsyncMock()))
    second = SimpleNamespace(coordinator=SimpleNamespace(async_request_refresh=AsyncMock()))
    services = FakeServices()
    hass = SimpleNamespace(
        data={DOMAIN: {"one": first, "two": second}},
        services=services,
    )

    _register_services(hass)
    _register_services(hass)
    handler = services.handlers[(DOMAIN, SERVICE_REFRESH_DEVICES)]
    await handler(SimpleNamespace())

    first.coordinator.async_request_refresh.assert_awaited_once()
    second.coordinator.async_request_refresh.assert_awaited_once()

    hass.data[DOMAIN].clear()
    _unregister_services_if_unused(hass)
    assert not services.has_service(DOMAIN, SERVICE_REFRESH_DEVICES)
