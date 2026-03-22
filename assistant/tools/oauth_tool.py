"""OAuth tool definitions."""

from __future__ import annotations

from assistant.tools.registry import ToolDefinition


def build_oauth_tools(flow_manager, token_manager) -> list[ToolDefinition]:
    async def oauth_connect(payload):
        provider = str(payload["provider"]).lower()
        result = await flow_manager.start_oauth_flow(provider)
        return result

    async def oauth_status(_payload):
        connected = await token_manager.list_connected()
        return {"providers": connected}

    return [
        ToolDefinition(
            name="oauth_connect",
            description="Start an OAuth flow for a configured provider and return the authorization URL.",
            parameters={
                "type": "object",
                "properties": {"provider": {"type": "string", "enum": ["google", "github"]}},
                "required": ["provider"],
            },
            handler=oauth_connect,
        ),
        ToolDefinition(
            name="oauth_status",
            description="List connected OAuth providers and token expiry details.",
            parameters={"type": "object", "properties": {}},
            handler=oauth_status,
        ),
    ]
