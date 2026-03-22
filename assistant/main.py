"""ASGI entry point for the SonarBot gateway."""

from assistant.gateway.server import create_app

app = create_app()
