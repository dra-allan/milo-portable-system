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
        self.download_height = self._int('DOWNLOAD_HEIGHT', 1080, minimum=240)
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

        # --- Encoding ----------------------------------------------------
        self.video_preset = os.getenv('VIDEO_PRESET', 'medium')
        self.video_crf = self._int('VIDEO_CRF', 20, minimum=0)
        self.caption_font_size = self._int('CAPTION_FONT_SIZE', 54, minimum=8)
        # Caption style: 'default', 'hormozi', 'minimalist', 'pop', 'kinetic'
        self.caption_style = (os.getenv('CAPTION_STYLE') or 'default').lower()

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

    def has_upload_credentials(self) -> bool:
        if self.google_credentials_path and Path(self.google_credentials_path).exists():
            return True
        if self.oauth_client_secrets and Path(self.oauth_client_secrets).exists():
            return True
        return bool(self.youtube_api_key)


# Global config instance. Constructed lazily-but-eagerly: it no longer raises
# on a missing niches.yaml, so importing this module is always safe.
config = Config()
