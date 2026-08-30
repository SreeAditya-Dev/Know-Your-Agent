"""FastAPI delivery surface for the KYA gateway and operations dashboard."""

from kya.api.app import app, create_app

__all__ = ["app", "create_app"]
