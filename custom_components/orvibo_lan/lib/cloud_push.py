"""Authenticated ORVIBO cloud event stream for Wi-Fi door locks."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
import ssl
import struct
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from ..exceptions import OrviboLanError
from .packet import (
    CMD_HEARTBEAT,
    CMD_LOGIN,
    CMD_STATE_UPDATE,
    DEFAULT_KEY,
    DK_TYPE,
    ID_UNSET,
    LANGUAGE,
    MAGIC,
    PK_TYPE,
    SOFTWARE_NAME,
    SYS_VERSION,
    build_packet,
    parse_packet,
)

_LOGGER = logging.getLogger(__name__)

_PORT: Final = 10002
_CONNECT_TIMEOUT: Final = 15.0
_REQUEST_TIMEOUT: Final = 10.0
_HEARTBEAT_INTERVAL: Final = 60.0
_RECONNECT_DELAY: Final = 5.0
_HEADER_SIZE: Final = 42
_MAX_FRAME_SIZE: Final = 65535
_PUSH_SOFTWARE_VERSION: Final = "5.2.6.302"
_PUSH_SOFTWARE_VERSION_CODE: Final = "50206302"
_PUSH_DEBUG_INFO: Final = "Android_ZhiJia365_32_5.2.6.302"
_CERT_PATH: Final = Path(__file__).with_name("orvibo_client_cert.pem")
_KEY_PATH: Final = Path(__file__).with_name("orvibo_client_key.pem")
_SERVER_CERT_SHA256: Final = {
    "china.orvibo.com": bytes.fromhex(
        "8985c4533a4ca280658edfca74dc19626015ef9fe04db30d576c28c3fec17344"
    )
}
_LOCK_PROPERTY_VALUES: Final = {
    "door_status": frozenset(("open", "closed", "on", "off")),
    "reverse_lock": frozenset(("locked", "unlocked", "on", "off")),
    "handle": frozenset(("up", "down")),
    "child_lock": frozenset(("locked", "unlocked", "on", "off")),
    "clild_lock": frozenset(("locked", "unlocked", "on", "off")),
}

LockEventCallback = Callable[["CloudLockEvent"], Awaitable[None]]


class CloudPushError(OrviboLanError):
    """A cloud push operation failed without exposing credentials or packets."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class CloudLockEvent:
    """Validated door-lock properties received from the cloud stream."""

    device_id: str
    uid: str
    properties: Mapping[str, object]
    update_time: int | None


