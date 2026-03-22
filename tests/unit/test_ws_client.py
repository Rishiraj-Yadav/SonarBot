from __future__ import annotations

from cli.ws_client import GatewayClient


def test_gateway_client_initializes_socket_slot(app_config) -> None:
    client = GatewayClient(app_config)
    assert client._socket is None
