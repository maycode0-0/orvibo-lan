"""Concurrency and protocol tests for GatewayConnection."""

import asyncio
import logging
from typing import Any, cast

import pytest

from custom_components.orvibo_lan.lib.gateway_connection import (
    GatewayConnection,
    GatewayConnectionError,
    GatewayDisconnectedError,
    GatewayRequestTimeoutError,
)
from custom_components.orvibo_lan.lib.protocol import (
    DEFAULT_KEY,
    DK_TYPE,
    PK_TYPE,
    build_packet,
    parse_packet,
)

SESSION = b"s" * 32
SESSION_KEY = b"0123456789abcdef"


class FakeWriter:
    def __init__(self, reader: asyncio.StreamReader) -> None:
        self.reader = reader
        self.writes: list[bytes] = []
        self.closed = False
        self.drain_gate: asyncio.Event | None = None

    def write(self, packet: bytes) -> None:
        self.writes.append(packet)

    async def drain(self) -> None:
        if self.drain_gate is not None:
            await self.drain_gate.wait()

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None

    def respond(
        self,
        payload: dict[str, Any],
        *,
        packet_type: bytes = DK_TYPE,
        key: bytes = SESSION_KEY,
        session_id: bytes = SESSION,
    ) -> None:
        self.reader.feed_data(build_packet(packet_type, key, session_id, payload))


def activate(connection: GatewayConnection) -> tuple[asyncio.StreamReader, FakeWriter]:
    reader = asyncio.StreamReader()
    writer = FakeWriter(reader)
    connection.reader = reader
    connection.writer = cast(asyncio.StreamWriter, writer)
    connection.session_id = SESSION
    connection.session_key = SESSION_KEY
    connection._keys[SESSION] = SESSION_KEY
    connection._closed = False
    connection._ready = True
    connection.generation = 1
    connection._reader_task = asyncio.create_task(
        connection._reader_loop(1, reader, cast(asyncio.StreamWriter, writer))
    )
    return reader, writer


@pytest.mark.asyncio
async def test_single_reader_routes_concurrent_serial_responses() -> None:
    connection = GatewayConnection("192.168.1.2")
    _, writer = activate(connection)

    first = asyncio.create_task(connection.send({"cmd": 15, "serial": 101}))
    second = asyncio.create_task(connection.send({"cmd": 15, "serial": 202}))
    while len(writer.writes) < 2:
        await asyncio.sleep(0)

    writer.respond({"cmd": 15, "serial": 202, "status": 0})
    writer.respond({"cmd": 15, "serial": 101, "status": 0})

    assert (await first)["serial"] == 101
    assert (await second)["serial"] == 202
    assert connection._reader_task is not None
    await connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [42, "42", 99])
async def test_routes_unsolicited_device_updates_to_push_callback(command: int | str) -> None:
    pushes: list[dict[str, Any]] = []

    def push_callback(payload: dict[str, Any]) -> None:
        pushes.append(payload)

    connection = GatewayConnection("192.168.1.2", push_callback=push_callback)
    _, writer = activate(connection)
    writer.respond({"cmd": command, "deviceId": "door", "value1": 1})
    while not pushes:
        await asyncio.sleep(0)

    assert len(pushes) == 1
    public_push = {key: value for key, value in pushes[0].items() if not key.startswith("__")}
    assert public_push == {"cmd": command, "deviceId": "door", "value1": 1}
    await connection.close()


@pytest.mark.asyncio
async def test_ignores_unsolicited_command_without_device_id() -> None:
    pushes: list[dict[str, Any]] = []
    connection = GatewayConnection("192.168.1.2", push_callback=pushes.append)
    _, writer = activate(connection)

    writer.respond({"cmd": 99, "value1": 1})
    await asyncio.sleep(0)

    assert pushes == []
    await connection.close()


@pytest.mark.asyncio
async def test_push_logs_mask_host_and_device_id(caplog: pytest.LogCaptureFixture) -> None:
    pushes: list[dict[str, Any]] = []
    host = "192.168.1.27"
    device_id = "private-device-12345678"
    command = "private-command-value"
    connection = GatewayConnection(host, push_callback=pushes.append)
    _, writer = activate(connection)
    caplog.set_level(
        logging.DEBUG,
        logger="custom_components.orvibo_lan.lib.gateway_connection",
    )

    writer.respond({"cmd": command, "deviceId": device_id, "value1": 1})
    while not pushes:
        await asyncio.sleep(0)

    assert host not in caplog.text
    assert device_id not in caplog.text
    assert command not in caplog.text
    assert "192.168.1.*" in caplog.text
    await connection.close()


