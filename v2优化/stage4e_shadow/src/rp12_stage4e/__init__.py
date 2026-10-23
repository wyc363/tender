"""RP12 Stage 4E.0/4E.1 offline-only shadow utilities."""

from .context_resolver import ContextResolver, ContextResolutionError
from .rule_registry import build_scope_registry

__all__ = ["ContextResolver", "ContextResolutionError", "build_scope_registry"]
