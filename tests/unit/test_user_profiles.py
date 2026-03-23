from __future__ import annotations

import pytest

from assistant.users import UserProfileStore


@pytest.mark.asyncio
async def test_user_profiles_auto_link_identities_to_default_user(app_config) -> None:
    store = UserProfileStore(app_config)
    await store.initialize()

    telegram_user = await store.resolve_user_id(
        "telegram",
        "12345",
        {"channel": "telegram", "chat_id": "12345"},
    )
    webchat_user = await store.resolve_user_id(
        "webchat",
        "device-1",
        {"channel": "webchat", "device_id": "device-1"},
    )

    assert telegram_user == app_config.users.default_user_id
    assert webchat_user == app_config.users.default_user_id

    profile = await store.get_profile(app_config.users.default_user_id)
    assert "telegram" in profile["linked_channels"]
    assert "webchat" in profile["linked_channels"]

    identity = await store.get_identity(app_config.users.default_user_id, "telegram")
    assert identity is not None
    assert identity["metadata"]["chat_id"] == "12345"


@pytest.mark.asyncio
async def test_default_user_profile_is_synced_from_config(app_config) -> None:
    store = UserProfileStore(app_config)
    await store.initialize()
    await store.set_primary_channel(app_config.users.default_user_id, "webchat")

    app_config.users.primary_channel = "telegram"
    app_config.users.fallback_channels = ["webchat"]

    synced_store = UserProfileStore(app_config)
    await synced_store.initialize()
    profile = await synced_store.get_profile(app_config.users.default_user_id)

    assert profile["primary_channel"] == "telegram"
    assert profile["fallback_channels"] == ["webchat"]
