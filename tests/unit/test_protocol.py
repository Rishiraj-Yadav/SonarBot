from __future__ import annotations

import pytest
from pydantic import ValidationError

from assistant.gateway.protocol import ConnectFrame, RequestFrame


def test_valid_request_frame() -> None:
    frame = RequestFrame.model_validate(
        {"type": "req", "id": "1", "method": "agent.send", "params": {"message": "hello"}}
    )
    assert frame.method == "agent.send"


def test_invalid_connect_frame_missing_token() -> None:
    with pytest.raises(ValidationError):
        ConnectFrame.model_validate({"type": "connect", "device_id": "cli", "auth": {}})
