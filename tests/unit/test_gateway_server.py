from __future__ import annotations

from assistant.gateway.server import _is_benign_windows_connection_reset


def test_is_benign_windows_connection_reset_matches_expected_proactor_error() -> None:
    error = ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host")
    error.winerror = 10054  # type: ignore[attr-defined]
    context = {
        "message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost()",
        "exception": error,
    }

    assert _is_benign_windows_connection_reset(context) is True


def test_is_benign_windows_connection_reset_rejects_other_contexts() -> None:
    error = ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host")
    error.winerror = 10054  # type: ignore[attr-defined]

    assert _is_benign_windows_connection_reset({"message": "different callback", "exception": error}) is False
    assert _is_benign_windows_connection_reset({"message": "Exception in callback _ProactorBasePipeTransport._call_connection_lost()", "exception": RuntimeError("boom")}) is False
