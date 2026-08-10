"""Configuration: ``config/.env`` for secrets and machine-specific paths,
``config/ranking.yaml`` for topics and style.

The split is the same one the shorts pipeline uses, for the same reason: the
YAML is safe to commit and diff, the .env is not.

Everything resolves against this package's own directory, so the two pipelines
never write into each other's ``data/`` even when launched from the same shell.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .utils import ensure_dir, setup_logger

logger = setup_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        logger.debug('python-dotenv not installed; relying on real env vars')
        return
    for candidate in (PROJECT_ROOT / 'config' / '.env', PROJECT_ROOT / '.env'):
        if candidate.exists():
            load_dotenv(candidate)
            logger.debug('loaded env from %s', candidate)
            return


def _b(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class RankingConfig:
    def __init__(self) -> None:
        _load_env()
        self.project_root = PROJECT_ROOT

        # -- paths ------------------------------------------------------
        self.data_dir = self._path('DATA_DIR', 'data')
        self.temp_dir = self._path('TEMP_DIR', 'data/temp')
        self.output_dir = self._path('OUTPUT_DIR', 'data/output')
        self.clips_dir = ensure_dir(self.data_dir / 'clips')
        self.vo_dir = ensure_dir(self.data_dir / 'vo')
        self.log_dir = ensure_dir(self.data_dir / 'logs')
        self.sfx_dir = self._path('SFX_DIR', 'assets/sfx', create=False)
        self.music_dir = self._path('MUSIC_DIR', 'assets/music', create=False)
        self.db_path = self.data_dir / 'ranking.db'

        # -- render -----------------------------------------------------
        self.width = _i('VIDEO_WIDTH', 1080)
        self.height = _i('VIDEO_HEIGHT', 1920)
        self.fps = _i('VIDEO_FPS', 30)
        self.crf = _i('VIDEO_CRF', 18)
        self.preset = os.getenv('VIDEO_PRESET', 'medium')
        self.encoder = os.getenv('VIDEO_ENCODER', 'auto').lower()
        self.font = os.getenv('OVERLAY_FONT', '').strip()

        # -- voice-over -------------------------------------------------
        self.vo_enabled = _b('VO_ENABLED', True)
        self.vo_skip_first = _b('VO_SKIP_FIRST', True)
        self.tts_voice = os.getenv('RANKING_TTS_VOICE', 'Puck')
        self.tts_format = os.getenv('RANKING_TTS_FORMAT', 'mp3')

        # -- script writer ----------------------------------------------
        self.script_model = os.getenv('SCRIPT_MODEL', 'gemini-2.5-flash')
        self.script_api_key = (os.getenv('GEMINI_API_KEY')
                               or (os.getenv('GEMINI_API_KEYS', '').split(',')[0]
                                   or '').strip())

        # -- upload -----------------------------------------------------
        self.oauth_client_secrets = os.getenv(
            'RANKING_OAUTH_CLIENT_SECRETS',
            str(PROJECT_ROOT / 'credentials.json'))
        self.oauth_token_file = os.getenv(
            'RANKING_OAUTH_TOKEN_FILE',
            str(PROJECT_ROOT / 'config' / 'youtube_token_ranking.json'))
        self.privacy_status = os.getenv('UPLOAD_PRIVACY', 'private').lower()
        self.upload_max_per_run = _i('UPLOAD_MAX_PER_RUN', 1)

        # -- behaviour --------------------------------------------------
        self.dry_run = _b('DRY_RUN', False)

        # -- yaml -------------------------------------------------------
        raw = self._load_yaml()
        self.defaults: Dict[str, Any] = raw.get('defaults') or {}
        self.sfx_map: Dict[str, str] = raw.get('sfx_map') or {}
        self.topics: Dict[str, Dict[str, Any]] = raw.get('topics') or {}

        # .env wins over the YAML default where both express the same knob.
        if os.getenv('CLIPS_PER_VIDEO'):
            self.defaults['clips_per_video'] = _i('CLIPS_PER_VIDEO', 5)

    # -- helpers --------------------------------------------------------
    def _path(self, env: str, default: str, create: bool = True) -> Path:
        raw = os.getenv(env, default)
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return ensure_dir(path) if create else path

    def _load_yaml(self) -> dict:
        path = PROJECT_ROOT / 'config' / 'ranking.yaml'
        if not path.exists():
            logger.warning('config/ranking.yaml missing; using bare defaults')
            return {}
        try:
            import yaml
        except ImportError:
            logger.error('PyYAML not installed; cannot read ranking.yaml')
            return {}
        try:
            return yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        except Exception as exc:  # noqa: BLE001 - config errors must be loud
            logger.error('ranking.yaml is not valid YAML: %s', exc)
            return {}

    def get(self, key: str, default: Any = None) -> Any:
        """Read a style/behaviour knob from the YAML defaults."""
        return self.defaults.get(key, default)

    def topic(self, name: str) -> Dict[str, Any]:
        cfg = dict(self.topics.get(name) or {})
        cfg.setdefault('title', 'TOP {n}')
        cfg.setdefault('queries', [])
        cfg.setdefault('extra_sources', [])
        cfg.setdefault('negative_keywords', [])
        cfg.setdefault('tags', [])
        cfg['name'] = name
        return cfg

    def topic_names(self) -> List[str]:
        return list(self.topics.keys())

    def rank_color(self, rank: int) -> str:
        colors = self.get('rank_colors') or {}
        # YAML keys may load as ints or strings depending on quoting.
        return str(colors.get(rank) or colors.get(str(rank)) or '0x1E90FF')

    def sfx_path(self, name: str) -> Optional[Path]:
        filename = self.sfx_map.get(name)
        if not filename:
            return None
        path = self.sfx_dir / filename
        return path if path.exists() else None

    def resolve_font(self) -> str:
        """Return a usable font file, falling back to whatever exists.

        A missing font is not a soft failure: drawtext aborts the render, and
        the configured path is machine-specific (Impact on Windows, DejaVu on
        the Linux box), so a hardcoded default would break one of them.
        """
        if self.font and Path(self.font).exists():
            return self.font
        fallbacks = [
            'C:/Windows/Fonts/impact.ttf',
            'C:/Windows/Fonts/arialbd.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
            '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
            '/System/Library/Fonts/Supplemental/Impact.ttf',
        ]
        for candidate in fallbacks:
            if Path(candidate).exists():
                if self.font:
                    logger.warning('OVERLAY_FONT %s not found; using %s',
                                   self.font, candidate)
                return candidate
        raise RuntimeError(
            'No overlay font found. Set OVERLAY_FONT in config/.env to a .ttf '
            'that exists on this machine.')


config = RankingConfig()
