"""Configuration for the ranking Shorts pipeline.

Code lives in GitHub; runtime state does not. All generated data is anchored
under VIDEO_FACTORY_ROOT, so downloads, temp renders, plans, logs, databases,
voice files and final exports stay together outside the repository.
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
    return default if raw is None else raw.strip().lower() in ('1', 'true', 'yes', 'on')


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class RankingConfig:
    def __init__(self) -> None:
        _load_env()
        self.project_root = PROJECT_ROOT

        # Shared runtime root: set this to the same parent used by the other
        # video pipelines. Nothing generated here defaults into the Git repo.
        raw_root = os.getenv('VIDEO_FACTORY_ROOT', '').strip()
        if raw_root:
            factory_root = Path(raw_root).expanduser()
            if not factory_root.is_absolute():
                factory_root = (PROJECT_ROOT / factory_root).resolve()
        elif os.name == 'nt':
            factory_root = Path(os.getenv('LOCALAPPDATA', Path.home())) / 'DRA' / 'VideoFactory'
        else:
            factory_root = Path.home() / '.local' / 'share' / 'dra-video-factory'
        self.factory_root = factory_root
        self.runtime_root = ensure_dir(factory_root / 'ranking-shorts-pipeline')

        # Explicit overrides still win, but relative overrides resolve inside
        # the external runtime root, never against the current working dir.
        self.data_dir = self._path('DATA_DIR', 'data')
        self.temp_dir = self._path('TEMP_DIR', 'temp')
        self.output_dir = self._path('OUTPUT_DIR', 'output')
        self.clips_dir = ensure_dir(self.data_dir / 'clips')
        self.vo_dir = ensure_dir(self.data_dir / 'vo')
        self.log_dir = ensure_dir(self.data_dir / 'logs')
        self.sfx_dir = self._asset_path('SFX_DIR', 'assets/sfx')
        self.music_dir = self._asset_path('MUSIC_DIR', 'assets/music')
        self.db_path = self.data_dir / 'ranking.db'

        self.width = _i('VIDEO_WIDTH', 1080)
        self.height = _i('VIDEO_HEIGHT', 1920)
        self.fps = _i('VIDEO_FPS', 30)
        self.crf = _i('VIDEO_CRF', 18)
        self.preset = os.getenv('VIDEO_PRESET', 'medium')
        self.encoder = os.getenv('VIDEO_ENCODER', 'auto').lower()
        self.font = os.getenv('OVERLAY_FONT', '').strip()

        self.vo_enabled = _b('VO_ENABLED', True)
        self.vo_skip_first = _b('VO_SKIP_FIRST', True)
        self.tts_voice = os.getenv('RANKING_TTS_VOICE', 'Puck')
        self.tts_format = os.getenv('RANKING_TTS_FORMAT', 'mp3')
        self.script_model = os.getenv('SCRIPT_MODEL', 'gemini-2.5-flash')
        self.script_api_key = (os.getenv('GEMINI_API_KEY') or
                               (os.getenv('GEMINI_API_KEYS', '').split(',')[0] or '').strip())

        self.oauth_client_secrets = os.getenv('RANKING_OAUTH_CLIENT_SECRETS', str(PROJECT_ROOT / 'credentials.json'))
        token_default = self.runtime_root / 'config' / 'youtube_token_ranking.json'
        self.oauth_token_file = os.getenv('RANKING_OAUTH_TOKEN_FILE', str(token_default))
        self.privacy_status = os.getenv('UPLOAD_PRIVACY', 'private').lower()
        self.upload_max_per_run = _i('UPLOAD_MAX_PER_RUN', 1)
        self.dry_run = _b('DRY_RUN', False)

        raw = self._load_yaml()
        self.defaults: Dict[str, Any] = raw.get('defaults') or {}
        self.sfx_map: Dict[str, str] = raw.get('sfx_map') or {}
        self.topics: Dict[str, Dict[str, Any]] = raw.get('topics') or {}
        if os.getenv('CLIPS_PER_VIDEO'):
            self.defaults['clips_per_video'] = _i('CLIPS_PER_VIDEO', 5)

    def _path(self, env: str, default: str, create: bool = True) -> Path:
        raw = os.getenv(env, default)
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.runtime_root / path
        return ensure_dir(path) if create else path

    def _asset_path(self, env: str, default: str) -> Path:
        raw = os.getenv(env, default)
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    def _load_yaml(self) -> dict:
        path = PROJECT_ROOT / 'config' / 'ranking.yaml'
        if not path.exists():
            logger.warning('config/ranking.yaml missing; using bare defaults')
            return {}
        try:
            import yaml
            return yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        except ImportError:
            logger.error('PyYAML not installed; cannot read ranking.yaml')
            return {}
        except Exception as exc:
            logger.error('ranking.yaml is not valid YAML: %s', exc)
            return {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.defaults.get(key, default)

    def topic(self, name: str) -> Dict[str, Any]:
        cfg = dict(self.topics.get(name) or {})
        cfg.setdefault('title', 'TOP {n}')
        cfg.setdefault('queries', [])
        cfg.setdefault('channels', [])
        cfg.setdefault('extra_sources', [])
        cfg.setdefault('negative_keywords', [])
        cfg.setdefault('tags', [])
        cfg['name'] = name
        return cfg

    def topic_names(self) -> List[str]:
        return list(self.topics.keys())

    def rank_color(self, rank: int) -> str:
        colors = self.get('rank_colors') or {}
        return str(colors.get(rank) or colors.get(str(rank)) or '0x1E90FF')

    def sfx_path(self, name: str) -> Optional[Path]:
        filename = self.sfx_map.get(name)
        if not filename:
            return None
        path = self.sfx_dir / filename
        return path if path.exists() else None

    def resolve_font(self) -> str:
        if self.font and Path(self.font).exists():
            return self.font
        for candidate in ('C:/Windows/Fonts/impact.ttf', 'C:/Windows/Fonts/arialbd.ttf',
                          '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
                          '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
                          '/System/Library/Fonts/Supplemental/Impact.ttf'):
            if Path(candidate).exists():
                if self.font:
                    logger.warning('OVERLAY_FONT %s not found; using %s', self.font, candidate)
                return candidate
        raise RuntimeError('No overlay font found. Set OVERLAY_FONT to an existing .ttf file.')


config = RankingConfig()
