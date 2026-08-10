"""Ranking Shorts pipeline.

A standalone sibling of ``artisan/youtube-shorts-pipeline``. That pipeline cuts
highlights out of one long source video; this one *builds* a video: it sources
many short organic clips, ranks them 5 -> 1, and composes a countdown with
overlays, voice-over and SFX using FFmpeg alone.

The two share no code, no config, no database and no upload tokens. That is
deliberate: the shorts pipeline is in production and must not be destabilised
by changes made for this one.
"""

__version__ = '0.1.0'
