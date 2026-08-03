"""
tools/__init__.py — tool registry and discovery.
================================================

Automatically discovers Tool subclasses in the miloctl.tools package.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Dict, List, Type

from .base import Tool

# Registry of tool classes by name
_REGISTRY: dict[str, Type[Tool]] = {}


def _discover_tools():
    """Discover all Tool subclasses in the miloctl.tools package."""
    # Clear existing registry
    _REGISTRY.clear()

    # Import all modules in the tools package
    package = __name__
    for _, module_name, _ in pkgutil.iter_modules(__path__):
        try:
            module = importlib.import_module(f".{module_name}", package)
            # Look for Tool subclasses in the module
            for name in dir(module):
                obj = getattr(module, name)
                if (
                    isinstance(obj, type)
                    and issubclass(obj, Tool)
                    and obj is not Tool
                ):
                    # Create a temporary instance to get the name
                    try:
                        instance = obj()
                        _REGISTRY[instance.name] = obj
                    except Exception:
                        # Skip if we can't instantiate (e.g., abstract class)
                        pass
        except Exception:
            # Skip modules that fail silently skip modules that fail to load
            pass


def registry() -> dict[str, Type[Tool]]:
    """Get the tool registry, discovering tools if needed."""
    if not _REGISTRY:
        _discover_tools()
    return _REGISTRY


def all() -> list[Type[Tool]]:
    """Get all registered tool classes."""
    return list(registry().values())


def get(name: str) -> type[Tool] | None:
    """Get a tool class by name."""
    return registry().get(name)


# Discover tools when module is imported
_discover_tools()