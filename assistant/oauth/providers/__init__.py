"""OAuth provider registry."""

from assistant.oauth.providers.github import GitHubOAuthProvider
from assistant.oauth.providers.google import GoogleOAuthProvider


def get_oauth_provider(name: str, config):
    normalized = name.lower()
    if normalized == "google":
        return GoogleOAuthProvider(config)
    if normalized == "github":
        return GitHubOAuthProvider(config)
    raise KeyError(f"Unknown OAuth provider '{name}'.")
