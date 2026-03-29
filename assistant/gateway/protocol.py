"""WebSocket protocol models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ConnectAuth(BaseModel):
    token: str


class ConnectFrame(BaseModel):
    type: Literal["connect"]
    device_id: str
    auth: ConnectAuth


class HelloOkFrame(BaseModel):
    type: Literal["hello-ok"] = "hello-ok"


class RequestFrame(BaseModel):
    type: Literal["req"]
    id: str
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class ResponseFrame(BaseModel):
    type: Literal["res"] = "res"
    id: str
    ok: bool
    payload: dict[str, Any] | None = None
    error: str | None = None


class EventFrame(BaseModel):
    type: Literal["event"] = "event"
    event: str
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentSendParams(BaseModel):
    message: str
    session_key: str = "main"
    
class AgentListenParams(BaseModel):
    session_key: str = "main"
