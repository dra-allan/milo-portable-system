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
from typing import List

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
    'keywords': [],
    'min_duration': 300,
    'max_duration': 7200,
    'min_score': 0.0,
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

        # --- Processing limits -------------------------------------------
        self.max_concurrent_videos = self._int('MAX_CONCURRENT_VIDEOS', 3, minimum=1)
        self.min_segment_length = self._int('MIN_SEGMENT_LENGTH', 15, minimum=1)
        self.max_segment_length = self._int('MAX_SEGMENT_LENGTH', 60, minimum=1)
        if self.min_segment_length > self.max_segment_length:
            self.min_segment_length, self.max_segment_length = (
                self.max_segment_length, self.min_segment_length
            )
        self.max_clips_per_video = self._int('MAX_CLIPS_PER_VIDEO', 5, minimum=1)
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
        self.transcribe_window_minutes = self._int(
            'TRANSCRIBE_WINDOW_MINUTES', 15, minimum=1
        )
        self.transcribe_threads = self._int('TRANSCRIBE_THREADS', 0, minimum=0)

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
        self.download_height = self._int('DOWNLOAD_HEIGHT', 1440, minimum=240)
        self.download_concurrency = self._int('DOWNLOAD_CONCURRENCY', 2, minimum=1)

        # --- Render tuning -----------------------------------------------
        # Measured (see BENCHMARKS.md): parallel ffmpeg encodes give only
        # 1.02-1.06x on a 2-core box, because libx264 already saturates every
        # core -- two encodes just split the same CPU and double the memory.
        # So scale with core count instead of blindly defaulting to 2, which
        # is what the original plan called for.
        self.render_workers = self._int(
            'RENDER_WORKERS', max(1, min(2, (os.cpu_count() or 2) // 2)), minimum=1
        )
        # The blurred-backdrop fill was the single most expensive filter in
        # the chain (full-res gblur every frame). 'cheap' downscales before
        # blurring for a visually identical result at a fraction of the cost.
        # Use 'crop' to fill frame without bars, 'black' for solid bars, or 'cheap'/'blur' for blurred bars
# Use 'smart' for intelligent person-aware cropping (face detection based)
        self.background_mode = (os.getenv('BACKGROUND_MODE') or 'crop').lower()
        if self.background_mode not in ('cheap', 'blur', 'black', 'crop', 'smart'):
            self.background_mode = 'crop'
        # How many clips to keep in the persisted plan. Rendering is capped by
        # max_clips_per_video, but keeping a deep ranked list means "give me
        # 10 more clips" costs no download and no transcription.
        self.max_candidates = self._int('MAX_CANDIDATES', 30, minimum=1)

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
        self.upload_max_per_run = self._int('UPLOAD_MAX_PER_RUN', 5, minimum=1)
        # When a run has room left in its cap, fill it with older clips that
        # were rendered but never uploaded (the "new mixed with old" queue).
        self.upload_backlog = self._bool('UPLOAD_BACKLOG', True)
        # Channel key used when a niche has no explicit `channel:` binding.
        self.upload_default_channel = (os.getenv('UPLOAD_DEFAULT_CHANNEL') or '').strip()

        # --- Scheduled discovery -----------------------------------------
        # Candidates pulled per channel before dedup/filtering. Must be >=
        # schedule_max_videos so already-processed videos can't starve a run.
        self.discovery_lookback = self._int('DISCOVERY_LOOKBACK', 10, minimum=1)
        # Global cap on videos STARTED per scheduled run across all niches.
        # Quota: ~10k units/day, one upload ~1600 -> ~6 uploads/day. Discovery
        # and transcription cost real time/money, so default to 3 videos/run.
        self.schedule_max_videos = self._int('SCHEDULE_MAX_VIDEOS', 3, minimum=1)

        # --- Encoding ----------------------------------------------------
        # 'slow' over 'medium': at a fixed CRF a slower preset spends more time
        # searching and produces a *better looking* frame for the same file
        # size. Rendering is not the bottleneck (download + transcription are),
        # so the extra CPU is worth it for the visible gain.
        self.video_preset = os.getenv('VIDEO_PRESET', 'slow')
        # CRF 20 was leaving visible blocking in motion once captions and a
        # blurred backdrop were composited on top. 18 is effectively
        # transparent for 1080p delivery; YouTube re-encodes anyway, so
        # handing it a cleaner master directly improves the published result.
        self.video_crf = self._int('VIDEO_CRF', 18, minimum=0)
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
        self.smart_min_presence = self._float('SMART_MIN_PRESENCE', 0.34,
                                              minimum=0.0)
        # Cap on people given their own grid tile. Above 4 each tile is too
        # small to read on a phone.
        self.smart_max_people = self._int('SMART_MAX_PEOPLE', 4, minimum=1)
        # <1.0 tightens the crop for a closer shot.
        self.smart_zoom = self._float('SMART_ZOOM', 1.0, minimum=0.25)
        # How far below the face to place the framing centre, in face-heights.
        # 0 centres the face itself, which crops the body off.
        self.smart_headroom = self._float('SMART_HEADROOM', 0.55, minimum=0.0)
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
    def _float(name: str, default: float, minimum: float = None) -> float:
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
        """Return a niche config, merged over defaults so keys always exist."""
        merged = dict(DEFAULT_NICHE)
        raw = (self.niches or {}).get(niche_name)
        if isinstance(raw, dict):
            merged.update({k: v for k, v in raw.items() if v is not None})
        # Guarantee list types even if the YAML had a bare string.
        for key in ('channels', 'keywords'):
            val = merged.get(key)
            if isinstance(val, str):
                merged[key] = [val]
            elif not isinstance(val, list):
                merged[key] = []
        return merged

    def niche_names(self):
        return sorted((self.niches or {}).keys())

    def get_niche_channel(self, niche_name: str) -> str:
        """Return the upload channel key bound to a niche.

        Resolution order:
          1. the niche's explicit ``channel:`` value in niches.yaml,
          2. ``UPLOAD_DEFAULT_CHANNEL`` from .env,
          3. the niche name itself (a niche whose key matches a token, e.g.
             ``flick_shorts`` -> ``youtube_token_flick_shorts.json``),
          4. '' (no binding) -- uploads are skipped with a warning.
        """
        cfg = self.get_niche_config(niche_name)
        bound = str(cfg.get('channel') or '').strip()
        if bound:
            return bound
        if self.upload_default_channel:
            return self.upload_default_channel
        return (niche_name or '').strip()

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
