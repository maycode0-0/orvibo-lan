"""Pure unit tests for the strict packet codec."""

import json
import struct
from collections.abc import Callable
from typing import Type

import pytest

from custom_components.orvibo_lan.exceptions import (
    InvalidCrcError,
    InvalidLengthError,
    InvalidMagicError,
    InvalidPacketTypeError,
    InvalidPayloadError,
    InvalidSessionError,
    ProtocolError,
)
from custom_components.orvibo_lan.lib.packet import build_packet as legacy_build_packet
from custom_components.orvibo_lan.lib.protocol import (
    DEFAULT_KEY,
    DK_TYPE,
    PK_TYPE,
    _crc32,
    _encrypt,
    build_packet,
    decode_packet,
    parse_packet,
)

SESSION = b"s" * 32
SESSION_KEY = b"0123456789abcdef"


def test_build_is_wire_compatible_with_legacy_builder() -> None:
    payload = {"cmd": 42, "deviceId": "device-1", "online": True}

    assert build_packet(PK_TYPE, DEFAULT_KEY, SESSION, payload) == legacy_build_packet(
        PK_TYPE, DEFAULT_KEY, SESSION, payload
    )


def test_pk_roundtrip_uses_default_key_and_returns_dict() -> None:
    packet = build_packet(PK_TYPE, DEFAULT_KEY, SESSION, {"cmd": 0})

    assert parse_packet(packet, {}) == {"cmd": 0}


def test_dk_roundtrip_requires_matching_session() -> None:
    packet = build_packet(DK_TYPE, SESSION_KEY, SESSION, {"cmd": 15})

    decoded = decode_packet(packet, {SESSION: SESSION_KEY})
    assert decoded.packet_type == DK_TYPE
    assert decoded.session_id == SESSION
    assert decoded.payload["cmd"] == 15
    with pytest.raises(InvalidSessionError):
        parse_packet(packet, {})


@pytest.mark.parametrize(
    ("mutator", "error_type"),
    [
        (lambda packet: b"xx" + packet[2:], InvalidMagicError),
        (lambda packet: packet[:4] + b"zz" + packet[6:], InvalidPacketTypeError),
        (
            lambda packet: packet[:6] + b"\x00\x00\x00\x00" + packet[10:],
            InvalidCrcError,
        ),
        (
            lambda packet: packet[:2] + struct.pack(">H", len(packet) + 1) + packet[4:],
            InvalidLengthError,
        ),
    ],
)
def test_rejects_malformed_headers(
    mutator: Callable[[bytes], bytes], error_type: Type[ProtocolError]
) -> None:
    packet = build_packet(PK_TYPE, DEFAULT_KEY, SESSION, {"cmd": 0})

    with pytest.raises(error_type):
        parse_packet(mutator(packet), {})


def test_rejects_non_dict_json_payload() -> None:
    packet = build_packet(PK_TYPE, DEFAULT_KEY, SESSION, {"cmd": 0})
    encrypted = _encrypt(DEFAULT_KEY, json.dumps([1, 2]).encode())
    malformed = packet[:2] + struct.pack(">H", 42 + len(encrypted))
    malformed += packet[4:6] + _crc32(encrypted) + packet[10:42] + encrypted

    with pytest.raises(InvalidPayloadError):
        parse_packet(malformed, {})


def test_builder_validates_type_session_and_payload() -> None:
    with pytest.raises(InvalidPacketTypeError):
        build_packet(b"zz", DEFAULT_KEY, SESSION, {})
    with pytest.raises(InvalidSessionError):
        build_packet(PK_TYPE, DEFAULT_KEY, b"short", {})
    with pytest.raises(InvalidPayloadError):
        build_packet(PK_TYPE, DEFAULT_KEY, SESSION, json.loads("[]"))
