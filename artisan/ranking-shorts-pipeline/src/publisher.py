"""YouTube upload.

A deliberate copy of the shorts pipeline's OAuth flow rather than an import of
it. Two reasons: this pipeline must be able to move or be re-pointed without
the other one breaking, and more importantly the token files are namespaced
(``youtube_token_ranking_<channel>.json``) so a mistake here can never publish
to a channel the shorts pipeline owns.

OAuth desktop flow is the only mechanism that publishes to a channel: a service
account has no channel and an API key cannot write.
"""

import os
from pathlib import Path
from typing import Dict, List, Optional

from .config import config
from .utils import setup_logger

logger = setup_logger(__name__)

SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube',
]
DEFAULT_CATEGORY_ID = '24'  # Entertainment


class RankingPublisher:
    def __init__(self, channel: Optional[str] = None,
                 privacy_status: Optional[str] = None):
        self.channel = channel
        self.privacy_status = (privacy_status or config.privacy_status).lower()
        if self.privacy_status not in ('public', 'private', 'unlisted'):
            self.privacy_status = 'private'

        self.credentials_path = Path(config.oauth_client_secrets)
        base = Path(config.oauth_token_file)
        if channel:
            self.token_file = base.with_name(
                f'youtube_token_ranking_{channel}.json')
        else:
            self.token_file = base

        self.credentials = self._credentials()
        from googleapiclient.discovery import build
        self.youtube = build('youtube', 'v3', credentials=self.credentials,
                             cache_discovery=False)
        logger.info('publisher ready (channel=%s privacy=%s token=%s)',
                    channel, self.privacy_status, self.token_file.name)

    def _credentials(self):
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

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
            except RefreshError:
                creds = None
        if not creds or not creds.valid:
            if not self.credentials_path.exists():
                raise FileNotFoundError(
                    f'OAuth client secrets not found at '
                    f'{self.credentials_path}. Create a Desktop-app OAuth '
                    'client in Google Cloud Console, save it, and set '
                    'RANKING_OAUTH_CLIENT_SECRETS in config/.env.')
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.credentials_path), SCOPES)
            logger.info('no valid token; opening browser for OAuth login')
            creds = flow.run_local_server(port=0)

        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(creds.to_json(), encoding='utf-8')
        try:
            os.chmod(self.token_file, 0o600)
        except OSError:
            pass
        return creds

    def upload(self, video_path: str, title: str, description: str,
               tags: List[str],
               privacy_status: Optional[str] = None) -> Optional[str]:
        if not Path(video_path).exists():
            logger.error('video file not found: %s', video_path)
            return None
        status = (privacy_status or self.privacy_status).lower()
        body = {
            'snippet': {
                'title': title[:100],
                'description': description[:5000],
                'tags': [t for t in tags if t][:30],
                'categoryId': DEFAULT_CATEGORY_ID,
                'defaultLanguage': 'en',
            },
            'status': {
                'privacyStatus': status,
                'selfDeclaredMadeForKids': False,
            },
        }
        try:
            from googleapiclient.http import MediaFileUpload
            media = MediaFileUpload(video_path, chunksize=10 * 1024 * 1024,
                                    resumable=True)
            request = self.youtube.videos().insert(
                part='snippet,status', body=body, media_body=media)
            response = None
            while response is None:
                progress, response = request.next_chunk()
                if progress:
                    logger.info('upload %d%%', int(progress.progress() * 100))
            video_id = response.get('id')
            logger.info('uploaded -> https://youtu.be/%s (privacy=%s)',
                        video_id, status)
            return video_id
        except Exception as exc:  # noqa: BLE001
            logger.error('upload failed: %s', exc, exc_info=True)
            return None

    def channel_id(self) -> Optional[str]:
        try:
            response = self.youtube.channels().list(part='snippet',
                                                    mine=True).execute()
            items = response.get('items') or []
            return items[0]['id'] if items else None
        except Exception as exc:  # noqa: BLE001
            logger.error('could not resolve channel: %s', exc)
            return None


def auth(channel: str) -> Optional[str]:
    """One-time interactive login for a channel. Returns the channel id."""
    publisher = RankingPublisher(channel=channel, privacy_status='private')
    return publisher.channel_id()