@pytest.mark.asyncio
async def test_heartbeat_failure_logs_safe_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    host = "192.168.1.27"
    connection = GatewayConnection(
        host,
        heartbeat_interval=0,
        heartbeat_timeout=0.01,
    )
    activate(connection)
    caplog.set_level(
        logging.DEBUG,
        logger="custom_components.orvibo_lan.lib.gateway_connection",
    )
    heartbeat = asyncio.create_task(connection._heartbeat_loop(connection.generation))
    connection._heartbeat_task = heartbeat

    await heartbeat

    assert not connection.connected
    assert "reason=request_timeout" in caplog.text
    assert host not in caplog.text
    assert "192.168.1.*" in caplog.text


@pytest.mark.asyncio
async def test_correlated_device_response_is_not_mistaken_for_push() -> None:
    pushes: list[dict[str, Any]] = []
    connection = GatewayConnection("192.168.1.2", push_callback=pushes.append)
    _, writer = activate(connection)
    request = asyncio.create_task(connection.send({"cmd": 15, "serial": 101}))
    while len(writer.writes) < 1:
        await asyncio.sleep(0)
    writer.respond({"cmd": 15, "serial": 101, "deviceId": "door", "status": 0})

    assert (await request)["status"] == 0
    await asyncio.sleep(0)
    assert pushes == []
    await connection.close()


@pytest.mark.asyncio
async def test_unreliable_requests_are_single_flight_and_lock_wait_times_out() -> None:
    connection = GatewayConnection("192.168.1.2")
    _, writer = activate(connection)

    first = asyncio.create_task(connection.send({"cmd": 15}, timeout=1))
    while not writer.writes:
        await asyncio.sleep(0)
    with pytest.raises(GatewayRequestTimeoutError):
        await connection.send({"cmd": 16}, timeout=0.01)
    assert len(writer.writes) == 1

    sent = parse_packet(writer.writes[0], {SESSION: SESSION_KEY})
    writer.respond({"cmd": 15, "serial": sent["serial"], "status": 0})
    assert (await first)["status"] == 0
    await connection.close()


@pytest.mark.asyncio
async def test_bad_magic_disconnects_and_close_is_idempotent() -> None:
    connection = GatewayConnection("192.168.1.2")
    reader, writer = activate(connection)
    reader.feed_data(b"xx\x00\x3a" + b"0" * 54)

    assert connection._reader_task is not None
    await connection._reader_task
    assert not connection.connected
    assert writer.closed
    await connection.close()
    await connection.close()


@pytest.mark.asyncio
async def test_bad_crc_disconnects_current_generation() -> None:
    connection = GatewayConnection("192.168.1.2")
    reader, writer = activate(connection)
    packet = bytearray(build_packet(DK_TYPE, SESSION_KEY, SESSION, {"cmd": 15, "serial": 1}))
    packet[6:10] = b"\x00\x00\x00\x00"
    reader.feed_data(bytes(packet))

    assert connection._reader_task is not None
    await connection._reader_task
    assert not connection.connected
    assert writer.closed
    await connection.close()


@pytest.mark.asyncio
async def test_hello_uid_confirms_gateway_when_login_omits_uid() -> None:
    async def open_connection(host: str, port: int) -> tuple[asyncio.StreamReader, FakeWriter]:
        del host, port
        reader = asyncio.StreamReader()
        writer = FakeWriter(reader)

        async def server() -> None:
            while len(writer.writes) < 1:
                await asyncio.sleep(0)
            hello = parse_packet(writer.writes[0], {})
            writer.respond(
                {
                    "cmd": hello["cmd"],
                    "serial": hello["serial"],
                    "uid": "expected",
                    "sessionKey": SESSION_KEY.hex(),
                },
                packet_type=PK_TYPE,
                key=DEFAULT_KEY,
                session_id=SESSION,
            )
            while len(writer.writes) < 2:
                await asyncio.sleep(0)
            login = parse_packet(writer.writes[1], {SESSION: SESSION_KEY})
            writer.respond({"cmd": login["cmd"], "serial": login["serial"], "status": 0})

        asyncio.create_task(server())
        return reader, writer

    connection = GatewayConnection("192.168.1.2", open_connection=open_connection)
    await connection.connect("user", "password", expected_uid="expected")
    assert connection.identity_confirmed
    assert connection.peer_uid == "expected"
    await connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_missing_uid", [False, True])
