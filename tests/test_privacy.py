"""Privacy-safe formatting tests."""

from custom_components.orvibo_lan.privacy import mask_host, mask_identifier


def test_mask_identifier_never_returns_the_full_value() -> None:
    assert mask_identifier("gateway-12345678") == "***5678"
    assert mask_identifier("1234") == "***"
    assert mask_identifier("") == "***"


def test_mask_host_hides_endpoint_address() -> None:
    result = mask_host("192.168.10.25")

    assert result == "192.168.10.*"
    assert "192.168.10.25" not in result
