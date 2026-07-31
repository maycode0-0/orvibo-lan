"""Production-path tests for configuration and family selection flows."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.orvibo_lan import config_flow
from custom_components.orvibo_lan.const import (
    CONF_FAMILY_ID,
    CONF_PASSWORD,
    CONF_SELECTED_DEVICE_IDS,
    CONF_USERNAME,
)


class FakeCloudClient:
    def __init__(self) -> None:
        self.family_list = [
            {"familyId": "family-1", "familyName": "Home"},
            {"familyId": "family-2", "familyName": "Office"},
        ]
        self.family_name = "Home"
        self.family_id = "family-1"
        self.user_id = "user-1"

    async def login(self) -> None:
        return None

    async def fetch_devices(self):
        return (
            [
                {
                    "deviceId": "device-1",
                    "deviceType": 1,
                    "uid": "gateway-1",
                    "deviceName": "Desk light",
                    "roomId": "room-1",
                }
            ],
            {"device-1": {"deviceId": "device-1", "value1": 0}},
            [],
            [{"roomId": "room-1", "roomName": "Office"}],
            {"gateway-1": "192.168.1.2"},
            object(),
        )


def test_selection_helpers_use_actual_production_functions() -> None:
    devices = [
        {"deviceId": "one", "deviceName": "Light", "roomName": "Office"},
        {"deviceId": "two", "deviceName": "Sensor", "roomName": ""},
    ]

    assert config_flow._device_label("one", "Light", "Office") == "Light [Office]"
    assert config_flow._validated_selection(["two", "missing"], devices) == ["two"]
    assert config_flow._validated_selection("one", devices) == []


def test_options_flow_keeps_config_entry_without_setting_read_only_property() -> None:
    entry = SimpleNamespace(
        data={
            CONF_USERNAME: "account",
            CONF_PASSWORD: "password",
            CONF_FAMILY_ID: "family-1",
        },
        options={CONF_SELECTED_DEVICE_IDS: ["device-1"]},
    )

    flow = config_flow.OrviboLanConfigFlow.async_get_options_flow(entry)

    assert flow._config_entry is entry


@pytest.mark.asyncio
async def test_user_flow_selects_family_devices_and_scopes_unique_id() -> None:
    client = FakeCloudClient()
    flow = config_flow.OrviboLanConfigFlow()
    flow.hass = SimpleNamespace()

    with (
        patch.object(config_flow, "CloudClient", return_value=client),
        patch.object(config_flow, "async_get_clientsession", return_value=object()),
    ):
        result = await flow.async_step_user({CONF_USERNAME: "account", CONF_PASSWORD: "password"})
        assert result["step_id"] == "select_family"

        result = await flow.async_step_select_family({CONF_FAMILY_ID: "family-2"})
        assert result["step_id"] == "devices"

        result = await flow.async_step_devices({CONF_SELECTED_DEVICE_IDS: ["device-1"]})

    assert result["type"] == "create_entry"
    assert result["title"] == "account - Office"
    assert result["data"][CONF_FAMILY_ID] == "family-2"
    assert result["options"] == {CONF_SELECTED_DEVICE_IDS: ["device-1"]}
    assert flow.unique_id == "user-1:family-2"


@pytest.mark.asyncio
async def test_user_flow_rejects_empty_credentials() -> None:
    flow = config_flow.OrviboLanConfigFlow()
    flow.hass = SimpleNamespace()

    result = await flow.async_step_user({CONF_USERNAME: "", CONF_PASSWORD: ""})

    assert result["type"] == "form"
    assert result["errors"] == {"base": "empty_username_or_password"}