async def test_empty_gateway_uid_is_only_allowed_for_trusted_host(
    allow_missing_uid: bool,
) -> None:
    async def open_connection(host: str, port: int) -> tuple[asyncio.StreamReader, FakeWriter]:
        del host, port
        reader = asyncio.StreamReader()
        writer = FakeWriter(reader)

        async def server() -> None:
            while len(writer.writes) < 1:
                await asyncio.sleep(0)
            hello = parse_packet(writer.writes[0], {})
            writer.respond(
                {
                    "cmd": hello["cmd"],
                    "serial": hello["serial"],
                    "sessionKey": SESSION_KEY.hex(),
                },
                packet_type=PK_TYPE,
                key=DEFAULT_KEY,
                session_id=SESSION,
            )
            while len(writer.writes) < 2:
                await asyncio.sleep(0)
            login = parse_packet(writer.writes[1], {SESSION: SESSION_KEY})
            writer.respond({"cmd": login["cmd"], "serial": login["serial"], "status": 0, "uid": ""})

        asyncio.create_task(server())
        return reader, writer

    connection = GatewayConnection("192.168.1.2", open_connection=open_connection)
    if allow_missing_uid:
        await connection.connect(
            "user",
            "password",
            expected_uid="expected",
            allow_missing_uid=True,
        )
        assert connection.connected
        assert not connection.identity_confirmed
        await connection.close()
    else:
        with pytest.raises(GatewayConnectionError, match="expected UID"):
            await connection.connect("user", "password", expected_uid="expected")
        assert not connection.connected


@pytest.mark.asyncio
async def test_hello_rejects_mismatched_uid_before_login() -> None:
    writers: list[FakeWriter] = []

    async def open_connection(host: str, port: int) -> tuple[asyncio.StreamReader, FakeWriter]:
        del host, port
        reader = asyncio.StreamReader()
        writer = FakeWriter(reader)
        writers.append(writer)

        async def server() -> None:
            while len(writer.writes) < 1:
                await asyncio.sleep(0)
            hello = parse_packet(writer.writes[0], {})
            writer.respond(
                {
                    "cmd": hello["cmd"],
                    "serial": hello["serial"],
                    "uid": "actual-private-gateway",
                    "sessionKey": SESSION_KEY.hex(),
                },
                packet_type=PK_TYPE,
                key=DEFAULT_KEY,
                session_id=SESSION,
            )

        asyncio.create_task(server())
        return reader, writer

    connection = GatewayConnection("192.168.1.2", open_connection=open_connection)
    with pytest.raises(GatewayConnectionError, match="expected UID") as raised:
        await connection.connect(
            "user",
            "password",
            expected_uid="expected-private-gateway",
            allow_missing_uid=True,
        )
    assert not connection.identity_confirmed
    assert not connection.connected
    assert len(writers[0].writes) == 1
    assert raised.value.reason == "hello_uid_mismatch"
    assert "actual-private-gateway" not in str(raised.value)
    assert "expected-private-gateway" not in str(raised.value)


@pytest.mark.asyncio
async def test_write_time_is_in_request_deadline() -> None:
    connection = GatewayConnection("192.168.1.2")
    _, writer = activate(connection)
    writer.drain_gate = asyncio.Event()
    with pytest.raises(GatewayRequestTimeoutError):
        await connection.send({"cmd": 15, "serial": 1}, timeout=0.01)
    await connection.close()


@pytest.mark.asyncio
async def test_write_failure_disconnects_before_another_write() -> None:
    connection = GatewayConnection("192.168.1.2")
    _, writer = activate(connection)

    async def fail_drain() -> None:
        raise ConnectionError("broken")

    writer.drain = fail_drain
    with pytest.raises(GatewayDisconnectedError):
        await connection.send({"cmd": 15, "serial": 1})
    with pytest.raises(GatewayDisconnectedError):
        await connection.send({"cmd": 15, "serial": 2})
    assert len(writer.writes) == 1
    await connection.close()


@pytest.mark.asyncio
async def test_close_releases_lifecycle_lock_before_wait_closed() -> None:
    connection = GatewayConnection("192.168.1.2")
    _, writer = activate(connection)
    wait_gate = asyncio.Event()

    async def blocked_wait_closed() -> None:
        await wait_gate.wait()

    writer.wait_closed = blocked_wait_closed
    closing = asyncio.create_task(connection.close())
    while not writer.closed:
        await asyncio.sleep(0)
    async with connection._lifecycle_lock:
        assert connection.writer is None
    wait_gate.set()
    await closing
