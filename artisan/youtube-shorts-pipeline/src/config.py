"""Configuration loading for the shorts pipeline.

Two problems fixed here:

1. ``config = Config()`` ran at import time and called ``open(niches.yaml)``
   with no guard. A missing or malformed niches.yaml raised during *import*,
   so every module that did ``from config import config`` died with a
   traceback that pointed at the import line instead of the real cause.
2. ``TEMP_DIR=./data/temp`` was resolved against the current working
   directory. Launching from anywhere other than the project root silently
   created a second data tree, so downloads "vanished" between runs.
   All relative paths are now anchored to the project root.
"""

import os
from pathlib import Path
from typing import List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is optional
    def load_dotenv(*_args, **_kwargs):
        return False

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Sensible defaults so a fresh clone runs without any niches.yaml at all.
DEFAULT_NICHE = {
    'channels': [],
    'channel': '',
    'upload_channels': [],
    'keywords': [],
    'min_duration': 300,
    'max_duration': 7200,
    'min_score': 0.0,
    'min_views': 0,
    'max_videos': 0,
    # Burn word-level captions onto this niche's Shorts. Set false for
    # non-English niches (e.g. Luganda): Whisper transcribes them badly, so
    # captions would be confidently wrong -- and skipping them also removes
    # the accurate caption pass + the subtitles filter from the render,
    # which is the two most expensive things per clip.
    'captions': True,
    # Whisper language hint ('' = autodetect). A correct hint removes the
    # detection pass and stops Whisper hallucinating English.
    'whisper_language': '',
    # Opt-in countdown/list scoring (enumeration cues, #1 payoff,
    # superlatives). Off by default so existing niches score unchanged.
    'ranking_mode': False,
}


def _resolve(path_value: str) -> Path:
    """Resolve a configured path against the project root, not the CWD."""
    p = Path(str(path_value)).expanduser()
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p.resolve()


