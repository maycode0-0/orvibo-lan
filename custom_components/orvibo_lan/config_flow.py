"""Config, reauthentication, and options flows for ORVIBO LAN."""

from __future__ import annotations

import logging
from collections.abc import Mapping

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_FAMILY_ID,
    CONF_PASSWORD,
    CONF_SELECTED_DEVICE_IDS,
    CONF_USERNAME,
    DOMAIN,
    HIDDEN_TYPES,
)
from .device_profiles import normalize_readtable_devices, platforms_for_device
from .lib.cloud_client import CloudAuthenticationError, CloudClient, CloudClientError
from .selection import config_entry_unique_id, selected_device_ids

_LOGGER = logging.getLogger(__name__)

JsonObject = dict[str, object]


def _device_label(device_id: str, name: str, room: str) -> str:
    return f"{name} [{room}]" if room and room != name else name or device_id[-8:]


def _device_schema(
    devices: list[JsonObject],
    default_ids: list[str] | None = None,
) -> vol.Schema:
    options = [
        selector.SelectOptionDict(
            value=str(device["deviceId"]),
            label=_device_label(
                str(device["deviceId"]),
                str(device.get("deviceName") or ""),
                str(device.get("roomName") or ""),
            ),
        )
        for device in devices
    ]
    defaults = default_ids or [str(device["deviceId"]) for device in devices]
    return vol.Schema(
        {
            vol.Required(CONF_SELECTED_DEVICE_IDS, default=defaults): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        }
    )


async def _load_devices(client: CloudClient) -> list[JsonObject]:
    devices, statuses, _gateways, rooms, gateway_ips, _resolver = await client.fetch_devices()
    room_names = {
        str(room["roomId"]): str(room.get("roomName") or "")
        for room in rooms
        if room.get("roomId") not in (None, "")
    }
    result: list[JsonObject] = []
    for device in normalize_readtable_devices(devices, statuses, set(gateway_ips)):
        device_type = device.get("deviceType")
        if device_type in HIDDEN_TYPES or device_type == 114:
            continue
        device_id = str(device["deviceId"])
        state = statuses.get(device_id, {})
        if not device.get("_orvibo_lan_capable") and not platforms_for_device(device, state):
            continue
        normalized = dict(device)
        normalized["roomName"] = room_names.get(str(device.get("roomId") or ""), "")
        result.append(normalized)
    return result


