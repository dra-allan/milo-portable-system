"""
tools/base.py — base class for all tools.
========================================

Defines the standard interface for Milo tools, including JSON schema generation.
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, get_type_hints


class Tool:
    """Base class for Milo tools with automatic JSON schema generation."""

    name: str = ""
    description: str = ""

    def __init_subclass__(cls, **kwargs):
        """Ensure subclasses have required attributes."""
        super().__init_subclass__(**kwargs)
        if not cls.name:
            raise NotImplementedError("Tool subclasses must define a 'name' attribute")
        if not cls.description:
            raise NotImplementedError("Tool subclasses must define a 'description' attribute")

    def run(self, **kwargs) -> Any:
        """Execute the tool. Must be implemented by subclasses."""
        raise NotImplementedError("Tool subclasses must implement a 'run' method")

    def to_dict(self) -> dict[str, Any]:
        """Convert the tool to a dictionary representation."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.get_parameters_schema(),
        }

    def get_parameters_schema(self) -> dict[str, Any]:
        """Generate a JSON Schema for the tool's parameters based on the run method signature."""
        # Get the signature of the run method
        sig = inspect.signature(self.run)
        # Get type hints
        try:
            hints = get_type_hints(self.run)
        except Exception:
            hints = {}

        properties: dict[str, Any] = {}
        required: list[str] = []

        for param_name, param in sig.parameters.items():
            # Skip 'self' parameter
            if param_name == "self":
                continue

            # Get parameter type
            param_type = hints.get(param_name, str)  # Default to string if no hint

            # Convert Python type to JSON Schema type
            if param_type == str:
                json_type = "string"
            elif param_type == int:
                json_type = "integer"
            elif param_type == float:
                json_type = "number"
            elif param_type == bool:
                json_type = "boolean"
            elif param_type == list:
                json_type = "array"
            elif param_type == dict:
                json_type = "object"
            else:
                # For complex types, default to string
                json_type = "string"

            # Build property schema
            prop: dict[str, Any] = {"type": json_type}

            # Add description if available from parameter annotation or docstring
            # (Could be enhanced to parse docstrings in the future)

            properties[param_name] = prop

            # Check if parameter is required (no default value)
            if param.default is inspect.Parameter.empty:
                required.append(param_name)

        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }

        if required:
            schema["required"] = required

        return schema