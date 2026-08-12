"""Pipeline-wide audio defaults for the Shorts renderer.

Loaded automatically by Python when the pipeline is launched from its folder.
Keeps the renderer backward-compatible while making music less repetitive:
- music defaults to a more audible 28% bed level
- every render seeks to a random point in the selected track before looping
"""
from __future__ import annotations
import os
import random
import subprocess as _subprocess

# Let an explicit .env value win, otherwise use the louder default requested
# for Shorts. The existing config reads this during import.
os.environ.setdefault("MUSIC_VOLUME", "0.28")

_original_run = _subprocess.run


def _music_duration(path: str) -> float:
    try:
        probe = _original_run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=15,
        )
        value = float((probe.stdout or "").strip())
        return value if value > 1.0 else 0.0
    except Exception:
        return 0.0


def _run(command, *args, **kwargs):
    if isinstance(command, (list, tuple)):
        cmd = list(command)
        # Only touch the Shorts music input, never the source-video seek.
        if "-stream_loop" in cmd and "-1" in cmd:
            try:
                loop_i = cmd.index("-stream_loop")
                input_i = cmd.index("-i", loop_i)
                track = str(cmd[input_i + 1])
                if track.lower().endswith((".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac", ".opus")):
                    duration = _music_duration(track)
                    if duration > 1.0 and "-ss" not in cmd[loop_i:input_i]:
                        offset = random.uniform(0.0, max(0.0, duration - 0.5))
                        cmd[input_i:input_i] = ["-ss", f"{offset:.3f}"]
                        command = cmd
            except (ValueError, IndexError, TypeError):
                pass
    return _original_run(command, *args, **kwargs)


_subprocess.run = _run
