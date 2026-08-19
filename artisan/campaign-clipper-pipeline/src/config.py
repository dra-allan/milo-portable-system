"""Configuration for the campaign clipper pipeline.

Same two-layer contract as the other lanes: env wins over YAML, YAML wins over
the hardcoded default, and *nothing* runtime-generated is written inside the
checkout. Campaign content folders hold other people's copyrighted video; they
live under VIDEO_FACTORY_ROOT and never near git.

One rule specific to this lane: **per-campaign requirements are not config.**
They live in ``config/campaigns/<id>.yaml`` as compiled specs and are loaded
through :mod:`spec`, which validates them. Config here is only the operator's
own machine and style preferences.
"""

import os
from pathlib import Path

from .utils import ensure_dir, setup_logger

logger = setup_logger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

EDIT_STYLES = ('auto', 'question_first', 'cold_open', 'straight')


def _load_env():
    try:
        from dotenv import load_dotenv
        for candidate in (PROJECT_ROOT / 'config' / '.env',
                          PROJECT_ROOT / '.env'):
            if candidate.exists():
                load_dotenv(candidate)
                return
    except ImportError:
        pass


def _b(name, default):
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in (
        '1', 'true', 'yes', 'on')


def _i(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _f(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _list(name, default=''):
    return [item.strip() for item in os.getenv(name, default).split(',')
            if item.strip()]


class ClipperConfig:
    def __init__(self):
        _load_env()
        self.project_root = PROJECT_ROOT

        raw_root = os.getenv('VIDEO_FACTORY_ROOT', '').strip()
        if raw_root:
            factory_root = Path(raw_root).expanduser()
            if not factory_root.is_absolute():
                factory_root = (PROJECT_ROOT / factory_root).resolve()
        elif os.name == 'nt':
            factory_root = (Path(os.getenv('LOCALAPPDATA', Path.home()))
                            / 'DRA' / 'VideoFactory')
        else:
            factory_root = (Path.home() / '.local' / 'share'
                            / 'dra-video-factory')
        self.factory_root = factory_root
        self.runtime_root = ensure_dir(factory_root
                                       / 'campaign-clipper-pipeline')

        self.data_dir = self._path('DATA_DIR', 'data')
        self.temp_dir = self._path('TEMP_DIR', 'temp')
        self.output_dir = self._path('OUTPUT_DIR', 'output')
        self.sources_dir = ensure_dir(self.data_dir / 'sources')
        self.assets_dir = ensure_dir(self.data_dir / 'assets')
        self.log_dir = ensure_dir(self.data_dir / 'logs')
        self.db_path = self.data_dir / 'clipper.db'

        # -- render geometry (Shorts native) ----------------------------
        self.width = _i('VIDEO_WIDTH', 1080)
        self.height = _i('VIDEO_HEIGHT', 1920)
        # 30, not 60. The clipping playbook exports 60fps and for fast-motion
        # footage that is the right call, but it roughly doubles encode time per
        # clip on a 2-core box and is invisible on talking-head sources -- which
        # is most campaign content. Set VIDEO_FPS=60 per campaign when the
        # footage is actually moving.
        self.fps = _i('VIDEO_FPS', 30)
        # CRF 18 / preset medium is the measured setting from the Shorts lane:
        # veryfast reintroduced blocking beside hard caption edges, which the
        # platform re-encode preserves rather than hides.
        self.crf = _i('VIDEO_CRF', 18)
        self.preset = os.getenv('VIDEO_PRESET', 'medium')
        self.encoder = os.getenv('VIDEO_ENCODER', 'auto').lower()
        self.audio_bitrate = os.getenv('AUDIO_BITRATE', '192k')
        self.render_timeout = _i('CLIPPER_RENDER_TIMEOUT', 1800)
        self.render_workers = max(1, _i('CLIPPER_RENDER_WORKERS', 2))

        # -- text style --------------------------------------------------
        self.font = os.getenv('OVERLAY_FONT', '').strip()
        self.emoji_font = os.getenv('EMOJI_FONT',
                                    'C:/Windows/Fonts/seguiemj.ttf').strip()
        self.text_fill = os.getenv('TEXT_FILL', '#FFFFFF')
        self.text_highlight = os.getenv('TEXT_HIGHLIGHT', '#FFD700')
        self.text_size = _i('TEXT_SIZE', 76)
        self.text_y_ratio = _f('TEXT_Y_RATIO', 0.17)
        self.text_stroke_ratio = _f('TEXT_STROKE_RATIO', 0.08)
        self.text_shadow = (_i('TEXT_SHADOW_X', 6), _i('TEXT_SHADOW_Y', 6))
        self.text_max_lines = _i('TEXT_MAX_LINES', 3)
        self.text_side_margin = _f('TEXT_SIDE_MARGIN', 0.06)

        # -- speech captions (ASS, like the Shorts lane) ------------------
        self.caption_enabled = _b('CAPTION_ENABLED', True)
        # 'clipfarm' (registered by story_edit) is the one-group-at-a-time look
        # that pairs with a title hook: see src/story_edit.py.
        self.caption_style = os.getenv('CAPTION_STYLE', 'viral').lower()
        self.caption_font_size = _i('CAPTION_FONT_SIZE', 0) or None
        self.caption_max_words = _i('CAPTION_MAX_WORDS', 0) or None
        self.caption_punch_ratio = _f('CAPTION_PUNCH_RATIO', 0.22)

        # -- edit structure (hook -> story -> payoff) ---------------------
        # See src/story_edit.py. 'auto' restructures a clip when the transcript
        # supports it and leaves it alone when it does not, so it is safe as a
        # default; 'straight' is the one switch back to a continuous cut.
        self.edit_style = (os.getenv('EDIT_STYLE') or 'auto').strip().lower()
        if self.edit_style not in EDIT_STYLES:
            self.edit_style = 'auto'
        # A span shorter than this is a visual glitch rather than a shot, so the
        # planner drops it instead of cutting to it for four frames.
        self.edit_min_span = _f('EDIT_MIN_SPAN_SECONDS', 0.6)
        # How much of the answering beat rides along with a lifted question.
        # "Are cows allowed on the plane?" alone dangles; with ~1.2s it lands as
        # an exchange.
        self.hook_tail_seconds = _f('HOOK_TAIL_SECONDS', 1.2)
        # Cold-open teaser length. Long enough to raise a question, short enough
        # not to spend the payoff it is selling.
        self.cold_open_seconds = _f('COLD_OPEN_SECONDS', 1.8)
        # The burned-in title hook, held for the whole clip. Roughly 80% of
        # Shorts viewers scroll with the sound off, so this and the speech
        # captions are the only things doing any work for them.
        self.hook_text_enabled = _b('HOOK_TEXT_ENABLED', True)
        self.hook_uppercase = _b('HOOK_UPPERCASE', True)
        self.hook_max_words = _i('HOOK_MAX_WORDS', 9)
        # Top of the frame. Speech captions live in the lower third, and two
        # blocks of large text in the same band make both unreadable.
        self.hook_y_ratio = _f('HOOK_Y_RATIO', 0.11)
        # Prefer a hook lifted from the footage over model/template copy.
        self.hook_prefer_transcript = _b('HOOK_PREFER_TRANSCRIPT', True)

        # -- clip selection ----------------------------------------------
        self.clips_per_source = max(1, _i('CLIPS_PER_SOURCE', 2))
        self.clips_per_run = max(1, _i('CLIPS_PER_RUN', 3))
        self.scene_threshold = _f('SCENE_THRESHOLD', 0.25)
        self.target_duration = _f('TARGET_DURATION', 22.0)
        self.score_audio = _b('SCORE_AUDIO', True)
        self.head_trim = _f('HEAD_TRIM', 0.5)
        self.tail_trim = _f('TAIL_TRIM', 0.5)

        # -- copy model ---------------------------------------------------
        # Same rolling alias + fallback chain as the ranking lane:
        # gemini-2.5-flash is closed to new projects and 404s, which silently
        # drops every run back to template copy.
        self.script_model = os.getenv('SCRIPT_MODEL', 'gemini-flash-latest')
        self.script_model_fallbacks = _list(
            'SCRIPT_MODEL_FALLBACKS',
            'gemini-3.6-flash,gemini-3.5-flash-lite,gemini-2.5-flash')
        self.script_api_key = (os.getenv('GEMINI_API_KEY')
                               or (os.getenv('GEMINI_API_KEYS', '')
                                   .split(',')[0] or '').strip())

        # -- publish ------------------------------------------------------
        self.upload_channel = os.getenv('CLIPPER_UPLOAD_CHANNEL', '').strip()
        self.privacy_status = os.getenv('UPLOAD_PRIVACY', 'private').lower()
        self.oauth_client_secrets = self._resolve_path(os.getenv(
            'CLIPPER_OAUTH_CLIENT_SECRETS',
            str(PROJECT_ROOT / 'credentials.json')))
        self.oauth_token_file = self._resolve_path(os.getenv(
            'CLIPPER_OAUTH_TOKEN_FILE',
            str(self.runtime_root / 'config'
                / 'youtube_token_clipper.json')))
        self.upload_max_per_day = _i('CLIPPER_MAX_PER_DAY', 5)
        self.upload_max_per_campaign_per_day = _i(
            'CLIPPER_MAX_PER_CAMPAIGN_PER_DAY', 3)

        # -- clipster -----------------------------------------------------
        self.clipster_base = os.getenv('CLIPSTER_BASE',
                                       'https://clipster.gg').rstrip('/')
        self.clipster_headless = _b('CLIPSTER_HEADLESS', False)
        self.clipster_profile = os.getenv(
            'CLIPSTER_PROFILE_DIR',
            str(self.runtime_root / 'browser' / 'clipster')).strip()
        self.clipster_timeout = _i('CLIPSTER_TIMEOUT_MS', 45000)

        # -- safety switches ----------------------------------------------
        # Both default OFF. A non-compliant submission is an account strike,
        # not a retryable error, so unattended publishing has to be opted into
        # deliberately after you have watched a campaign pass validation.
        self.auto_upload = _b('CLIPPER_AUTO_UPLOAD', False)
        self.auto_submit = _b('CLIPPER_AUTO_SUBMIT', False)
        self.dry_run = _b('DRY_RUN', False)
        self.strict_validation = _b('CLIPPER_STRICT_VALIDATION', True)
        self.cleanup_after_build = _b('CLIPPER_CLEANUP_AFTER_BUILD', True)
        self.keep_sources = _b('CLIPPER_KEEP_SOURCES', True)
        self.delete_after_submit = _b('CLIPPER_DELETE_AFTER_SUBMIT', False)

        # -- download tooling ---------------------------------------------
        self.gdown_bin = os.getenv('GDOWN_BIN', 'gdown')
        self.rclone_bin = os.getenv('RCLONE_BIN', 'rclone')
        self.rclone_remote = os.getenv('RCLONE_REMOTE', '').strip()
        self.download_timeout = _i('CLIPPER_DOWNLOAD_TIMEOUT', 3600)

        raw = self._load_yaml()
        self.defaults = raw.get('defaults') or {}
        self.style = raw.get('style') or {}
        self.hook_templates = raw.get('hook_templates') or []
        self.banned_words = [str(w).lower()
                             for w in (raw.get('banned_words') or [])]

        # niche -> channel key map (config/channels.yaml). A campaign's own
        # upload_channel field beats this; this beats the global env value.
        self.channel_map = self._load_channels()

        # YAML overrides for the knobs that are style, not machine setup.
        if not os.getenv('TARGET_DURATION') and self.get('target_duration'):
            self.target_duration = float(self.get('target_duration'))
        if not os.getenv('CLIPS_PER_RUN') and self.get('clips_per_run'):
            self.clips_per_run = int(self.get('clips_per_run'))
        if not os.getenv('TEXT_SIZE') and self.style.get('text_size'):
            self.text_size = int(self.style['text_size'])
        if not os.getenv('CAPTION_ENABLED') and self.style.get('caption_enabled') is not None:
            self.caption_enabled = bool(self.style['caption_enabled'])
        if not os.getenv('CAPTION_STYLE') and self.style.get('caption_style'):
            self.caption_style = str(self.style['caption_style']).strip().lower()
        if not os.getenv('CAPTION_FONT_SIZE') and self.style.get('caption_font_size'):
            self.caption_font_size = (int(self.style['caption_font_size']) or None)
        if not os.getenv('CAPTION_MAX_WORDS') and self.style.get('caption_max_words'):
            self.caption_max_words = (int(self.style['caption_max_words']) or None)
        if not os.getenv('CAPTION_PUNCH_RATIO') \
                and self.style.get('caption_punch_ratio') is not None:
            self.caption_punch_ratio = float(self.style['caption_punch_ratio'])
        # Edit structure is a style choice, so it follows the same path. An
        # unknown value in YAML is ignored rather than raising: a typo in a
        # style file should not stop a build.
        if not os.getenv('EDIT_STYLE') and self.style.get('edit_style'):
            candidate = str(self.style['edit_style']).strip().lower()
            if candidate in EDIT_STYLES:
                self.edit_style = candidate
            else:
                logger.warning('clipper.yaml style.edit_style=%r is not one of '
                               '%s; keeping %s', candidate,
                               ', '.join(EDIT_STYLES), self.edit_style)
        if not os.getenv('HOOK_TEXT_ENABLED') \
                and self.style.get('hook_text_enabled') is not None:
            self.hook_text_enabled = bool(self.style['hook_text_enabled'])
        if not os.getenv('HOOK_UPPERCASE') \
                and self.style.get('hook_uppercase') is not None:
            self.hook_uppercase = bool(self.style['hook_uppercase'])
        if not os.getenv('HOOK_MAX_WORDS') and self.style.get('hook_max_words'):
            self.hook_max_words = int(self.style['hook_max_words'])
        if not os.getenv('HOOK_Y_RATIO') \
                and self.style.get('hook_y_ratio') is not None:
            self.hook_y_ratio = float(self.style['hook_y_ratio'])
        if not os.getenv('HOOK_PREFER_TRANSCRIPT') \
                and self.style.get('hook_prefer_transcript') is not None:
            self.hook_prefer_transcript = bool(
                self.style['hook_prefer_transcript'])
        if not os.getenv('COLD_OPEN_SECONDS') \
                and self.style.get('cold_open_seconds') is not None:
            self.cold_open_seconds = float(self.style['cold_open_seconds'])
        if not os.getenv('HOOK_TAIL_SECONDS') \
                and self.style.get('hook_tail_seconds') is not None:
            self.hook_tail_seconds = float(self.style['hook_tail_seconds'])

    # -- path helpers ---------------------------------------------------
    def _path(self, env, default, create=True):
        path = Path(os.getenv(env, default)).expanduser()
        if not path.is_absolute():
            path = self.runtime_root / path
        return ensure_dir(path) if create else path

    def _resolve_path(self, value):
        path = Path(value).expanduser()
        return str(path if path.is_absolute() else PROJECT_ROOT / path)

    def _load_yaml(self):
        path = PROJECT_ROOT / 'config' / 'clipper.yaml'
        if not path.exists():
            return {}
        try:
            import yaml
            return yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        except Exception as exc:
            logger.error('clipper.yaml load failed: %s', exc)
            return {}

    def _load_channels(self):
        path = PROJECT_ROOT / 'config' / 'channels.yaml'
        if not path.exists():
            return {}
        try:
            import yaml
            raw = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
            return {str(k).strip().lower(): str(v).strip()
                    for k, v in raw.items()
                    if str(k).strip() and str(v).strip()}
        except Exception as exc:
            logger.error('channels.yaml load failed: %s', exc)
            return {}

    def channel_for_niche(self, niche: str) -> str:
        """Channel key for a niche, or '' when unbound."""
        return self.channel_map.get(str(niche or '').strip().lower(), '')

    def get(self, key, default=None):
        return self.defaults.get(key, default)

    # -- per-campaign runtime dirs --------------------------------------
    def campaign_source_dir(self, campaign_id: str) -> Path:
        return ensure_dir(self.sources_dir / campaign_id)

    def campaign_asset_dir(self, campaign_id: str) -> Path:
        return ensure_dir(self.assets_dir / campaign_id)

    def campaign_output_dir(self, campaign_id: str) -> Path:
        return ensure_dir(self.output_dir / campaign_id)

    def campaign_temp_dir(self, campaign_id: str) -> Path:
        return ensure_dir(self.temp_dir / campaign_id)

    @property
    def campaign_spec_dir(self) -> Path:
        return ensure_dir(PROJECT_ROOT / 'config' / 'campaigns')

    def resolve_font(self):
        if self.font and Path(self.font).exists():
            return self.font
        for candidate in ('C:/Windows/Fonts/impact.ttf',
                          'C:/Windows/Fonts/arialbd.ttf',
                          'C:/Windows/Fonts/segoeuib.ttf',
                          '/usr/share/fonts/truetype/dejavu/'
                          'DejaVuSans-Bold.ttf',
                          '/usr/share/fonts/truetype/liberation/'
                          'LiberationSans-Bold.ttf',
                          '/System/Library/Fonts/Supplemental/Impact.ttf'):
            if Path(candidate).exists():
                return candidate
        raise RuntimeError('No overlay font found. Set OVERLAY_FONT to an '
                           'existing .ttf file.')

    def resolve_emoji_font(self):
        if self.emoji_font and Path(self.emoji_font).exists():
            return self.emoji_font
        vendored = PROJECT_ROOT / 'assets' / 'fonts' / 'NotoColorEmoji.ttf'
        return str(vendored) if vendored.exists() else None


config = ClipperConfig()
