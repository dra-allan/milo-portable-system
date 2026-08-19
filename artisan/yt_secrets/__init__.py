"""Portable YouTube channel authentication and routing helpers.

``identity`` is the canonical channel-key -> YouTube-channel binding. All three
pipelines load it by path through their own ``src/channel_guard.py`` shim, so it
must stay free of pipeline imports.
"""

__all__ = ["auth", "identity"]
