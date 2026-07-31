"""Tests for untrusted UDP gateway candidate discovery."""

import asyncio
from collections.abc import Callable

import pytest

from custom_components.orvibo_lan.lib.discovery import GatewayDiscovery, _DiscoveryProtocol
from custom_components.orvibo_lan.lib.packet import ID_UNSET
from custom_components.orvibo_lan.lib.protocol import DEFAULT_KEY, PK_TYPE, build_packet


def packet(uid: str) -> bytes:
    return build_packet(PK_TYPE, DEFAULT_KEY, ID_UNSET, {"cmd": 86, "uid": uid})


def test_protocol_accepts_only_known_uid_from_private_ipv4() -> None:
    protocol = _DiscoveryProtocol(frozenset({"known"}))
    protocol.datagram_received(packet("unknown"), ("192.168.1.2", 10000))
    protocol.datagram_received(packet("known"), ("8.8.8.8", 10000))
    protocol.datagram_received(packet("known"), ("127.0.0.1", 10000))
    protocol.datagram_received(packet("known"), ("192.168.1.20", 10000))

    assert set(protocol.candidates) == {"known"}
    assert protocol.candidates["known"].host == "192.168.1.20"


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent.append((data, addr))

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_discover_returns_candidates_and_closes_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = FakeTransport()

    async def create_endpoint(
        factory: Callable[[], _DiscoveryProtocol], **kwargs: object
    ) -> tuple[FakeTransport, _DiscoveryProtocol]:
        del kwargs
        protocol = factory()
        protocol.datagram_received(packet("known"), ("10.0.0.8", 10000))
        return transport, protocol

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "create_datagram_endpoint", create_endpoint)
    discovery = GatewayDiscovery(timeout=0)

    result = await discovery.discover({"known"})

    assert result["known"].host == "10.0.0.8"
    assert transport.sent
    assert transport.closed


@pytest.mark.asyncio
async def test_discover_with_no_known_uids_opens_no_transport() -> None:
    assert await GatewayDiscovery(timeout=0).discover(set()) == {}
