"""Portable YouTube channel authentication and routing helpers.

``identity`` is the canonical channel-key -> YouTube-channel binding. All three
pipelines load it by path through their own ``src/channel_guard.py`` shim, so it
must stay free of pipeline imports.

``registry`` is the only thing allowed to write ``channels.yaml``. It edits
single lines rather than round-tripping the document, because the comments in
that file are the incident history.
"""

__all__ = ["auth", "identity", "registry"]
