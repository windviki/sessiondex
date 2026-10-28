"""Provider registry: agent name -> adapter instance."""
from __future__ import annotations

from pathlib import Path

from ..config import Config, default_provider_paths
from .base import Provider
from .claude import ClaudeProvider
from .dsh import DshProvider
from .kimi import KimiProvider
from .opencode import OpencodeProvider
from .qoder import QoderProvider
from .zcode import ZcodeProvider

PROVIDER_CLASSES: dict[str, type[Provider]] = {
    "claude": ClaudeProvider,
    "zcode": ZcodeProvider,
    "kimi": KimiProvider,
    "dsh": DshProvider,
    "qoder": QoderProvider,
    "opencode": OpencodeProvider,
}


def build_providers(
    cfg: Config | None = None, only: list[str] | None = None
) -> dict[str, Provider]:
    """Instantiate providers for every agent whose storage exists on disk."""
    paths = cfg.providers if cfg else default_provider_paths()
    out: dict[str, Provider] = {}
    for agent, cls in PROVIDER_CLASSES.items():
        if only and agent not in only:
            continue
        path = paths.get(agent)
        if path is None or not Path(path).exists():
            continue
        if agent in ("zcode", "opencode"):
            out[agent] = cls(path, cache_dir=(cfg.cache_dir if cfg else None))
        else:
            out[agent] = cls(path)
    return out


def provider_for(agent: str) -> Provider:
    """Build a single provider by agent name (used by the on-demand reader)."""
    cls = PROVIDER_CLASSES.get(agent)
    if cls is None:
        raise KeyError(f"unknown agent: {agent}")
    if agent in ("zcode", "opencode"):
        return cls()
    return cls()
