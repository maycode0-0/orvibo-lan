"""Security regression tests for the legacy HTTPS compatibility client."""

import json
import logging

import pytest

from custom_components.orvibo_lan.lib.https_client import HttpsClient


class FakeResponse:
    async def text(self) -> str:
        return json.dumps(
            {
                "status": 0,
                "data": {
                    "access_token": "private-token",
                    "user_id": "private-user-id",
                },
            }
        )


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append((url, kwargs))
        return FakeResponse()


@pytest.mark.asyncio
async def test_legacy_oauth_uses_post_without_logging_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = FakeSession()
    client = HttpsClient("private-account", "private-password")
    caplog.set_level(
        logging.DEBUG,
        logger="custom_components.orvibo_lan.lib.https_client",
    )

    await client._ensure_token(session)  # type: ignore[arg-type]

    assert len(session.calls) == 1
    url, kwargs = session.calls[0]
    assert url.endswith("/getOauthToken")
    assert "private-account" not in url
    assert "params" not in kwargs
    data = kwargs["data"]
    assert isinstance(data, dict)
    assert data["userName"] == "private-account"
    assert len(str(data["password"])) == 32
    assert kwargs["allow_redirects"] is False
    assert "private-account" not in caplog.text
    assert "private-password" not in caplog.text
    assert "private-token" not in caplog.text
    assert "private-user-id" not in caplog.text
