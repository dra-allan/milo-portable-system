"""YouTube publisher using the shared Shorts/POV OAuth token.

Three things this lane gained on 2026-08-19, all shared with the shorts lane:

* **identity verification.** ``actual_channel_id`` was already fetched and
  logged, and nothing ever compared it to anything. That is how a token minted
  against the wrong Google account published to the wrong channel for two days
  without a single error line. It is now checked against the channel key's
  binding before any upload.
* **no unattended consent flow.** ``run_local_server(port=0)`` inside a
  scheduled daemon blocks forever on a browser nobody will open, so a revoked
  token looked like a slow run instead of a broken credential.
* **deleted_client gets the runbook** rather than an opaque OAuth error.

Uploads here still delete the local file on success: in this lane an upload is
the end of the job (unlike the clipper, where the board submission comes after).
"""
import os
from pathlib import Path
from typing import List, Optional, Tuple

from .config import config
from .utils import setup_logger

try:
    from . import channel_guard
except ImportError:  # pragma: no cover - direct script execution
    import channel_guard

logger = setup_logger(__name__)
SCOPES = ['https://www.googleapis.com/auth/youtube.upload',
          'https://www.googleapis.com/auth/youtube',
          'https://www.googleapis.com/auth/youtube.force-ssl']
DEFAULT_CATEGORY_ID = '24'


def _interactive_allowed() -> bool:
    return (os.getenv('MILO_ALLOW_INTERACTIVE_AUTH') or '').strip().lower() in (
        '1', 'true', 'yes', 'on')


def _shared_dir() -> Path:
    explicit = os.getenv('POV_SECRETS_DIR', '').strip()
    if explicit:
        return Path(explicit).expanduser()
    root = os.getenv('VIDEO_FACTORY_ROOT', '').strip()
    if root:
        return Path(root).expanduser() / 'pov' / 'config'
    return Path(config.oauth_token_file).parent


def _token_path(channel: str) -> Path:
    shared = _shared_dir() / f'youtube_token_{channel}.json'
    legacy = Path(config.oauth_token_file).with_name(
        f'youtube_token_ranking_{channel}.json')
    return shared if shared.exists() else legacy


def _client_secrets(channel: Optional[str] = None) -> Path:
    """OAuth client JSON for a channel, following ``client_from:``.

    A channel whose own Google Cloud client was deleted (flick_shorts) can point
    at a live project's client in channels.yaml. The grant is per Google
    account, so borrowing a client changes whose quota is spent, not which
    channel the token controls.
    """
    override = os.getenv('POV_OAUTH_CLIENT_SECRETS', '').strip()
    if override and Path(override).exists():
        return Path(override).expanduser()
    for key in filter(None, (channel_guard.client_source(channel) if channel else None,
                             channel)):
        per_channel = _shared_dir() / f'credentials_{key}.json'
        if per_channel.exists():
            return per_channel
        per_channel = Path(config.oauth_client_secrets).with_name(
            f'youtube_client_secrets_ranking_{key}.json')
        if per_channel.exists():
            return per_channel
    shared = _shared_dir() / 'credentials.json'
    return shared if shared.exists() else Path(config.oauth_client_secrets)