def _identifier(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    return str(value).strip()


def _lock_property(key: str, value: object) -> object | None:
    allowed = _LOCK_PROPERTY_VALUES[key]
    if isinstance(value, bool):
        return value if key == "door_status" else None
    if isinstance(value, int) and not isinstance(value, bool):
        return value if key == "door_status" and value in (0, 1) else None
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in allowed else None


def parse_lock_event(packet: Mapping[str, Any]) -> CloudLockEvent | None:
    """Return a bounded door-lock event from one unsolicited cmd=42 packet."""

    command = packet.get("cmd")
    if isinstance(command, str):
        try:
            command = int(command.strip())
        except ValueError:
            return None
    if command != CMD_STATE_UPDATE or isinstance(command, bool):
        return None
    device_id = _identifier(packet.get("deviceId"))
    uid = _identifier(packet.get("uid"))
    if not device_id and not uid:
        return None
    raw_properties = packet.get("properties")
    if not isinstance(raw_properties, Mapping):
        return None
    properties: dict[str, object] = {}
    for key in _LOCK_PROPERTY_VALUES:
        if key not in raw_properties:
            continue
        value = _lock_property(key, raw_properties[key])
        if value is not None:
            properties[key] = value
    if not properties:
        return None
    raw_update_time = packet.get("updateTimeSec")
    update_time = (
        raw_update_time
        if isinstance(raw_update_time, int)
        and not isinstance(raw_update_time, bool)
        and raw_update_time >= 0
        else None
    )
    return CloudLockEvent(device_id, uid, properties, update_time)


class CloudPushClient:
    """Maintain one pinned mutual-TLS stream and deliver validated lock events."""

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        family_id: str,
        callback: LockEventCallback,
        *,
        reconnect_delay: float = _RECONNECT_DELAY,
    ) -> None:
        if host not in _SERVER_CERT_SHA256:
            raise ValueError("unsupported ORVIBO cloud push host")
        if not username or not password or not family_id:
            raise ValueError("cloud push credentials and family ID are required")
        self.host = host
        self._username = username
        self._password = password
        self._family_id = family_id
        self._callback = callback
        self._reconnect_delay = reconnect_delay
        self._stop = asyncio.Event()
        self._writer: asyncio.StreamWriter | None = None
        self._session_id = ID_UNSET
        self._dynamic_key: bytes | None = None
        self._serial = int(time.time())
        self._identifier = secrets.token_hex(8)
        self.connected = False

    async def run(self) -> None:
        """Reconnect until closed, keeping all untrusted details out of logs."""

        while not self._stop.is_set():
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # Keep the managed listener alive after network faults.
                if not self._stop.is_set():
                    reason = err.reason if isinstance(err, CloudPushError) else "transport"
                    _LOGGER.warning(
                        "ORVIBO cloud push disconnected (reason=%s, error=%s); retrying",
                        reason,
                        type(err).__name__,
                    )
            if not self._stop.is_set():
                try:
                    await asyncio.wait_for(self._stop.wait(), self._reconnect_delay)
                except TimeoutError:
                    pass

    async def async_update_credentials(self, username: str, password: str) -> None:
        """Use rotated transient credentials on the next connection generation."""

        if not username or not password:
            raise ValueError("cloud push credentials are required")
        if hmac.compare_digest(self._username, username) and hmac.compare_digest(
            self._password,
            password,
        ):
            return
        self._username = username
        self._password = password
        await self._close_writer()

    async def close(self) -> None:
        """Stop reconnecting and close the active stream."""

        self._stop.set()
        await self._close_writer()

    async def _run_once(self) -> None:
        self._session_id = ID_UNSET
        self._dynamic_key = None
        context = self._ssl_context()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                self.host,
                _PORT,
                ssl=context,
                server_hostname=self.host,
            ),
            _CONNECT_TIMEOUT,
        )
        self._writer = writer
        try:
            if self._stop.is_set():
                return
            self._verify_server_certificate(writer)
            await self._handshake(reader, writer)
            await self._login(reader, writer)
            self.connected = True
            _LOGGER.debug("ORVIBO cloud push listener connected")
            await self._stream(reader, writer)
        finally:
            self.connected = False
            if self._writer is writer:
                self._writer = None
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    def _ssl_context() -> ssl.SSLContext:
        if not _CERT_PATH.is_file() or not _KEY_PATH.is_file():
            raise CloudPushError("client_certificate_missing")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        # The endpoint uses a legacy self-signed certificate. It is pinned below
        # before credentials are sent, rather than trusted as a general-purpose CA.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.load_cert_chain(str(_CERT_PATH), str(_KEY_PATH))
        return context

    def _verify_server_certificate(self, writer: asyncio.StreamWriter) -> None:
        ssl_object = writer.get_extra_info("ssl_object")
        get_peer_certificate = getattr(ssl_object, "getpeercert", None)
        peer_certificate = (
            get_peer_certificate(binary_form=True)
            if callable(get_peer_certificate)
            else None
        )
        expected = _SERVER_CERT_SHA256[self.host]
        if not isinstance(peer_certificate, bytes) or not hmac.compare_digest(
            hashlib.sha256(peer_certificate).digest(),
            expected,
        ):
            raise CloudPushError("server_certificate_mismatch")

    def _next_serial(self) -> int:
        self._serial += 1
        return self._serial

    def _base_payload(self, command: int) -> dict[str, object]:
        return {
            "cmd": command,
            "serial": self._next_serial(),
            "clientType": 1,
            "uniSerial": int(time.time() * 1000),
            "serverRecord": False,
            "ver": _PUSH_SOFTWARE_VERSION,
            "debugInfo": _PUSH_DEBUG_INFO,
        }

    async def _handshake(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        payload = self._base_payload(0)
        payload.update(
            {
                "source": SOFTWARE_NAME,
                "softwareVersion": _PUSH_SOFTWARE_VERSION_CODE,
                "sysVersion": SYS_VERSION,
                "hardwareVersion": "Home Assistant",
                "language": LANGUAGE,
                "identifier": self._identifier,
                "phoneName": "Home Assistant",
            }
        )
        await self._send(writer, payload, dynamic=False)
        session_id, response = await self._receive_command(reader, 0)
        raw_key = response.get("key") or response.get("sessionKey")
        if not isinstance(raw_key, str):
            raise CloudPushError("handshake_key_missing")
        dynamic_key = raw_key.encode("utf-8")
        if len(dynamic_key) != 16:
            raise CloudPushError("handshake_key_invalid")
        self._dynamic_key = dynamic_key
        payload_session_id = _identifier(response.get("sessionId"))
        if payload_session_id:
            try:
                self._session_id = payload_session_id.encode("ascii")[:32].ljust(32, b"0")
            except UnicodeEncodeError as err:
                raise CloudPushError("handshake_session_invalid") from err
        else:
            self._session_id = session_id

    async def _login(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        payload = self._base_payload(CMD_LOGIN)
        payload.update(
            {
                "userName": self._username,
                "password": self._password,
                "familyId": self._family_id,
                "type": 4,
                "needAccountDetailError": True,
            }
        )
        await self._send(writer, payload)
        _, response = await self._receive_command(reader, CMD_LOGIN)
        if response.get("status") not in (None, 0, "0"):
            raise CloudPushError("login_rejected")

    async def _stream(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        while not self._stop.is_set():
            try:
                _, packet = await asyncio.wait_for(
                    self._read_packet(reader),
                    _HEARTBEAT_INTERVAL,
                )
            except TimeoutError:
                await self._send(writer, self._base_payload(CMD_HEARTBEAT))
                try:
                    _, packet = await asyncio.wait_for(
                        self._read_packet(reader),
                        _REQUEST_TIMEOUT,
                    )
                except TimeoutError as err:
                    raise CloudPushError("heartbeat_timeout") from err
            await self._dispatch_packet(packet)

    async def _dispatch_packet(self, packet: Mapping[str, Any]) -> None:
        event = parse_lock_event(packet)
        if event is None:
            return
        try:
            await self._callback(event)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.error(
                "ORVIBO cloud push callback failed (error=%s)",
                type(err).__name__,
            )

    async def _receive_command(
        self,
        reader: asyncio.StreamReader,
        command: int,
    ) -> tuple[bytes, dict[str, Any]]:
        async with asyncio.timeout(_REQUEST_TIMEOUT):
            while True:
                session_id, packet = await self._read_packet(reader)
                if packet.get("cmd") == command:
                    return session_id, packet

    async def _read_packet(
        self,
        reader: asyncio.StreamReader,
    ) -> tuple[bytes, dict[str, Any]]:
        prefix = await reader.readexactly(4)
        if prefix[:2] != MAGIC:
            raise CloudPushError("frame_magic_invalid")
        frame_length = struct.unpack(">H", prefix[2:4])[0]
        if not _HEADER_SIZE <= frame_length <= _MAX_FRAME_SIZE:
            raise CloudPushError("frame_length_invalid")
        frame = prefix + await reader.readexactly(frame_length - len(prefix))
        session_id = frame[10:42]
        keys = {ID_UNSET: DEFAULT_KEY.encode("utf-8")}
        if self._dynamic_key is not None:
            keys[self._session_id] = self._dynamic_key
            keys[session_id] = self._dynamic_key
        packet = parse_packet(frame, keys)
        if packet is None:
            raise CloudPushError("frame_decode_failed")
        return session_id, packet

    async def _send(
        self,
        writer: asyncio.StreamWriter,
        payload: Mapping[str, object],
        *,
        dynamic: bool = True,
    ) -> None:
        if dynamic and self._dynamic_key is None:
            raise CloudPushError("session_key_missing")
        key = self._dynamic_key if dynamic else DEFAULT_KEY.encode("utf-8")
        if key is None:
            raise CloudPushError("session_key_missing")
        packet = build_packet(
            DK_TYPE if dynamic else PK_TYPE,
            key,
            self._session_id,
            dict(payload),
        )
        writer.write(packet)
        await writer.drain()

    async def _close_writer(self) -> None:
        writer, self._writer = self._writer, None
        self.connected = False
        if writer is None:
            return
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
