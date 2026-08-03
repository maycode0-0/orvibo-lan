"""Unit tests for the externally-sessioned Orvibo cloud client."""

from __future__ import annotations

import asyncio
import sys
import traceback
import types
import unittest
from collections.abc import Mapping
from dataclasses import dataclass
from unittest.mock import patch

try:
    import aiohttp  # type: ignore[import-not-found]
except ImportError:
    aiohttp = types.ModuleType("aiohttp")

    class ClientError(Exception):
        """Fake aiohttp client error."""

    @dataclass(frozen=True)
    class ClientTimeout:
        """Fake aiohttp timeout value."""

        total: float | None = None
        connect: float | None = None
        sock_read: float | None = None

    aiohttp.ClientError = ClientError
    aiohttp.ClientTimeout = ClientTimeout
    aiohttp.ClientSession = object
    sys.modules["aiohttp"] = aiohttp

from custom_components.orvibo_lan.lib import cloud_client

CloudApiError = cloud_client.CloudApiError
CloudClient = cloud_client.CloudClient
CloudHttpError = cloud_client.CloudHttpError
CloudJsonError = cloud_client.CloudJsonError
CloudSchemaError = cloud_client.CloudSchemaError
CloudTransportError = cloud_client.CloudTransportError


class FakeResponse:
    def __init__(
        self,
        payload: object = None,
        *,
        status: int = 200,
        json_error: Exception | None = None,
    ) -> None:
        self.status = status
        self._payload = payload
        self._json_error = json_error
        self.released = False

    async def json(self, *, content_type: object = None) -> object:
        del content_type
        if self._json_error is not None:
            raise self._json_error
        return self._payload

    def release(self) -> None:
        self.released = True


class FakeSession:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    async def get(self, url: str, **kwargs: object) -> FakeResponse:
        return self._request("GET", url, kwargs)

    async def post(self, url: str, **kwargs: object) -> FakeResponse:
        return self._request("POST", url, kwargs)

    def _request(
        self,
        method: str,
        url: str,
        kwargs: dict[str, object],
    ) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected request")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def oauth_payload() -> dict[str, object]:
    return {
        "status": 0,
        "data": {"access_token": "secret-token", "user_id": "user-1"},
    }


class CloudClientLoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_oauth_uses_form_post(self) -> None:
        session = FakeSession(
            [
                FakeResponse(oauth_payload()),
                FakeResponse({"code": 0, "data": {"familyInfo": []}}),
            ]
        )
        client = CloudClient(session, "account@example.com", "password")
        await client.login()
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.startswith("https://"))
        self.assertTrue(url.endswith("/getOauthToken"))
        self.assertNotIn("account@example.com", url)
        self.assertEqual(kwargs["allow_redirects"], False)
        self.assertIsInstance(kwargs["timeout"], aiohttp.ClientTimeout)
        self.assertNotIn("params", kwargs)
        data = kwargs["data"]
        self.assertIsInstance(data, Mapping)
        self.assertEqual(data["userName"], "account@example.com")
        self.assertEqual(len(data["password"]), 32)

    async def test_explicit_get_oauth_retains_compatibility(self) -> None:
        session = FakeSession(
            [
                FakeResponse(oauth_payload()),
                FakeResponse({"code": 0, "data": []}),
            ]
        )
        client = CloudClient(session, "account", "password", oauth_method="GET")
        await client.login()
        method, _, kwargs = session.calls[0]
        self.assertEqual(method, "GET")
        self.assertIn("params", kwargs)
        self.assertNotIn("data", kwargs)

    async def test_default_oauth_retries_get_when_post_response_is_incompatible(self) -> None:
        session = FakeSession(
            [
                FakeResponse({"status": 0, "data": None}),
                FakeResponse(oauth_payload()),
                FakeResponse({"code": 0, "data": {"familyInfo": []}}),
                FakeResponse(oauth_payload()),
                FakeResponse({"code": 0, "data": {"familyInfo": []}}),
            ]
        )
        client = CloudClient(session, "account", "password")

        await client.login()

        self.assertEqual([call[0] for call in session.calls], ["POST", "GET", "POST"])
        self.assertEqual(client.access_token, "secret-token")
        self.assertEqual(client.user_id, "user-1")

        await client.login()

        self.assertEqual(
            [call[0] for call in session.calls],
            ["POST", "GET", "POST", "GET", "POST"],
        )

    async def test_default_oauth_retries_get_for_unsupported_form_post(self) -> None:
        session = FakeSession(
            [
                FakeResponse({}, status=415),
                FakeResponse(oauth_payload()),
                FakeResponse({"code": 0, "data": {"familyInfo": []}}),
            ]
        )
        client = CloudClient(session, "account", "password")

        await client.login()

        self.assertEqual([call[0] for call in session.calls], ["POST", "GET", "POST"])

    async def test_login_parses_families_and_selects_first(self) -> None:
        family = {"familyId": "family-1", "familyName": "Home"}
        session = FakeSession(
            [
                FakeResponse(oauth_payload()),
                FakeResponse({"code": 0, "data": {"familyInfo": [family]}}),
            ]
        )
        client = CloudClient(session, "account", "password")
        self.assertTrue(await client.ensure_login())
        self.assertEqual(client.family_list, [family])
        self.assertEqual(client.family_id, "family-1")
        self.assertEqual(client.family_name, "Home")

    async def test_ensure_login_preserves_false_contract(self) -> None:
        client = CloudClient(
            FakeSession([FakeResponse({"status": 1, "code": 1})]),
            "a",
            "p",
        )
        self.assertFalse(await client.ensure_login())


class CloudClientErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_error_has_status_and_releases_response(self) -> None:
        response = FakeResponse({}, status=503)
        client = CloudClient(FakeSession([response]), "a", "p")
        with self.assertRaises(CloudHttpError) as raised:
            await client.login()
        self.assertEqual(raised.exception.status, 503)
        self.assertTrue(response.released)

    async def test_invalid_json_raises_explicit_error(self) -> None:
        responses = [
            FakeResponse(json_error=ValueError("bad post")),
            FakeResponse(json_error=ValueError("bad get")),
        ]
        session = FakeSession(responses)
        client = CloudClient(session, "a", "p")
        with self.assertRaises(CloudJsonError):
            await client.login()
        self.assertTrue(all(response.released for response in responses))
        self.assertEqual([call[0] for call in session.calls], ["POST", "GET"])

    async def test_non_object_json_raises_schema_error(self) -> None:
        responses = [FakeResponse([]), FakeResponse([])]
        session = FakeSession(responses)
        client = CloudClient(session, "a", "p")
        with self.assertRaises(CloudSchemaError):
            await client.login()
        self.assertTrue(all(response.released for response in responses))
        self.assertEqual([call[0] for call in session.calls], ["POST", "GET"])

    async def test_readtable_api_error_is_explicit(self) -> None:
        client = CloudClient(
            FakeSession(
                [
                    FakeResponse({"code": 7}),
                    FakeResponse(oauth_payload()),
                    FakeResponse({"code": 0, "data": {"familyInfo": []}}),
                    FakeResponse({"code": 7}),
                ]
            ),
            "a",
            "p",
        )
        client.access_token = "token"
        client.user_id = "user"
        with self.assertRaises(CloudApiError):
            await client.fetch_devices()

    async def test_timeout_is_transport_error_without_secret_leakage(self) -> None:
        secrets = (
            "private-user",
            "private-password",
            "secret-token",
            "https://",
            "cloud.invalid",
        )
        session = FakeSession(
            [
                asyncio.TimeoutError("private-password at https://cloud.invalid"),
                asyncio.TimeoutError("secret-token at https://cloud.invalid"),
            ]
        )
        client = CloudClient(session, secrets[0], secrets[1])
        client.access_token = secrets[2]

        with self.assertRaises(CloudTransportError) as raised:
            await client.login()

        self.assertEqual([call[0] for call in session.calls], ["POST"])

        rendered_error = "".join(
            traceback.format_exception(
                type(raised.exception),
                raised.exception,
                raised.exception.__traceback__,
            )
        )
        self.assertEqual(str(raised.exception), "OAuth login request failed")
        for secret in secrets:
            self.assertNotIn(secret, rendered_error)

        with patch.object(cloud_client._LOGGER, "debug") as debug:
            self.assertFalse(await client.ensure_login())
        self.assertEqual([call[0] for call in session.calls], ["POST", "POST"])
        rendered_log = " ".join(str(value) for call in debug.call_args_list for value in call.args)
        for secret in secrets:
            self.assertNotIn(secret, rendered_log)


class CloudClientDeviceTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_devices_reauthenticates_once_after_api_rejection(self) -> None:
        session = FakeSession(
            [
                FakeResponse({"code": 7}),
                FakeResponse(oauth_payload()),
                FakeResponse({"code": 0, "data": {"familyInfo": []}}),
                FakeResponse(
                    {
                        "code": 0,
                        "data": {
                            "device": [],
                            "deviceStatus": [],
                            "gateway": [],
                            "room": [],
                        },
                    }
                ),
            ]
        )
        client = CloudClient(session, "a", "p", family_id="family-1")
        client.access_token = "expired"
        client.user_id = "user"

        devices, *_ = await client.fetch_devices()

        self.assertEqual(devices, [])
        self.assertEqual([call[0] for call in session.calls], ["POST", "POST", "POST", "POST"])

    async def test_fetch_devices_preserves_six_item_contract(self) -> None:
        payload = {
            "code": 0,
            "data": {
                "device": [
                    {"deviceId": "gw", "deviceType": 114, "uid": "gw-1"},
                    {"deviceId": "switch", "deviceType": 1, "uid": "gw-1"},
                ],
                "deviceStatus": [{"deviceId": "switch", "value": 1}],
                "gateway": [
                    {"uid": "gw-1", "localStaticIP": "192.0.2.10:8088"},
                    {"uid": "other", "localStaticIP": "192.0.2.11"},
                ],
                "room": [{"roomId": "room-1", "roomName": "Office"}],
            },
        }
        session = FakeSession([FakeResponse(payload)])
        client = CloudClient(session, "a", "p", family_id="family-1")
        client.access_token = "token"
        client.user_id = "user"
        result = await client.fetch_devices()
        self.assertEqual(len(result), 6)
        devices, statuses, gateways, rooms, gateway_ips, resolver = result
        self.assertEqual(statuses["switch"]["value"], 1)
        self.assertEqual(gateway_ips, {"gw-1": "192.0.2.10"})
        self.assertEqual(resolver(devices[1]), "192.0.2.10")
        self.assertEqual(len(gateways), 2)
        self.assertEqual(rooms[0]["roomName"], "Office")

    async def test_invalid_status_item_is_schema_error(self) -> None:
        payload = {
            "code": 0,
            "data": {
                "device": [],
                "deviceStatus": [{}],
                "gateway": [],
                "room": [],
            },
        }
        client = CloudClient(FakeSession([FakeResponse(payload)]), "a", "p")
        client.access_token = "token"
        client.user_id = "user"
        with self.assertRaises(CloudSchemaError):
            await client.fetch_devices()

    async def test_logs_do_not_include_secrets_or_url(self) -> None:
        client = CloudClient(
            FakeSession([FakeResponse({"status": 1, "code": 1})]),
            "private-user",
            "private-password",
        )
        with patch.object(cloud_client._LOGGER, "debug") as debug:
            self.assertFalse(await client.ensure_login())
        rendered = " ".join(str(value) for call in debug.call_args_list for value in call.args)
        for secret in (
            "private-user",
            "private-password",
            "secret-token",
            "https://",
        ):
            self.assertNotIn(secret, rendered)


if __name__ == "__main__":
    unittest.main()
