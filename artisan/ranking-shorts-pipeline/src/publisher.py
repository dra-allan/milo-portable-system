"""YouTube publisher using the shared Shorts/POV OAuth token."""
import os
from pathlib import Path
from typing import List, Optional
from .config import config
from .utils import setup_logger
logger = setup_logger(__name__)
SCOPES = ['https://www.googleapis.com/auth/youtube.upload','https://www.googleapis.com/auth/youtube','https://www.googleapis.com/auth/youtube.force-ssl']
DEFAULT_CATEGORY_ID = '24'

def _shared_dir() -> Path:
    explicit = os.getenv('POV_SECRETS_DIR', '').strip()
    if explicit: return Path(explicit).expanduser()
    root = os.getenv('VIDEO_FACTORY_ROOT', '').strip()
    if root: return Path(root).expanduser() / 'pov' / 'config'
    return Path(config.oauth_token_file).parent

def _token_path(channel: str) -> Path:
    shared = _shared_dir() / f'youtube_token_{channel}.json'
    legacy = Path(config.oauth_token_file).with_name(f'youtube_token_ranking_{channel}.json')
    return shared if shared.exists() else legacy

def _client_secrets() -> Path:
    override = os.getenv('POV_OAUTH_CLIENT_SECRETS', '').strip()
    if override and Path(override).exists(): return Path(override).expanduser()
    shared = _shared_dir() / 'credentials.json'
    return shared if shared.exists() else Path(config.oauth_client_secrets)

class RankingPublisher:
    def __init__(self, channel: Optional[str] = None, privacy_status: Optional[str] = None):
        self.channel = channel or (os.getenv('RANKING_UPLOAD_CHANNEL') or 'rankdrop').strip()
        self.privacy_status = (privacy_status or os.getenv('UPLOAD_PRIVACY') or 'public').lower()
        self.credentials_path = _client_secrets(); self.token_file = _token_path(self.channel)
        self.credentials = self._credentials()
        from googleapiclient.discovery import build
        self.youtube = build('youtube','v3',credentials=self.credentials,cache_discovery=False)
        self.actual_channel_id = self.channel_id()
        logger.info('CHANNEL_READY key=%s actual_channel_id=%s', self.channel, self.actual_channel_id or 'unknown')

    def _credentials(self):
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        creds = None
        if self.token_file.exists():
            try: creds = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)
            except (ValueError, OSError): creds = None
        if creds and creds.expired and creds.refresh_token:
            try: creds.refresh(Request())
            except RefreshError: creds = None
        if not creds or not creds.valid:
            if not self.credentials_path.exists(): raise FileNotFoundError(f'OAuth client secrets not found at {self.credentials_path}')
            creds = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), SCOPES).run_local_server(port=0)
        self.token_file.parent.mkdir(parents=True, exist_ok=True); self.token_file.write_text(creds.to_json(), encoding='utf-8')
        try: os.chmod(self.token_file, 0o600)
        except OSError: pass
        return creds

    def upload(self, video_path: str, title: str, description: str, tags: List[str], privacy_status: Optional[str] = None) -> Optional[str]:
        path = Path(video_path)
        if not path.exists(): logger.error('UPLOAD_SKIP missing_file=%s', video_path); return None
        status = (privacy_status or self.privacy_status or 'public').lower()
        body = {'snippet': {'title': title[:100], 'description': description[:5000], 'tags': [t for t in tags if t][:30], 'categoryId': DEFAULT_CATEGORY_ID, 'defaultLanguage': 'en'}, 'status': {'privacyStatus': status, 'selfDeclaredMadeForKids': False}}
        try:
            from googleapiclient.http import MediaFileUpload
            media = MediaFileUpload(str(path), chunksize=10*1024*1024, resumable=True)
            request = self.youtube.videos().insert(part='snippet,status', body=body, media_body=media)
            response = None
            while response is None: _, response = request.next_chunk()
            vid = response.get('id')
            if vid:
                try: path.unlink()
                except OSError: pass
            logger.info('UPLOAD_DONE channel_key=%s actual_channel_id=%s video_id=%s privacy=%s', self.channel, self.actual_channel_id or 'unknown', vid, status)
            return vid
        except Exception as exc:
            logger.error('UPLOAD_FAIL channel_key=%s error=%s', self.channel, str(exc)[:240]); return None

    def channel_id(self):
        try:
            items = self.youtube.channels().list(part='snippet', mine=True).execute().get('items') or []
            return items[0].get('id') if items else None
        except Exception: return None

def auth(channel: str) -> Optional[str]:
    return RankingPublisher(channel=channel, privacy_status='private').actual_channel_id
