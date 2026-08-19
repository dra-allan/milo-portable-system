"""YouTube publisher for the campaign clipper.

Shares the OAuth token layout used by the Shorts, POV and ranking lanes so one
authenticated channel serves every pipeline instead of each one holding its own
copy of the same grant.

One deliberate deviation from the ranking publisher: **the local file is not
deleted after upload.** In the ranking lane an upload is the end of the job. Here
it is the middle: the clip is not finished until its link has been accepted by
the campaign board, and that submission step can fail on its own. Deleting on
upload would leave nothing to retry with and no artefact to review.

WHY IDENTITY VERIFICATION MATTERS MOST IN THIS LANE
---------------------------------------------------
A campaign spec names an ``eligible_accounts`` list. Publishing a campaign clip
from the wrong channel is not merely an embarrassing upload -- it is a
submission to a paying board from an account that is not the eligible one, which
puts the linked account at risk. That account is the only thing here that cannot
be rebuilt from a git branch, so a mismatch aborts rather than warns.
"""

import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
        f'youtube_token_clipper_{channel}.json')
    return shared if shared.exists() else legacy


def _client_secrets(channel: Optional[str] = None) -> Path:
    override = os.getenv('POV_OAUTH_CLIENT_SECRETS', '').strip()
    if override and Path(override).exists():
        return Path(override).expanduser()
    # client_from: lets a channel borrow a live OAuth client when its own has
    # been deleted in Google Cloud.
    for key in filter(None, (channel_guard.client_source(channel) if channel else None,
                             channel)):
        candidate = _shared_dir() / f'credentials_{key}.json'
        if candidate.exists():
            return candidate
    shared = _shared_dir() / 'credentials.json'
    if shared.exists():
        return shared
    # The Shorts/POV/ranking lanes keep their client secrets at the pipeline
    # root; the clipper shares their token dir, so it shares their secrets too.
    sibling = _shared_dir().parent / 'credentials.json'
    if sibling.exists():
        return sibling
    return Path(config.oauth_client_secrets)


class ClipperPublisher:
    def __init__(self, channel: Optional[str] = None,
                 privacy_status: Optional[str] = None,
                 verify_identity: bool = True):
        self.channel = (channel or config.upload_channel or 'clipper').strip()
        self.privacy_status = (privacy_status or config.privacy_status
                               or 'private').lower()
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
                context='campaign clip upload')
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
                creds = Credentials.from_authorized_user_file(
                    str(self.token_file), SCOPES)
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
            raise RuntimeError(
                f'no usable token for channel {self.channel!r} at '
                f'{self.token_file}. Refusing to start an interactive OAuth '
                'flow in a non-interactive process. Re-auth in your own '
                f'terminal:\n  cd artisan && python -m yt_secrets auth '
                f'--channel {self.channel}')
        from google_auth_oauthlib.flow import InstalledAppFlow
        if not self.credentials_path.exists():
            raise FileNotFoundError('OAuth client secrets not found at '
                                    f'{self.credentials_path}')
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
               tags: List[str],
               privacy_status: Optional[str] = None) -> Optional[Dict]:
        """Upload and return ``{id, url}``. The local file is left in place."""
        path = Path(video_path)
        if not path.exists():
            logger.error('UPLOAD_SKIP missing_file=%s', video_path)
            return None
        status = (privacy_status or self.privacy_status or 'private').lower()
        body = {
            'snippet': {'title': title[:100],
                        'description': description[:5000],
                        'tags': [t for t in tags if t][:30],
                        'categoryId': DEFAULT_CATEGORY_ID,
                        'defaultLanguage': 'en'},
            'status': {'privacyStatus': status,
                       'selfDeclaredMadeForKids': False},
        }
        try:
            from googleapiclient.http import MediaFileUpload
            media = MediaFileUpload(str(path), chunksize=10 * 1024 * 1024,
                                    resumable=True)
            request = self.youtube.videos().insert(
                part='snippet,status', body=body, media_body=media)
            response = None
            while response is None:
                _, response = request.next_chunk()
            vid = response.get('id')
            if not vid:
                logger.error('UPLOAD_NO_ID channel=%s', self.channel)
                return None
            # Shorts URL form. The campaign board wants the link a viewer would
            # open, and /watch?v= for a vertical short redirects but looks wrong
            # to a human reviewer.
            url = f'https://www.youtube.com/shorts/{vid}'
            logger.info('UPLOAD_DONE channel=%s channel_id=%s video_id=%s '
                        'privacy=%s url=%s', self.channel,
                        self.actual_channel_id or 'unknown', vid, status, url)
            return {'id': vid, 'url': url, 'privacy': status}
        except Exception as exc:
            logger.error('UPLOAD_FAIL channel=%s error=%s', self.channel,
                         str(exc)[:240])
            return None

    def set_public(self, video_id: str) -> bool:
        """Flip a private upload public once it has been reviewed.

        The default upload privacy is private for a reason: a campaign clip that
        turns out to be non-compliant can be fixed before anyone sees it, but
        views only count from publication, so this has to be a one-call flip.
        """
        try:
            self.youtube.videos().update(
                part='status',
                body={'id': video_id,
                      'status': {'privacyStatus': 'public',
                                 'selfDeclaredMadeForKids': False}}).execute()
            logger.info('PRIVACY_PUBLIC video_id=%s', video_id)
            return True
        except Exception as exc:
            logger.error('PRIVACY_FAIL video_id=%s error=%s', video_id,
                         str(exc)[:200])
            return False

    def channel_id(self) -> Optional[str]:
        return self.actual_channel_id or None


def auth(channel: str) -> Optional[str]:
    """Run the OAuth flow for one channel key and report the channel id."""
    return ClipperPublisher(channel=channel,
                            privacy_status='private').actual_channel_id
