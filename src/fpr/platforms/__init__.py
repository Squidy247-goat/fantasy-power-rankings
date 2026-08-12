"""Platform adapter registry.

Adapters are imported lazily so the package still works without the optional
platform dependencies installed. Someone running off a YAML roster file
shouldn't need espn-api on their machine.
"""

from __future__ import annotations

from fpr.platforms.base import (
    Credentials,
    MissingCredentials,
    Platform,
    PlatformError,
    normalize_status,
)

__all__ = [
    "Credentials",
    "MissingCredentials",
    "Platform",
    "PlatformError",
    "normalize_status",
    "available",
    "get",
    "credentials_for",
]

AVAILABLE = ("espn", "sleeper")


def available() -> tuple[str, ...]:
    return AVAILABLE


def get(name: str) -> Platform:
    key = (name or "").strip().lower()
    if key == "espn":
        from fpr.platforms.espn import ESPNPlatform

        return ESPNPlatform()
    if key == "sleeper":
        from fpr.platforms.sleeper import SleeperPlatform

        return SleeperPlatform()
    raise PlatformError(f"unknown platform {name!r}. Available: {', '.join(AVAILABLE)}")


def credentials_for(name: str, env_path: str = ".env") -> Credentials:
    key = (name or "").strip().lower()
    if key == "espn":
        from fpr.platforms.espn import credentials_from_env

        return credentials_from_env(env_path)
    if key == "sleeper":
        from fpr.platforms.sleeper import credentials_from_env

        return credentials_from_env(env_path)
    raise PlatformError(f"unknown platform {name!r}. Available: {', '.join(AVAILABLE)}")
