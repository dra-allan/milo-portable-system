"""Configuration for the ranking Shorts pipeline."""
import os
from pathlib import Path
from .utils import ensure_dir, setup_logger
logger = setup_logger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

def _load_env():
    try:
        from dotenv import load_dotenv
        for candidate in (PROJECT_ROOT / 'config' / '.env', PROJECT_ROOT / '.env'):
            if candidate.exists():
                load_dotenv(candidate)
                return
    except ImportError:
        pass

def _b(name, default):
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in ('1', 'true', 'yes', 'on')

def _i(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default

class RankingConfig:
    def __init__(self):
        _load_env(); self.project_root = PROJECT_ROOT
        raw_root = os.getenv('VIDEO_FACTORY_ROOT', '').strip()
        if raw_root:
            factory_root = Path(raw_root).expanduser()
            factory_root = (PROJECT_ROOT / factory_root).resolve() if not factory_root.is_absolute() else factory_root
        elif os.name == 'nt':
            factory_root = Path(os.getenv('LOCALAPPDATA', Path.home())) / 'DRA' / 'VideoFactory'
        else:
            factory_root = Path.home() / '.local' / 'share' / 'dra-video-factory'
        self.factory_root = factory_root
        self.runtime_root = ensure_dir(factory_root / 'ranking-shorts-pipeline')
        self.data_dir = self._path('DATA_DIR', 'data'); self.temp_dir = self._path('TEMP_DIR', 'temp'); self.output_dir = self._path('OUTPUT_DIR', 'output')
        self.clips_dir = ensure_dir(self.data_dir / 'clips'); self.vo_dir = ensure_dir(self.data_dir / 'vo'); self.log_dir = ensure_dir(self.data_dir / 'logs')
        self.sfx_dir = self._asset_path('SFX_DIR', 'assets/sfx'); self.music_dir = self._asset_path('MUSIC_DIR', 'assets/music'); self.db_path = self.data_dir / 'ranking.db'
        self.width = _i('VIDEO_WIDTH', 1080); self.height = _i('VIDEO_HEIGHT', 1920); self.fps = _i('VIDEO_FPS', 30); self.crf = _i('VIDEO_CRF', 18)
        self.preset = os.getenv('VIDEO_PRESET', 'veryfast'); self.encoder = os.getenv('VIDEO_ENCODER', 'auto').lower(); self.font = os.getenv('OVERLAY_FONT', '').strip()
        self.fast_mode = _b('RANKING_FAST_MODE', True); self.render_workers = max(1, _i('RANKING_RENDER_WORKERS', 2)); self.reject_budget = max(1, _i('RANKING_REJECT_BUDGET', 2))
        self.vet_transcribe = _b('RANKING_VET_TRANSCRIBE', not self.fast_mode); self.vet_music = _b('RANKING_VET_MUSIC', not self.fast_mode); self.vet_ocr = _b('RANKING_VET_OCR', not self.fast_mode)
        self.vo_enabled = _b('VO_ENABLED', True); self.vo_skip_first = _b('VO_SKIP_FIRST', True); self.tts_voice = os.getenv('RANKING_TTS_VOICE', 'Puck'); self.tts_format = os.getenv('RANKING_TTS_FORMAT', 'mp3')
        self.script_model = os.getenv('SCRIPT_MODEL', 'gemini-2.5-flash'); self.script_api_key = os.getenv('GEMINI_API_KEY') or (os.getenv('GEMINI_API_KEYS', '').split(',')[0] or '').strip()
        self.oauth_client_secrets = os.getenv('RANKING_OAUTH_CLIENT_SECRETS', str(PROJECT_ROOT / 'credentials.json')); self.oauth_token_file = os.getenv('RANKING_OAUTH_TOKEN_FILE', str(self.runtime_root / 'config' / 'youtube_token_ranking.json'))
        self.privacy_status = os.getenv('UPLOAD_PRIVACY', 'private').lower(); self.dry_run = _b('DRY_RUN', False)
        self.upload_max_per_day = _i('UPLOAD_MAX_PER_DAY', 6); self.upload_max_per_run = _i('UPLOAD_MAX_PER_RUN', 6); self.queue_target_total = _i('QUEUE_TARGET_TOTAL', 12)
        self.sweep_fresh_share = _i('SWEEP_FRESH_SHARE', 3); self.sweep_backlog_share = _i('SWEEP_BACKLOG_SHARE', 3); self.schedule_run_times = [t.strip() for t in os.getenv('RUN_TIMES', '0 9 * * *').split(',') if t.strip()]; self.schedule_jitter_minutes = _i('SCHEDULE_JITTER_MINUTES', 0)
        self.download_height = _i('RANKING_DOWNLOAD_HEIGHT', 720); self.download_max_bytes = _i('RANKING_MAX_DOWNLOAD_MB', 250) * 1024 * 1024; self.download_concurrency = _i('RANKING_DOWNLOAD_CONCURRENCY', 4); self.max_source_duration = _i('RANKING_MAX_SOURCE_SECONDS', 900); self.render_timeout = _i('RANKING_RENDER_TIMEOUT', 900)
        raw = self._load_yaml(); self.defaults = raw.get('defaults') or {}; self.sfx_map = raw.get('sfx_map') or {}; self.topics = raw.get('topics') or {}
        self.defaults.setdefault('max_download_height', self.download_height); self.defaults.setdefault('max_download_bytes', self.download_max_bytes); self.defaults.setdefault('max_source_duration', self.max_source_duration); self.defaults.setdefault('download_concurrency', self.download_concurrency); self.defaults.setdefault('render_workers', self.render_workers)
        if not os.getenv('VO_ENABLED'): self.vo_enabled = bool(self.defaults.get('vo_enabled', True))
        if os.getenv('CLIPS_PER_VIDEO'): self.defaults['clips_per_video'] = _i('CLIPS_PER_VIDEO', 5)
        if os.getenv('RANKING_VIDEOS_PER_RUN'): self.defaults['videos_per_run'] = _i('RANKING_VIDEOS_PER_RUN', 1)
    def _path(self, env, default, create=True):
        path = Path(os.getenv(env, default)).expanduser(); path = self.runtime_root / path if not path.is_absolute() else path; return ensure_dir(path) if create else path
    def _asset_path(self, env, default):
        path = Path(os.getenv(env, default)).expanduser(); return PROJECT_ROOT / path if not path.is_absolute() else path
    def _load_yaml(self):
        path = PROJECT_ROOT / 'config' / 'ranking.yaml'
        if not path.exists(): return {}
        try:
            import yaml
            return yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        except Exception as exc:
            logger.error('ranking.yaml load failed: %s', exc); return {}
    def get(self, key, default=None): return self.defaults.get(key, default)
    def topic(self, name):
        cfg = dict(self.topics.get(name) or {}); cfg.setdefault('title', 'TOP {n}'); cfg.setdefault('queries', []); cfg.setdefault('channels', []); cfg.setdefault('extra_sources', []); cfg.setdefault('negative_keywords', []); cfg.setdefault('tags', []); cfg['name'] = name; return cfg
    def topic_names(self): return list(self.topics.keys())
    def rank_color(self, rank):
        colors = self.get('rank_colors') or {}; return str(colors.get(rank) or colors.get(str(rank)) or '0x1E90FF')
    def sfx_path(self, name):
        filename = self.sfx_map.get(name); return (self.sfx_dir / filename) if filename and (self.sfx_dir / filename).exists() else None
    def resolve_font(self):
        if self.font and Path(self.font).exists(): return self.font
        for candidate in ('C:/Windows/Fonts/impact.ttf', 'C:/Windows/Fonts/arialbd.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf', '/System/Library/Fonts/Supplemental/Impact.ttf'):
            if Path(candidate).exists(): return candidate
        raise RuntimeError('No overlay font found. Set OVERLAY_FONT to an existing .ttf file.')
config = RankingConfig()
