"""Path discovery and runtime configuration.

All defaults are derived from the user's home directory at runtime so the
repository contains no machine-specific paths. Every location can be
overridden with a ``SESSIONDEX_<NAME>`` environment variable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "sessiondex"


def _home() -> Path:
    return Path(os.environ.get("SESSIONDEX_FAKE_HOME", Path.home()))


@dataclass(frozen=True)
class Config:
    """Resolved runtime configuration."""

    index_path: Path
    cache_dir: Path
    host: str
    port: int
    token: str | None
    scan_interval: int  # seconds between background scans in `serve`; 0 disables
    providers: dict[str, Path]  # agent name -> storage root/file

    def provider_path(self, agent: str) -> Path | None:
        return self.providers.get(agent)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def default_provider_paths() -> dict[str, Path]:
    """Default session-storage locations for each supported agent."""
    home = _home()
    override = os.environ.get("SESSIONDEX_PROVIDERS_DIR")
    paths = {
        "claude": home / ".claude",
        "zcode": home / ".zcode" / "cli" / "db" / "db.sqlite",
        "kimi": home / ".kimi-code",
        "dsh": home / ".dsh",
        "qoder": home / ".qoder-cn",
        "opencode": home / ".local" / "share" / "opencode" / "opencode.db",
    }
    if override:  # tests / multi-machine setups: SESSIONDEX_PROVIDERS_DIR=<root>
        root = Path(override)
        paths = {
            "claude": root / "claude",
            "zcode": root / "zcode" / "db.sqlite",
            "kimi": root / "kimi",
            "dsh": root / "dsh",
            "qoder": root / "qoder",
            "opencode": root / "opencode" / "opencode.db",
        }
    resolved: dict[str, Path] = {}
    env_names = {
        "claude": "SESSIONDEX_CLAUDE_DIR",
        "zcode": "SESSIONDEX_ZCODE_DB",
        "kimi": "SESSIONDEX_KIMI_DIR",
        "dsh": "SESSIONDEX_DSH_DIR",
        "qoder": "SESSIONDEX_QODER_DIR",
        "opencode": "SESSIONDEX_OPENCODE_DB",
    }
    for agent, path in paths.items():
        env_val = os.environ.get(env_names[agent])
        resolved[agent] = Path(env_val).expanduser() if env_val else path
    return resolved


def load_config() -> Config:
    cache_dir = Path(_env("SESSIONDEX_CACHE_DIR", str(_home() / ".cache" / APP_NAME)))
    index_path = Path(_env("SESSIONDEX_INDEX", str(cache_dir / "index.sqlite")))
    return Config(
        index_path=index_path,
        cache_dir=cache_dir,
        host=_env("SESSIONDEX_HOST", "127.0.0.1"),
        port=int(_env("SESSIONDEX_PORT", "8765")),
        token=os.environ.get("SESSIONDEX_TOKEN") or None,
        scan_interval=int(_env("SESSIONDEX_SCAN_INTERVAL", "300")),
        providers=default_provider_paths(),
    )
