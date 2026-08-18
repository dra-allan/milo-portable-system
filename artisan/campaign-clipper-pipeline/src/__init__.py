"""Campaign clipper pipeline.

Takes campaign source *video files* (pulled from a campaign content folder),
cuts compliant vertical clips, burns the operator's own text, stamps the
required logo, validates the result against that campaign's requirements,
uploads to the eligible account, then submits the published link back to the
campaign board.

Deliberately not a discovery pipeline: the source pool is handed to you by the
campaign, so there is no search, no yt-dlp, and no candidate ranking.
"""

__all__ = ['config', 'spec', 'database', 'sources', 'segmenter', 'overlay',
           'renderer', 'validator', 'captions', 'publisher', 'clipster',
           'compiler', 'cleanup', 'intake', 'opencli_bridge', 'main']
