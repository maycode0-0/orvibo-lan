"""Tests for the authenticated ORVIBO cloud push stream."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.orvibo_lan.device_profiles import (
    PROPERTY_LOCK_OPEN_DIRECTION,
    PROPERTY_LOCK_OPEN_USER,
)
from custom_components.orvibo_lan.lib import cloud_push
from custom_components.orvibo_lan.lib.cloud_push import (
    CloudPushClient,
    CloudPushError,
    parse_lock_event,
)
from custom_components.orvibo_lan.lib.packet import ID_UNSET


async def _callback(_event) -> None:
    return None


def test_parse_lock_event_prefers_device_id_and_filters_properties() -> None:
    event = parse_lock_event(
        {
            "cmd": 42,
            "deviceId": "device-1",
            "uid": "shared-or-different-uid",
            "properties": {
                "door_status": "open",
                "reverse_lock": "off",
                "handle": "down",
                "doorOpenType": {"value": "outside"},
                "doorOpenUserName": {"name": "张三"},
                "ssid": "private-network",
            },
            "updateTimeSec": 1785419851,
        }
    )

    assert event is not None
    assert event.device_id == "device-1"
    assert event.uid == "shared-or-different-uid"
    assert event.properties == {
        "door_status": "open",
        "reverse_lock": "off",
        "handle": "down",
        PROPERTY_LOCK_OPEN_DIRECTION: "outside",
        PROPERTY_LOCK_OPEN_USER: "张三",
    }
    assert event.update_time == 1785419851


def test_parse_lock_event_rejects_unknown_door_values() -> None:
    assert (
        parse_lock_event(
            {
                "cmd": 42,
                "deviceId": "device-1",
                "properties": {"door_status": "fingerprint"},
            }
        )
        is None
    )


def test_parse_lock_event_accepts_open_metadata_without_door_state() -> None:
    event = parse_lock_event(
        {
            "cmd": "42",
            "deviceId": "device-1",
            "properties": {
                "open_direction": "室内",
                "unlock_user_id": 7,
                "userName": "cloud-account",
            },
        }
    )

    assert event is not None
    assert event.properties == {
        PROPERTY_LOCK_OPEN_DIRECTION: "inside",
        PROPERTY_LOCK_OPEN_USER: "7",
    }


def test_parse_lock_event_accepts_specific_top_level_metadata() -> None:
    event = parse_lock_event(
        {
            "cmd": 42,
            "deviceId": "device-1",
            "userName": "cloud-account",
            "openDoorDirection": "outside",
            "openDoorUserName": "李四",
            "properties": {},
        }
    )

    assert event is not None
    assert event.properties == {
        PROPERTY_LOCK_OPEN_DIRECTION: "outside",
        PROPERTY_LOCK_OPEN_USER: "李四",
    }


def test_parse_lock_event_infers_direction_from_unlock_method() -> None:
    event = parse_lock_event(
        {
            "cmd": 42,
            "deviceId": "device-1",
            "properties": {
                "openType": "fingerprint",
                "openUserId": 7,
            },
        }
    )

    assert event is not None
    assert event.properties == {
        PROPERTY_LOCK_OPEN_DIRECTION: "outside",
        PROPERTY_LOCK_OPEN_USER: "7",
    }


@pytest.mark.asyncio
async def test_dynamic_packet_round_trip() -> None:
    client = CloudPushClient(
        "china.orvibo.com",
        "binary-user",
        "transient-password",
        "family-1",
        _callback,
    )
    client._session_id = b"s" * 32
    client._dynamic_key = b"0123456789ABCDEF"
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()

    await client._send(writer, {"cmd": 42, "properties": {"door_status": "open"}})

    packet = writer.write.call_args.args[0]
    reader = asyncio.StreamReader()
    reader.feed_data(packet)
    reader.feed_eof()
    session_id, decoded = await client._read_packet(reader)
    assert session_id == b"s" * 32
    assert decoded["properties"] == {"door_status": "open"}


@pytest.mark.asyncio
async def test_lock_event_schema_log_never_exposes_values(caplog) -> None:
    callback = AsyncMock()
    client = CloudPushClient(
        "china.orvibo.com",
        "binary-user",
        "transient-password",
        "family-1",
        callback,
    )
    packet = {
        "cmd": 42,
        "deviceId": "private-device-value",
        "properties": {
            "door_status": "open",
            "unlockRecord": {
                "userName": "private-user-value",
                "direction": "private-direction-value",
                "credentials": ["private-token-value"],
            },
        },
    }

    with caplog.at_level(logging.DEBUG, logger=cloud_push.__name__):
        await client._dispatch_packet(packet)

    callback.assert_awaited_once()
    assert "properties.unlockRecord.userName:string" in caplog.text
    assert "properties.unlockRecord.credentials[]:string" in caplog.text
    assert "private-device-value" not in caplog.text
    assert "private-user-value" not in caplog.text
    assert "private-direction-value" not in caplog.text
    assert "private-token-value" not in caplog.text


@pytest.mark.asyncio
async def test_handshake_uses_payload_session_id_when_header_is_unset() -> None:
    client = CloudPushClient(
        "china.orvibo.com",
        "binary-user",
        "transient-password",
        "family-1",
        _callback,
    )
    client._send = AsyncMock()
    client._receive_command = AsyncMock(
        return_value=(
            ID_UNSET,
            {
                "cmd": 0,
                "key": "0123456789ABCDEF",
                "sessionId": "s" * 32,
            },
        )
    )

    await client._handshake(MagicMock(), MagicMock())

    assert client._session_id == b"s" * 32


def test_server_certificate_is_pinned_before_login() -> None:
    certificate = b"server-certificate"
    ssl_object = MagicMock()
    ssl_object.getpeercert.return_value = certificate
    writer = MagicMock()
    writer.get_extra_info.return_value = ssl_object
    client = CloudPushClient(
        "china.orvibo.com",
        "binary-user",
        "transient-password",
        "family-1",
        _callback,
    )

    with patch.dict(
        cloud_push._SERVER_CERT_SHA256,
        {"china.orvibo.com": hashlib.sha256(certificate).digest()},
    ):
        client._verify_server_certificate(writer)
        ssl_object.getpeercert.return_value = b"attacker-certificate"
        with pytest.raises(CloudPushError, match="server_certificate_mismatch"):
            client._verify_server_certificate(writer)


@pytest.mark.asyncio
async def test_run_reconnects_and_close_stops_backoff() -> None:
    client = CloudPushClient(
        "china.orvibo.com",
        "binary-user",
        "transient-password",
        "family-1",
        _callback,
        reconnect_delay=0.01,
    )
    second_attempt = asyncio.Event()
    attempts = 0

    async def fail_then_wait() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise CloudPushError("transport")
        second_attempt.set()
        await client._stop.wait()

    client._run_once = fail_then_wait
    task = asyncio.create_task(client.run())
    await asyncio.wait_for(second_attempt.wait(), 1)

    await client.close()
    await asyncio.wait_for(task, 1)

    assert attempts == 2


@pytest.mark.asyncio
async def test_idle_stream_reconnects_when_heartbeat_is_not_acknowledged() -> None:
    client = CloudPushClient(
        "china.orvibo.com",
        "binary-user",
        "transient-password",
        "family-1",
        _callback,
    )
    client._read_packet = AsyncMock(side_effect=TimeoutError)
    client._send = AsyncMock()

    with patch.object(cloud_push, "_HEARTBEAT_INTERVAL", 0.001), patch.object(
        cloud_push,
        "_REQUEST_TIMEOUT",
        0.001,
    ):
        with pytest.raises(CloudPushError, match="heartbeat_timeout"):
            await client._stream(MagicMock(), MagicMock())

    client._send.assert_awaited_once()