class RankingPublisher:
    def __init__(self, channel: Optional[str] = None,
                 privacy_status: Optional[str] = None,
                 verify_identity: bool = True):
        self.channel = channel or (os.getenv('RANKING_UPLOAD_CHANNEL') or 'rankdrop').strip()
        self.privacy_status = (privacy_status or os.getenv('UPLOAD_PRIVACY') or 'public').lower()
        self.credentials_path = _client_secrets(self.channel)
        self.token_file = _token_path(self.channel)
        self.credentials = self._credentials()
        from googleapiclient.discovery import build
        self.youtube = build('youtube', 'v3', credentials=self.credentials,
                             cache_discovery=False)
        self.actual_channel_id, self.actual_channel_title = self._channel_snapshot()
        if verify_identity:
            channel_guard.assert_identity(
                self.channel, self.actual_channel_id, self.actual_channel_title,
                context='ranking upload')
        logger.info('CHANNEL_READY key=%s actual_channel_id=%s title=%s',
                    self.channel, self.actual_channel_id or 'unknown',
                    self.actual_channel_title or 'unknown')

    def _credentials(self):
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        creds = None
        if self.token_file.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)
            except (ValueError, OSError):
                creds = None
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as exc:
                help_text = channel_guard.deleted_client_help(exc)
                if help_text:
                    raise RuntimeError(
                        f'{self.channel}: OAuth client deleted.\n{help_text}') from exc
                logger.error('TOKEN_REFRESH_FAILED channel=%s error=%s',
                             self.channel, str(exc)[:200])
                creds = None
        if creds and creds.valid:
            return creds
        if not _interactive_allowed():
            # Refusing beats hanging. In a scheduled run the consent flow waits
            # on a browser that will never open, which reads as "still running".
            raise RuntimeError(
                f'no usable token for channel {self.channel!r} at '
                f'{self.token_file}. Refusing to start an interactive OAuth '
                'flow in a non-interactive process. Re-auth in your own '
                f'terminal:\n  cd artisan && python -m yt_secrets auth '
                f'--channel {self.channel}')
        from google_auth_oauthlib.flow import InstalledAppFlow
        if not self.credentials_path.exists():
            raise FileNotFoundError(
                f'OAuth client secrets not found at {self.credentials_path}')
        try:
            creds = InstalledAppFlow.from_client_secrets_file(
                str(self.credentials_path), SCOPES).run_local_server(port=0)
        except Exception as exc:
            help_text = channel_guard.deleted_client_help(exc)
            if help_text:
                raise RuntimeError(
                    f'{self.channel}: OAuth client deleted.\n{help_text}') from exc
            raise
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(creds.to_json(), encoding='utf-8')
        try:
            os.chmod(self.token_file, 0o600)
        except OSError:
            pass
        return creds

    def _channel_snapshot(self) -> Tuple[str, str]:
        try:
            items = self.youtube.channels().list(
                part='snippet', mine=True).execute().get('items') or []
        except Exception as exc:
            logger.error('CHANNEL_LOOKUP_FAILED key=%s error=%s', self.channel,
                         str(exc)[:200])
            return '', ''
        if not items:
            return '', ''
        return (str(items[0].get('id') or ''),
                str((items[0].get('snippet') or {}).get('title') or ''))

    def upload(self, video_path: str, title: str, description: str,
               tags: List[str], privacy_status: Optional[str] = None) -> Optional[str]:
        path = Path(video_path)
        if not path.exists():
            logger.error('UPLOAD_SKIP missing_file=%s', video_path)
            return None
        status = (privacy_status or self.privacy_status or 'public').lower()
        body = {'snippet': {'title': title[:100], 'description': description[:5000],
                            'tags': [t for t in tags if t][:30],
                            'categoryId': DEFAULT_CATEGORY_ID,
                            'defaultLanguage': 'en'},
                'status': {'privacyStatus': status,
                           'selfDeclaredMadeForKids': False}}
        try:
            from googleapiclient.http import MediaFileUpload
            media = MediaFileUpload(str(path), chunksize=10 * 1024 * 1024, resumable=True)
            request = self.youtube.videos().insert(part='snippet,status',
                                                  body=body, media_body=media)
            response = None
            while response is None:
                _, response = request.next_chunk()
            vid = response.get('id')
            if vid:
                try:
                    path.unlink()
                except OSError:
                    pass
            logger.info('UPLOAD_DONE channel_key=%s actual_channel_id=%s '
                        'video_id=%s privacy=%s', self.channel,
                        self.actual_channel_id or 'unknown', vid, status)
            return vid
        except Exception as exc:
            logger.error('UPLOAD_FAIL channel_key=%s error=%s', self.channel,
                         str(exc)[:240])
            return None

    def channel_id(self):
        return self.actual_channel_id or None


def auth(channel: str) -> Optional[str]:
    return RankingPublisher(channel=channel,
                           privacy_status='private').actual_channel_id