class OrviboLanConfigFlow(  # type: ignore[call-arg]
    config_entries.ConfigFlow,
    domain=DOMAIN,
):
    """Create and reauthenticate an ORVIBO LAN config entry."""

    VERSION = 3

    def __init__(self) -> None:
        self._username = ""
        self._password = ""
        self._family_list: list[JsonObject] = []
        self._family_name = ""
        self._selected_family_id: str | None = None
        self._user_id = ""
        self._devices: list[JsonObject] = []
        self._client: CloudClient | None = None

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return OrviboLanOptionsFlow(config_entry)

    async def async_step_user(
        self,
        user_input: JsonObject | None = None,
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            username = str(user_input.get(CONF_USERNAME) or "").strip()
            password = str(user_input.get(CONF_PASSWORD) or "")
            if not username or not password:
                errors["base"] = "empty_username_or_password"
            else:
                client = CloudClient(async_get_clientsession(self.hass), username, password)
                try:
                    await client.login()
                except CloudAuthenticationError:
                    errors["base"] = "auth_failed"
                except CloudClientError:
                    errors["base"] = "cannot_connect"
                else:
                    self._username = username
                    self._password = password
                    self._client = client
                    self._family_list = client.family_list
                    self._family_name = client.family_name
                    self._user_id = client.user_id or username
                    if len(self._family_list) > 1:
                        return await self.async_step_select_family()
                    self._selected_family_id = client.family_id
                    return await self.async_step_devices()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_select_family(
        self,
        user_input: JsonObject | None = None,
    ) -> FlowResult:
        if user_input is not None:
            selected = str(user_input.get(CONF_FAMILY_ID) or "")
            if selected:
                self._selected_family_id = selected
                if self._client is not None:
                    self._client.family_id = selected
                for family in self._family_list:
                    if family.get("familyId") == selected:
                        self._family_name = str(family.get("familyName") or "")
                        break
                return await self.async_step_devices()
        choices = {
            str(family["familyId"]): str(family.get("familyName") or family["familyId"])
            for family in self._family_list
        }
        return self.async_show_form(
            step_id="select_family",
            data_schema=vol.Schema({vol.Required(CONF_FAMILY_ID): vol.In(choices)}),
        )

    async def async_step_devices(
        self,
        user_input: JsonObject | None = None,
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if not self._devices and self._client is not None:
            try:
                self._devices = await _load_devices(self._client)
            except CloudAuthenticationError:
                errors["base"] = "auth_failed"
            except CloudClientError:
                errors["base"] = "cannot_connect"
        if not self._devices and not errors:
            errors["base"] = "no_devices"
        if user_input is not None and CONF_SELECTED_DEVICE_IDS in user_input:
            selected = _validated_selection(user_input[CONF_SELECTED_DEVICE_IDS], self._devices)
            if selected:
                return await self._create_entry(selected)
            errors["base"] = "no_devices_selected"
        return self.async_show_form(
            step_id="devices",
            data_schema=_device_schema(self._devices),
            errors=errors,
        )

    async def _create_entry(self, selected: list[str]) -> FlowResult:
        family_id = self._selected_family_id
        if family_id is None and self._family_list:
            family_id = str(self._family_list[0]["familyId"])
        await self.async_set_unique_id(config_entry_unique_id(self._user_id, family_id))
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"{self._username} - {self._family_name}",
            data={
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_FAMILY_ID: family_id,
            },
            options={CONF_SELECTED_DEVICE_IDS: selected},
        )

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, object],
    ) -> FlowResult:
        self._username = str(entry_data.get(CONF_USERNAME) or "")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: JsonObject | None = None,
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            password = str(user_input.get(CONF_PASSWORD) or "")
            entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
            if entry is None:
                return self.async_abort(reason="reauth_entry_missing")
            client = CloudClient(
                async_get_clientsession(self.hass),
                self._username,
                password,
                str(entry.data.get(CONF_FAMILY_ID) or "") or None,
            )
            try:
                await client.login()
            except CloudAuthenticationError:
                errors["base"] = "auth_failed"
            except CloudClientError:
                errors["base"] = "cannot_connect"
            else:
                data = dict(entry.data)
                data[CONF_PASSWORD] = password
                self.hass.config_entries.async_update_entry(entry, data=data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )


class OrviboLanOptionsFlow(config_entries.OptionsFlow):
    """Select exposed devices using fresh cloud metadata."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        # Home Assistant exposes ``config_entry`` as a read-only property on
        # OptionsFlow. Keep the factory argument privately for compatibility
        # with HA versions that do not populate that property yet.
        self._config_entry = config_entry
        self._devices: list[JsonObject] = []

    async def async_step_init(
        self,
        user_input: JsonObject | None = None,
    ) -> FlowResult:
        errors: dict[str, str] = {}
        config_entry = self._config_entry
        if not self._devices:
            client = CloudClient(
                async_get_clientsession(self.hass),
                str(config_entry.data[CONF_USERNAME]),
                str(config_entry.data[CONF_PASSWORD]),
                str(config_entry.data.get(CONF_FAMILY_ID) or "") or None,
            )
            try:
                await client.login()
                self._devices = await _load_devices(client)
            except CloudAuthenticationError:
                errors["base"] = "auth_failed"
            except CloudClientError:
                errors["base"] = "cannot_connect"
        if not self._devices and not errors:
            errors["base"] = "no_devices"
        if user_input is not None and CONF_SELECTED_DEVICE_IDS in user_input:
            selected = _validated_selection(user_input[CONF_SELECTED_DEVICE_IDS], self._devices)
            if selected:
                return self.async_create_entry(
                    title="",
                    data={CONF_SELECTED_DEVICE_IDS: selected},
                )
            errors["base"] = "no_devices_selected"
        current = selected_device_ids(
            config_entry.options,
            (str(device["deviceId"]) for device in self._devices),
        )
        return self.async_show_form(
            step_id="init",
            data_schema=_device_schema(self._devices, sorted(current)),
            errors=errors,
        )


def _validated_selection(value: object, devices: list[JsonObject]) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    requested = {str(item) for item in value}
    return [str(device["deviceId"]) for device in devices if str(device["deviceId"]) in requested]