class Config:
    def __init__(self, env_file=None):
        if env_file is None:
            env_file = PROJECT_ROOT / '.env'
        env_file = Path(env_file)
        self.env_file = env_file
        self.project_root = PROJECT_ROOT
        self.env_loaded = env_file.exists()
        if self.env_loaded:
            load_dotenv(env_file)
        else:
            # Fall back to the ambient environment; `doctor` reports this.
            load_dotenv()

        # --- YouTube auth -------------------------------------------------
        self.youtube_api_key = os.getenv('YOUTUBE_API_KEY') or None
        creds = os.getenv('GOOGLE_APPLICATION_CREDENTIALS') or None
        self.google_credentials_path = str(_resolve(creds)) if creds else None
        self.oauth_client_secrets = os.getenv('YOUTUBE_OAUTH_CLIENT_SECRETS') or None
        if self.oauth_client_secrets:
            self.oauth_client_secrets = str(_resolve(self.oauth_client_secrets))
        self.oauth_token_file = str(
            _resolve(os.getenv('YOUTUBE_OAUTH_TOKEN_FILE', 'config/youtube_token.json'))
        )
        self.oauth_client_secrets_dir = str(
            _resolve(os.getenv('YOUTUBE_OAUTH_CLIENT_SECRETS_DIR', 'config'))
        )

    def oauth_client_secrets_for(self, channel: Optional[str] = None) -> Optional[str]:
        """Resolve the OAuth client-secrets file for a channel.

        Each channel can bind to its own Google Cloud project (and therefore
        its own daily upload quota) via ``config/youtube_client_secrets_<channel>.json``.
        Falls back to the shared client-secrets file when no per-channel file
        exists, then to None.
        """
        if channel:
            per_channel = Path(self.oauth_client_secrets_dir) / f'youtube_client_secrets_{channel}.json'
            if per_channel.exists():
                return str(per_channel.resolve())
        if self.oauth_client_secrets and Path(self.oauth_client_secrets).exists():
            return self.oauth_client_secrets
        return None

        # --- Processing limits -------------------------------------------
        self.max_concurrent_videos = self._int('MAX_CONCURRENT_VIDEOS', 3, minimum=1)
        self.min_segment_length = self._int('MIN_SEGMENT_LENGTH', 15, minimum=1)
        self.max_segment_length = self._int('MAX_SEGMENT_LENGTH', 60, minimum=1)
        if self.min_segment_length > self.max_segment_length:
            self.min_segment_length, self.max_segment_length = (
                self.max_segment_length, self.min_segment_length
            )
        self.max_clips_per_video = self._int('MAX_CLIPS_PER_VIDEO', 5, minimum=1)
        # Cap for sources the feedback loop has proven (avg views >= 200). A
        # winning channel gets more clips per pull than a first-timer, so the
        # expensive download/transcribe pays off more often.
        self.max_clips_per_video_winner = self._int(
            'MAX_CLIPS_PER_VIDEO_WINNER', 8, minimum=1
        )
        # 200+ avg views = proven winner (matches discovery._source_rank).
        self.winner_avg_views = self._int('WINNER_AVG_VIEWS', 200, minimum=0)
        self.min_gap_between_clips = self._int('MIN_GAP_BETWEEN_CLIPS', 30, minimum=0)

        # --- Whisper -----------------------------------------------------
        self.whisper_model = os.getenv('WHISPER_MODEL', 'base')
        self.whisper_device = os.getenv('WHISPER_DEVICE', 'cpu')

        # --- Transcription tuning (the 85%-of-runtime stage) -------------
        # Two passes with different tradeoffs:
        #   discovery -- fast + cheap, only used to FIND highlights.
        #   caption   -- accurate + word-level, run on only the chosen clips.
        # The old code hardcoded beam_size=5 + word_timestamps=True for the
        # whole file, which is what OOMs on a 4 GB box and forces the slow
        # chunked fallback.
        self.transcribe_model = os.getenv('TRANSCRIBE_MODEL') or 'tiny'
        self.transcribe_beam = self._int('TRANSCRIBE_BEAM', 1, minimum=1)
        self.transcribe_word_timestamps = self._bool('TRANSCRIBE_WORD_TIMESTAMPS', False)
        self.transcribe_vad = self._bool('TRANSCRIBE_VAD', True)
        # 0 = transcribe the whole source. N = only the first N minutes.
        # Default 0: truncating the source throws away clips, and the goal is
        # "as many good clips as possible". Opt in for fast discovery runs.
        self.transcribe_max_minutes = self._int('TRANSCRIBE_MAX_MINUTES', 0, minimum=0)
        # Window size for the memory-safe long-file path. faster-whisper
        # builds a full-file mel array, so a hard cap on how much audio is in
        # flight is what actually prevents the OOM.
        # 15 min windows were both slow AND the direct cause of the
        # "mkl_malloc: failed to allocate memory" window failures in the logs:
        # a 15-minute mel array plus the model plus a decode context does not
        # fit in a ~4 GB box, so window 2 died and a fifth of the source was
        # silently dropped. 5 minutes keeps peak memory ~3x lower and lets
        # windows run in parallel (see transcribe_workers).
        self.transcribe_window_minutes = self._int(
            'TRANSCRIBE_WINDOW_MINUTES', 5, minimum=1
        )
        # 0 => let ctranslate2 pick. On a small box explicitly pinning threads
        # to the core count avoids oversubscription when windows run in
        # parallel (each worker then gets cores/workers threads).
        self.transcribe_threads = self._int('TRANSCRIBE_THREADS', 0, minimum=0)
        # Parallel windows. Whisper on CPU does NOT scale linearly with
        # threads -- a single ctranslate2 model saturates ~2-4 threads and then
        # flattens out. Running independent windows concurrently is what
        # actually uses the rest of the machine. Windows are independent by
        # construction here (condition_on_previous_text=False), so this is
        # safe. Default: cores/2, capped at 4, so memory stays bounded.
        self.transcribe_workers = self._int(
            'TRANSCRIBE_WORKERS', max(1, min(4, (os.cpu_count() or 2) // 2)),
            minimum=1,
        )
        # Skip transcription entirely when YouTube already published a
        # transcript. Downloading subtitles takes ~1 second versus ~50 minutes
        # of Whisper for an hour-long source: by far the largest single speed
        # win available, and it is free.
        self.use_youtube_subs = self._bool('USE_YOUTUBE_SUBS', True)

        # Caption pass: only ever runs on the selected clips (a few minutes of
        # audio total), so it can afford to be accurate.
        self.caption_model = os.getenv('CAPTION_MODEL') or 'base'
        self.caption_beam = self._int('CAPTION_BEAM', 5, minimum=1)
        # Master switch for the two-pass design. Off => captions come from the
        # discovery transcript (faster, less precise).
        self.two_pass_captions = self._bool('TWO_PASS_CAPTIONS', True)

        # --- Download tuning ---------------------------------------------
        # Audio-only discovery fetch: ~40 MB for an hour instead of 1-2 GB.
        self.download_audio_only = self._bool('DOWNLOAD_AUDIO_ONLY', True)
        # Fetch only the chosen clip ranges as separate small files rather
        # than the entire source video.
        self.download_sections = self._bool('DOWNLOAD_SECTIONS', True)
        # Padding around each section so a later timing nudge needs no
        # re-download, and so keyframe drift lands inside slack we own.
        self.section_padding = self._float('SECTION_PADDING', 8.0, minimum=0.0)
        # Source resolution ceiling. A vertical Short is 1080x1920, and smart
        # framing crops *into* the source -- a 1080p landscape frame cropped to
        # a 9:16 tile is only ~608px wide, which then has to be upscaled. So
        # allowing 1440p+ genuinely helps when the source offers it, and costs
        # nothing when it does not (the format selector just falls through).
        # 1440 was chosen for smart-crop headroom, but it also multiplies the
        # bytes fetched and the pixels every filter has to touch. A 9:16 tile
        # cropped from 1080p is 608px wide and upscaled to 1080 -- fine after
        # lanczos, and roughly half the download and decode cost.
        self.download_height = self._int('DOWNLOAD_HEIGHT', 1080, minimum=240)
        # Section fetches are network-bound, not CPU-bound: they overlap almost
        # perfectly. 4 is well under YouTube's per-client rate limiting.
        self.download_concurrency = self._int('DOWNLOAD_CONCURRENCY', 4, minimum=1)
        # Channel listings during discovery are metadata-only HTTP requests
        # (~2-3s each, serial). With 20+ channels per niche that is a minute of
        # pure waiting before any real work starts. They are independent, so
        # they parallelise cleanly.
        self.discovery_workers = self._int('DISCOVERY_WORKERS', 8, minimum=1)

        # --- Render tuning -----------------------------------------------
        # Measured (see BENCHMARKS.md): parallel ffmpeg encodes give only
        # 1.02-1.06x on a 2-core box, because libx264 already saturates every
        # core -- two encodes just split the same CPU and double the memory.
        # So scale with core count instead of blindly defaulting to 2, which
        # is what the original plan called for.
        # NOTE: that measurement was taken with preset=slow, where libx264 does
        # saturate every core. At the new default preset ('veryfast') a single
        # encode leaves cores idle, and with a hardware encoder the CPU is
        # nearly free -- so parallel renders now do help. Still bounded,
        # because each worker holds its own filtergraph in memory.
        self.render_workers = self._int(
            'RENDER_WORKERS', max(1, min(3, (os.cpu_count() or 2) // 2)), minimum=1
        )
        # The blurred-backdrop fill was the single most expensive filter in
        # the chain (full-res gblur every frame). 'cheap' downscales before
        # blurring for a visually identical result at a fraction of the cost.
        # Use 'crop' to fill frame without bars, 'black' for solid bars, or 'cheap'/'blur' for blurred bars
# Use 'smart' for intelligent person-aware cropping (face detection based)
        self.background_mode = (os.getenv('BACKGROUND_MODE') or 'crop').lower()
        if self.background_mode not in ('cheap', 'blur', 'black', 'crop', 'smart'):
            self.background_mode = 'crop'
        # How many highlight clips a source video may produce. Allan's rule:
        # render the TOP 12 detected segments per source so the queue can't
        # explode into a 300+ clip backlog. This is the real render count:
        # the planner (processor.py) uses max_candidates over max_clips when
        # both are set, so keep this the smaller, intentional cap.
        self.max_candidates = self._int('MAX_CANDIDATES', 12, minimum=1)

        # --- Upload behaviour --------------------------------------------
        # Default to NOT uploading: an unattended pipeline that publishes to a
        # live channel on its first successful run is a footgun.
        self.upload_enabled = self._bool('UPLOAD_ENABLED', False)
        self.privacy_status = os.getenv('PRIVACY_STATUS', 'private').lower()
        if self.privacy_status not in ('public', 'private', 'unlisted'):
            self.privacy_status = 'private'
        # Hard cap on how many Shorts a single run may publish. YouTube's
        # default Data API quota is ~10,000 units/day and one upload costs
        # ~1,600, so only ~6 uploads fit per day; 20+ clips from one video
        # would blow that instantly, so we throttle and let backlog drain it.
        self.upload_max_per_run = self._int('UPLOAD_MAX_PER_RUN', 0, minimum=0)  # 0 = unlimited (per-channel cap is the real gate)
        # Hard cap on how many Shorts the same source video may publish per day.
        # Allan's cadence rule: never post more than 3 clips from one source
        # video in 24h (the algo test-fights identical-source bursts). The
        # backlog drain would otherwise dump every clip of a rich source in one
        # sweep -- exactly the "5 identical-title shorts in 9 minutes" burst we
        # saw 2026-08-09.
        self.upload_max_per_source = self._int('UPLOAD_MAX_PER_SOURCE', 3, minimum=1)
        # Hard cap on how many Shorts each channel may publish per day.
        # Allan's cadence rule: max 6 shorts per channel per 24h. Counts across
        # all sweeps (9AM/2PM/7PM), so three runs can't stack 18 onto one
        # channel -- the round-robin and the per-channel budget together keep
        # every channel at a steady, algo-friendly 6/day max.
        self.upload_max_per_channel = self._int('UPLOAD_MAX_PER_CHANNEL', 6, minimum=1)
        # When a run has room left in its cap, fill it with older clips that
        # were rendered but never uploaded (the "new mixed with old" queue).
        self.upload_backlog = self._bool('UPLOAD_BACKLOG', True)
        # Channel key used when a niche has no explicit `channel:` binding.
        self.upload_default_channel = (os.getenv('UPLOAD_DEFAULT_CHANNEL') or '').strip()
        # Run raw clip hooks through the rule-based title optimizer when
        # generating Short titles. Off => the raw hook is used verbatim.
        self.title_optimizer = self._bool('TITLE_OPTIMIZER', True)
        # Multichannel upload mode: how to distribute Shorts among the channels bound to a niche.
        # Options: 'round_robin' (default), 'all', 'first'.
        self.multichannel_upload_mode = os.getenv('MULTICHANNEL_UPLOAD_MODE', 'round_robin').lower()
        if self.multichannel_upload_mode not in ('round_robin', 'all', 'first'):
            self.multichannel_upload_mode = 'round_robin'
        # Max clips to generate from one source video per discovery cycle.
        # Prevents a single rich source from flooding the queue.
        self.max_clips_per_source_per_cycle = self._int('MAX_CLIPS_PER_SOURCE_PER_CYCLE', 2, minimum=1)
        # Max un-uploaded clips allowed per source video in the queue.
        # Older clips beyond this are not generated (they'd just sit capped).
        self.max_queued_per_source = self._int('MAX_QUEUED_PER_SOURCE', 3, minimum=1)

        # --- Scheduled discovery -----------------------------------------
        # Candidates pulled per channel before dedup/filtering. Must be >=
        # schedule_max_videos so already-processed videos can't starve a run.
        self.discovery_lookback = self._int('DISCOVERY_LOOKBACK', 10, minimum=1)
        # Default cap on videos STARTED per scheduled run per niche. A niche
        # can override with `max_videos:` in niches.yaml; SCHEDULE_MAX_TOTAL
        # (below) still clamps the whole sweep so a stack of hungry niches
        # can't blow the day's quota in one run. Quota: ~10k units/day, one
        # upload ~1600 -> ~6 uploads/day, so a few videos/run is the norm.
        self.schedule_max_videos = self._int('SCHEDULE_MAX_VIDEOS', 3, minimum=1)
        # Hard ceiling on videos started across ALL niches in one sweep.
        # 0 = unlimited (per-niche caps rule). Default matches the upload
        # quota reality: ~6 uploads/day.
        self.schedule_max_total = self._int('SCHEDULE_MAX_TOTAL', 6, minimum=0)
        # Pull-once model (Allan's design): a sweep that already has un-uploaded
        # clips for a niche should POST those instead of pulling+downloading a
        # new source. Only pull again when the clip supply runs out. This is
        # what turns "3 downloads a day" into "1 download, posted all day".
        self.schedule_backlog_first = self._bool('SCHEDULE_BACKLOG_FIRST', True)
        # A niche's clip supply is considered exhausted when fewer than this
        # many un-uploaded clips remain (default 1 = pull only when nothing is
        # left to post). Lower = re-pull sooner, higher = keep pulling while
        # anything remains posted.
        self.schedule_backlog_min = self._int('SCHEDULE_BACKLOG_MIN', 1, minimum=0)

        # --- Queue health / backlog management -----------------------------
        # Target total clips queued per niche (across all sources). Discovery
        # will run to fill the queue when it drops below this.
        self.queue_target_total = self._int('QUEUE_TARGET_TOTAL', 12, minimum=1)
        # Minimum distinct source_video_ids in queue. If below, discovery runs
        # to diversify the supply.
        self.queue_min_distinct_sources = self._int('QUEUE_MIN_DISTINCT_SOURCES', 4, minimum=1)
        # Maximum share of queue from a single source. If top source exceeds
        # this share, discovery runs to diversify.
        self.queue_max_top_source_share = self._float('QUEUE_MAX_TOP_SOURCE_SHARE', 0.50,
                                                      minimum=0.0, maximum=1.0)
        # Max un-uploaded clips per source before discovery skips it.
        # The DB already enforces MAX_QUEUED_PER_SOURCE, this is for discovery logic.
        self.queue_max_pending_per_source = self._int('QUEUE_MAX_PENDING_PER_SOURCE', 3, minimum=1)
        # TTL for backlog clips in days. Clips older than this are marked
        # 'expired' and no longer block discovery or upload.
        self.backlog_ttl_days = self._int('BACKLOG_TTL_DAYS', 7, minimum=1)
        # Max clips to generate from one source video per discovery cycle.
        self.max_clips_generated_per_source_per_cycle = self._int('MAX_CLIPS_GENERATED_PER_SOURCE_PER_CYCLE', 2, minimum=1)

        # --- Scheduled discovery -----------------------------------------
        # Candidates pulled per channel before dedup/filtering. Must be >=
        # schedule_max_videos so already-processed videos can't starve a run.
        self.discovery_lookback = self._int('DISCOVERY_LOOKBACK', 10, minimum=1)
        # Default cap on videos STARTED per scheduled run per niche. A niche
        # can override with `max_videos:` in niches.yaml; SCHEDULE_MAX_TOTAL
        # (below) still clamps the whole sweep so a stack of hungry niches
        # can't blow the day's quota in one run. Quota: ~10k units/day, one
        # upload ~1600 -> ~6 uploads/day, so a few videos/run is the norm.
        self.schedule_max_videos = self._int('SCHEDULE_MAX_VIDEOS', 3, minimum=1)
        # Hard ceiling on videos started across ALL niches in one sweep.
        # 0 = unlimited (per-niche caps rule). Default matches the upload
        # quota reality: ~6 uploads/day.
        self.schedule_max_total = self._int('SCHEDULE_MAX_TOTAL', 6, minimum=0)
        # Pull-once model (Allan's design): a sweep that already has un-uploaded
        # clips for a niche should POST those instead of pulling+downloading a
        # new source. Only pull again when the clip supply runs out. This is
        # what turns "3 downloads a day" into "1 download, posted all day".
        self.schedule_backlog_first = self._bool('SCHEDULE_BACKLOG_FIRST', True)
        # A niche's clip supply is considered exhausted when fewer than this
        # many un-uploaded clips remain (default 1 = pull only when nothing is
        # left to post). Lower = re-pull sooner, higher = keep pulling while
        # anything remains posted.
        self.schedule_backlog_min = self._int('SCHEDULE_BACKLOG_MIN', 1, minimum=0)

        # Guaranteed fresh chop (SWEEP_GUARANTEE_FRESH): a full sweep always
        # chops one fresh source video per authenticated channel, regardless of
        # the shared sweep budget (SCHEDULE_MAX_TOTAL) and of queue health, so
        # a channel is never skipped just because its queue looks healthy.
        # The chop only queues the clips; uploads are still capped below.
        self.sweep_guarantee_fresh = self._bool('SWEEP_GUARANTEE_FRESH', True)

        # --- Upload pacing (anti-burst) ----------------------------------
        # Random delay in seconds between successive uploads in the same run,
        # so a batch of clips doesn't land on YouTube in one burst. YouTube's
        # Shorts algorithm feeds each upload on its own; posting 5 clips in 82
        # seconds (what we saw in production logs) makes them compete with
        # each other for the same first-test audience.
        self.upload_pacing_min = self._int('UPLOAD_PACING_MIN', 45, minimum=0)
        self.upload_pacing_max = self._int('UPLOAD_PACING_MAX', 180, minimum=0)
        if self.upload_pacing_max < self.upload_pacing_min:
            self.upload_pacing_max = self.upload_pacing_min
        # Random minute-offset (0..n) added to each scheduled run time, so
        # fixed 9AM/2PM/7PM windows become 9:xx / 2:xx / 7:xx instead of
        # everyone firing at :00. Set 0 to keep exact times.
        self.schedule_jitter_minutes = self._int('SCHEDULE_JITTER_MINUTES', 45,
                                                 minimum=0)

        # --- Encoding ----------------------------------------------------
        # 'medium', deliberately between the two extremes this setting has had.
        # It was 'slow' (justified with "rendering is not the bottleneck"), then
        # 'veryfast' when that stopped being true as the channel count grew.
        # 'veryfast' went too far: it was paired with CRF 20 and reintroduced
        # exactly the blocking artefacts around composited caption glyphs that
        # test_render_quality.py was written to catch -- flat graphic regions
        # next to hard text edges are the worst case for a fast preset's coarse
        # mode decisions, and YouTube's re-encode preserves those blocks rather
        # than hiding them. 'medium' is ~4x faster than 'slow' and keeps the
        # frame clean; the real render speedup now comes from parallel encodes
        # and the hardware encoder, not from degrading the picture.
        self.video_preset = os.getenv('VIDEO_PRESET', 'medium')
        # 18, not 20. Measured, not guessed: at 20 the caption backdrops and
        # flat areas visibly blocked once glyphs were burned in. The extra bits
        # are cheap next to re-rendering a Short that looks cheap.
        self.video_crf = self._int('VIDEO_CRF', 18, minimum=0)
        # Optional hardware encoder ('h264_nvenc', 'h264_qsv', 'h264_amf').
        # 'auto' probes ffmpeg once and uses one if present -- typically 5-10x
        # faster than libx264 and it frees the CPU for transcription, which is
        # the real bottleneck. 'off' forces libx264.
        self.video_encoder = (os.getenv('VIDEO_ENCODER') or 'auto').strip().lower()
        # swscale flag for the *visible* rescale. fast_bilinear (the old
        # hard-coded value) is the lowest-quality option available and softened
        # every frame; lanczos keeps edges and text sharp on the downscale from
        # a 1080p/4K source.
        self.video_scaler = (os.getenv('VIDEO_SCALER') or 'lanczos').strip() or 'lanczos'
        if self.video_scaler not in ('lanczos', 'bicubic', 'bilinear', 'spline',
                                     'neighbor', 'area', 'fast_bilinear'):
            self.video_scaler = 'lanczos'
        # Output framerate cap. The source rate is preserved below this, so a
        # 60fps source stays 60fps (the old code forced everything to 30 and
        # threw away half the frames).
        self.video_max_fps = self._int('VIDEO_MAX_FPS', 60, minimum=1)
        # 128k AAC was audibly lossy on music beds; 192k/48k matches YouTube's
        # own stereo recommendation.
        self.audio_bitrate = os.getenv('AUDIO_BITRATE', '192k')
        self.audio_sample_rate = self._int('AUDIO_SAMPLE_RATE', 48000, minimum=8000)

        # --- Background music ----------------------------------------------
        # Directory containing copyright-free music tracks (MP3/WAV/OGG).
        # Tracks should be licensed for commercial use (e.g., YouTube Audio
        # Library, StreamBeats, NCS, etc.). One random track is picked per clip.
        self.music_dir = _resolve(os.getenv('MUSIC_DIR', 'data/music'))
        # Volume of background music relative to main audio (0.0 - 1.0).
        # 0.15 = 15% (audible but not overpowering speech).
        self.music_volume = self._float('MUSIC_VOLUME', 0.15, minimum=0.0, maximum=1.0)
        # Ducking: when speech is detected, lower music further by this factor.
        # 0.3 = reduce music to 30% of its already-low volume during speech.
        self.music_duck_factor = self._float('MUSIC_DUCK_FACTOR', 0.3, minimum=0.0, maximum=1.0)
        # Enable/disable background music entirely.
        self.music_enabled = self._bool('MUSIC_ENABLED', True)

        # Viral captions are big: at 1080x1920 a 54px font is a caption on a
        # desktop video, not a Short. 104 is ~10% of frame width per character
        # row, which is what the reference Shorts use.
        self.caption_font_size = self._int('CAPTION_FONT_SIZE', 104, minimum=8)
        # Caption style / preset. See src/captions.py PRESETS:
        #   viral (default), hormozi, kinetic, single, minimalist, neon
        #   legacy -> the old one-paragraph-per-segment renderer
        self.caption_style = (os.getenv('CAPTION_STYLE') or 'viral').lower()
        # Words visible at once. 0/unset uses the preset's own value; viral
        # captions live in the 1-4 range and 4 is the ceiling before the block
        # starts reading as a paragraph.
        self.caption_max_words = self._int('CAPTION_MAX_WORDS', 0, minimum=0) or None
        # Fraction of caption groups allowed a red "punch" word. Rationed on
        # purpose: a red word in every group stops registering as emphasis.
        self.caption_punch_ratio = self._float('CAPTION_PUNCH_RATIO', 0.22,
                                               minimum=0.0)

        # --- Smart (person-aware) framing --------------------------------
        # Used when BACKGROUND_MODE=smart. See src/smart_crop.py.
        # Frames sampled across the clip for person detection. More samples =
        # steadier layout decisions; 9 costs well under a second.
        self.smart_samples = self._int('SMART_SAMPLES', 9, minimum=3)
        # A detection must appear in this fraction of sampled frames to count
        # as a person. This is the false-positive filter: Haar cascades fire on
        # background texture in isolated frames.
        # Lowered from 0.40 to 0.25 for higher sensitivity (more detections kept).
        self.smart_min_presence = self._float('SMART_MIN_PRESENCE', 0.25,
                                              minimum=0.0)
        # Minimum size of a detection relative to the smaller side of the
        # work frame (before scaling up). Higher values reject tiny false
        # positives from texture.
        # Lowered from 0.08 to 0.05 to detect smaller/distant faces.
        self.smart_min_size_ratio = self._float('SMART_MIN_SIZE_RATIO', 0.05,
                                                minimum=0.01)
        # Minimum number of overlapping detections required to retain a
        # candidate (higher = stricter).
        # Lowered from 8 to 4 for higher sensitivity (fewer false negatives).
        self.smart_min_neighbors = self._int('SMART_MIN_NEIGHBORS', 4, minimum=1)
        # Whether to also load the profile Haar cascade (often noisy).
        # Enabled for higher sensitivity - catches side/angled faces.
        self.smart_use_profile_cascade = self._bool('SMART_USE_PROFILE_CASCADE', True)
        # Cap on people given their own grid tile. Above 4 each tile is too
        # small to read on a phone.
        self.smart_max_people = self._int('SMART_MAX_PEOPLE', 4, minimum=1)
        # <1.0 tightens the crop for a closer shot.
        self.smart_zoom = self._float('SMART_ZOOM', 1.0, minimum=0.25)
        # How far below the face to place the framing centre, in face-heights.
        # 0 centres the face itself, which crops the body off.
        self.smart_headroom = self._float('SMART_HEADROOM', 0.55, minimum=0.0)
        # Maximum allowed size variance for a track (as fraction of mean size).
        # Prevents a single blob that grows/shrinks from being mistaken for a person.
        # Increased from 0.3 to 0.4 for more lenient size filtering.
        self.smart_max_size_variance = self._float('SMART_MAX_SIZE_VARIANCE', 0.4,
                                                   minimum=0.0, maximum=1.0)
        # Tracking tolerance: maximum centre shift (as fraction of frame width)
        # allowed between frames for a detection to be considered the same person.
        # Increased from 0.12 to 0.18 for more lenient tracking across frames.
        self.smart_track_tol = self._float('SMART_TRACK_TOL', 0.18,
                                           minimum=0.01, maximum=0.5)
        # Backdrop used when smart framing finds nobody.
        self.smart_fallback_mode = (os.getenv('SMART_FALLBACK_MODE') or 'cheap').lower()
        if self.smart_fallback_mode not in ('cheap', 'blur', 'black', 'crop'):
            self.smart_fallback_mode = 'cheap'

        # --- Paths (anchored to project root) ----------------------------
        self.temp_dir = _resolve(os.getenv('TEMP_DIR', 'data/temp'))
        self.data_dir = _resolve(os.getenv('DATA_DIR', 'data'))
        self.logs_dir = _resolve(os.getenv('LOG_DIR', 'data/logs'))
        self.shorts_dir = _resolve(os.getenv('SHORTS_DIR', 'data/shorts'))
        self.db_path = _resolve(os.getenv('DB_PATH', 'data/processed_videos.db'))
        self.log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
        # How long a channel that fails to list (dead ID / no videos tab / 404
        # handle) stays quiet before it is re-probed by the downloader. The
        # cache lives in data/dead_channels.json.
        self.dead_channel_cooldown_days = self._int(
            'DEAD_CHANNEL_COOLDOWN_DAYS', 14, minimum=1)
        # Extra bounded retries for a channel listing that fails with a
        # TRANSIENT network error (DNS, timeout, reset, rate limit). These are
        # never cached as dead -- see downloader._is_transient_error. 0 disables
        # the extra retries (yt-dlp's own extractor_retries still applies).
        self.channel_listing_retries = self._int(
            'CHANNEL_LISTING_RETRIES', 2, minimum=0)

        for d in (self.temp_dir, self.data_dir, self.logs_dir, self.shorts_dir):
            d.mkdir(parents=True, exist_ok=True)

        # --- Niches (never raise at import time) -------------------------
        self.niches_file = PROJECT_ROOT / 'config' / 'niches.yaml'
        self.niches, self.niches_error = self._load_niches()

    # ------------------------------------------------------------------
    @staticmethod
    def _int(name: str, default: int, minimum: int = None) -> int:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == '':
            value = default
        else:
            # Tolerate inline comments, e.g. "MAX=3  # three at a time"
            cleaned = str(raw).split('#')[0].strip()
            try:
                value = int(float(cleaned))
            except (TypeError, ValueError):
                value = default
        if minimum is not None and value < minimum:
            value = minimum
        return value

    @staticmethod
    def _float(name: str, default: float, minimum: float = None, maximum: float = None) -> float:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == '':
            value = float(default)
        else:
            cleaned = str(raw).split('#')[0].strip()
            try:
                value = float(cleaned)
            except (TypeError, ValueError):
                value = float(default)
        if minimum is not None and value < minimum:
            value = float(minimum)
        if maximum is not None and value > maximum:
            value = float(maximum)
        return value

    @staticmethod
    def _bool(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == '':
            return default
        return str(raw).split('#')[0].strip().lower() in ('1', 'true', 'yes', 'on')

    def _load_niches(self):
        if yaml is None:
            return {}, "PyYAML is not installed (pip install -r requirements.txt)"
        if not self.niches_file.exists():
            return {}, f"niches.yaml not found at {self.niches_file}"
        try:
            with open(self.niches_file, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                return {}, "niches.yaml must be a mapping of niche name -> settings"
            return data, None
        except Exception as exc:
            return {}, f"could not parse niches.yaml: {exc}"

    # ------------------------------------------------------------------
    def get_niche_config(self, niche_name: str) -> dict:
        """Return a niche config, merged over defaults so keys always exist.

        ``channels`` and ``channel`` mean the SCRAPE sources (who discovery
        pulls from). ``upload_channels`` means the CHANNELS THIS NICHE POSTS TO
        (a different thing entirely). Resolution:

        * ``upload_channels``: non-empty list -> used as-is. Missing/empty ->
          falls back to the legacy ``channel`` string as a single-item list.
          Still empty -> the niche has no upload binding (sweeps skip it).
        * ``channels`` / ``channel``: passed through untouched for discovery.
        """
        merged = dict(DEFAULT_NICHE)
        raw = (self.niches or {}).get(niche_name)
        if isinstance(raw, dict):
            merged.update({k: v for k, v in raw.items() if v is not None})

        # Upload target channels: explicit `upload_channels:` list wins,
        # otherwise the legacy `channel:` string becomes a single-item list.
        upload_channels = merged.get('upload_channels')
        if isinstance(upload_channels, list):
            if len(upload_channels) == 0:
                legacy_channel = merged.get('channel')
                if isinstance(legacy_channel, str) and legacy_channel.strip():
                    merged['upload_channels'] = [legacy_channel.strip()]
                else:
                    merged['upload_channels'] = []
        elif isinstance(upload_channels, str) and upload_channels.strip():
            merged['upload_channels'] = [upload_channels.strip()]
        else:
            legacy_channel = merged.get('channel')
            if isinstance(legacy_channel, str) and legacy_channel.strip():
                merged['upload_channels'] = [legacy_channel.strip()]
            else:
                merged['upload_channels'] = []

        # Scrape source channels stay untouched: they are what discovery pulls
        # from and must never be mistaken for upload targets.
        if not isinstance(merged.get('channels'), list):
            merged['channels'] = []

        # For keywords, we keep the existing conversion (string to list, non-list to empty list)
        key_val = merged.get('keywords')
        if isinstance(key_val, str):
            merged['keywords'] = [key_val]
        elif not isinstance(key_val, list):
            merged['keywords'] = []

        return merged

    def niche_names(self):
        return sorted((self.niches or {}).keys())

    def get_niche_channel(self, niche_name: str) -> str:
        """Return the primary upload channel key bound to a niche.

        Resolution order:
          1. the niche's ``upload_channels`` list (first item) if it has one,
          2. the legacy ``channel:`` value in niches.yaml,
          3. ``UPLOAD_DEFAULT_CHANNEL`` from .env,
          4. the niche name itself (a niche whose key matches a token, e.g.
             ``flick_shorts`` -> ``youtube_token_flick_shorts.json``),
          5. '' (no binding) -- uploads are skipped with a warning.
        """
        cfg = self.get_niche_config(niche_name)
        upload_channels = cfg.get('upload_channels', [])
        if upload_channels:
            return upload_channels[0]
        if self.upload_default_channel:
            return self.upload_default_channel
        return (niche_name or '').strip()

    def get_niche_channels(self, niche_name: str) -> List[str]:
        """Return the list of upload channel keys bound to a niche.

        These are the channels this niche PUBLISHES to (``upload_channels``,
        or the legacy ``channel`` string as a single item). ``channels`` --
        the scrape sources used by discovery -- are deliberately NOT included.
        Returns an empty list if no upload channels are bound.
        """
        cfg = self.get_niche_config(niche_name)
        return cfg.get('upload_channels', [])

    def authenticated_channels(self) -> List[str]:
        """Channel keys that have a token file on disk."""
        token_dir = Path(self.oauth_token_file).parent
        return sorted(
            p.name[len('youtube_token_'):-len('.json')]
            for p in token_dir.glob('youtube_token_*.json')
            if p.name != 'youtube_token.json'
        )

    def has_upload_credentials(self) -> bool:
        if self.google_credentials_path and Path(self.google_credentials_path).exists():
            return True
        if self.oauth_client_secrets and Path(self.oauth_client_secrets).exists():
            return True
        return bool(self.youtube_api_key)


# Global config instance. Constructed lazily-but-eagerly: it no longer raises
# on a missing niches.yaml, so importing this module is always safe.
config = Config()
