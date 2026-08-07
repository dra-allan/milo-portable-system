"""YouTube Shorts uploader with OAuth (desktop app) authentication.

The old uploader only supported service-account or API-key auth. Neither can
upload to a normal YouTube channel: service accounts have no channel, and an
API key cannot write. This version authenticates as the signed-in user via the
standard OAuth desktop flow (credentials.json + token.json), which is the only
mechanism that actually publishes to a channel.

Multi-channel: one Google account can own many channels. Each channel gets its
own token file named ``youtube_token_<channel>.json``. Pass ``channel=`` to
upload on behalf of that channel. If no per-channel token exists, the default
``youtube_token.json`` is used.
"""

import os
import time
from pathlib import Path
from typing import Optional, List, Dict

try:  # package-relative first (python -m src.main)
    from .utils import setup_logger
    from .config import config
except ImportError:  # pragma: no cover - direct script execution
    from utils import setup_logger
    from config import config

logger = setup_logger(__name__)

# Scopes required to upload, read stats, and (optionally) set thumbnails.
SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube',
    'https://www.googleapis.com/auth/youtube.force-ssl',
]

DEFAULT_CATEGORY_ID = '24'  # Entertainment
SHORTS_HASHTAG = '#Shorts'


def _build(credentials):
    from googleapiclient.discovery import build
    return build('youtube', 'v3', credentials=credentials, cache_discovery=False)


class YouTubeUploader:
    def __init__(self, channel: Optional[str] = None,
                 credentials_path: Optional[str] = None,
                 token_file: Optional[str] = None,
                 privacy_status: Optional[str] = None):
        """Initialize the YouTube client for a channel.

        Args:
            channel: channel key (e.g. 'flick_shorts'). Selects the token file
                ``config/youtube_token_<channel>.json`` if it exists, else the
                default token. Used to route uploads to different channels.
            credentials_path: override for the OAuth client secrets file.
            token_file: override for the token file.
            privacy_status: override for upload privacy ('public', 'private',
                'unlisted'). Defaults to the pipeline config value.
        """
        self.channel = channel
        self.privacy_status = (privacy_status or config.privacy_status).lower()
        if self.privacy_status not in ('public', 'private', 'unlisted'):
            self.privacy_status = 'private'

        self.credentials_path = Path(
            credentials_path or config.oauth_client_secrets
            or (config.project_root / 'credentials.json')  # fallback location
        )

        # Token selection: per-channel token wins, else the default.
        if token_file:
            self.token_file = Path(token_file)
        else:
            base = Path(config.oauth_token_file)
            if channel:
                candidate = base.with_name(f"youtube_token_{channel}.json")
                if candidate.exists():
                    self.token_file = candidate
                else:
                    self.token_file = base
                    logger.warning(
                        "No token for channel '%s' at %s; using default %s. "
                        "Run auth once per channel to route uploads correctly.",
                        channel, candidate, base,
                    )
            else:
                self.token_file = base

        self.credentials = self._get_credentials()
        self.youtube = _build(self.credentials)
        logger.info(
            "YouTube API client initialized (channel=%s, privacy=%s, token=%s)",
            channel, self.privacy_status, self.token_file.name,
        )

    # -- auth -----------------------------------------------------------
    def _get_credentials(self):
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        creds = None
        if self.token_file.exists():
            try:
                creds = Credentials.from_authorized_user_file(
                    str(self.token_file), SCOPES
                )
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
                    f"OAuth client secrets not found at {self.credentials_path}. "
                    "Create one in Google Cloud Console (APIs & Services -> "
                    "Credentials -> OAuth client ID -> Desktop app) and save it "
                    "as credentials.json, then set YOUTUBE_OAUTH_CLIENT_SECRETS "
                    "in .env."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(self.credentials_path), SCOPES
            )
            logger.info(
                "No valid token at %s. Opening browser for OAuth login...",
                self.token_file,
            )
            creds = flow.run_local_server(port=0)

        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.token_file, 'w', encoding='utf-8') as f:
            f.write(creds.to_json())
        try:
            os.chmod(self.token_file, 0o600)
        except OSError:
            pass
        logger.info("OAuth token saved -> %s", self.token_file)
        return creds

    # -- upload ---------------------------------------------------------
    def upload_short(self, video_path: str, title: str, description: str,
                     tags: List[str], privacy_status: Optional[str] = None,
                     category_id: str = DEFAULT_CATEGORY_ID,
                     publish_at: Optional[str] = None) -> Optional[str]:
        """Upload a Short to the configured channel.

        Args:
            video_path: path to the MP4 on disk.
            title: Short title (truncated to 100 chars; keep #Shorts).
            description: Short description (truncated to 5000 bytes).
            tags: list of tags (max 30).
            privacy_status: override per call.
            category_id: YouTube category id.
            publish_at: ISO-8601 UTC timestamp to schedule the publish
                (requires privacyStatus='private' while scheduled).

        Returns:
            YouTube video ID on success, None on failure.
        """
        if not Path(video_path).exists():
            logger.error("Video file not found: %s", video_path)
            return None

        title = title[:100]
        description = description[:5000]
        valid_tags = [t for t in tags if t and len(t.strip()) > 0][:30]
        status = (privacy_status or self.privacy_status).lower()
        if status not in ('public', 'private', 'unlisted'):
            status = 'private'

        body = {
            'snippet': {
                'title': title,
                'description': description,
                'tags': valid_tags,
                'categoryId': str(category_id),
                'defaultLanguage': 'en',
            },
            'status': {
                'privacyStatus': status,
                'selfDeclaredMadeForKids': False,
            },
        }
        if publish_at:
            body['status']['publishAt'] = publish_at

        try:
            from googleapiclient.http import MediaFileUpload
            media = MediaFileUpload(video_path, chunksize=10 * 1024 * 1024,
                                    resumable=True)
            request = self.youtube.videos().insert(
                part='snippet,status', body=body, media_body=media,
            )
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    pct = int(status.progress() * 100)
                    logger.info("Upload progress: %d%%", pct)
                    print(f"  Upload {pct}%", end='\r')
            print()
            video_id = response.get('id')
            logger.info(
                "Uploaded '%s' as %s -> https://youtu.be/%s (privacy=%s)",
                title, video_id, video_id, status,
            )
            return video_id
        except Exception as exc:
            logger.error("Failed to upload Short: %s", exc, exc_info=True)
            return None

    # -- metadata -------------------------------------------------------
    def get_video_details(self, video_id: str) -> Optional[Dict]:
        try:
            response = self.youtube.videos().list(
                part='snippet,statistics,status', id=video_id,
            ).execute()
            items = response.get('items') or []
            return items[0] if items else None
        except Exception as exc:
            logger.error("Failed to get video details for %s: %s", video_id, exc)
            return None

    def fetch_statistics(self, video_id: str) -> Optional[Dict]:
        """Pull view/like/comment counts for a video ID (feedback loop)."""
        details = self.get_video_details(video_id)
        if not details:
            return None
        stats = details.get('statistics') or {}

        def _count(key: str) -> int:
            try:
                return int(stats.get(key, 0) or 0)
            except (TypeError, ValueError):
                return 0

        return {
            'views': _count('viewCount'),
            'likes': _count('likeCount'),
            'comments': _count('commentCount'),
            'favorites': _count('favoriteCount'),
        }

    # -- channel helpers ------------------------------------------------
    def get_channel_id(self) -> Optional[str]:
        """Return the channel id the current token uploads to."""
        try:
            response = self.youtube.channels().list(
                part='snippet', mine=True,
            ).execute()
            items = response.get('items') or []
            if not items:
                return None
            return items[0]['id']
        except Exception as exc:
            logger.error("Could not resolve channel: %s", exc)
            return None

    @staticmethod
    def auth_for_channel(channel: str, credentials_path: str = None,
                         token_file: str = None) -> str:
        """One-time interactive login for a channel. Returns the channel ID.

        Always writes to the per-channel token file
        ``config/youtube_token_<channel>.json`` so that later ``channel=``
        uploads resolve to the right credentials. Explicit token_file overrides
        the default naming.
        """
        if token_file is None:
            base = Path(config.oauth_token_file)
            token_file = base.with_name(f"youtube_token_{channel}.json")
        up = YouTubeUploader(
            channel=channel, credentials_path=credentials_path,
            token_file=str(token_file), privacy_status='private',
        )
        channel_id = up.get_channel_id()
        logger.info(
            "Authenticated channel '%s' (id=%s, token=%s)",
            channel, channel_id, up.token_file,
        )
        return channel_id


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='YouTube OAuth / upload helper')
    sub = parser.add_subparsers(dest='cmd', required=True)

    a = sub.add_parser('auth', help='One-time login for a channel')
    a.add_argument('--channel', required=True, help='Channel key, e.g. flick_shorts')
    a.add_argument('--credentials', default=None, help='credentials.json path')
    a.add_argument('--token', default=None, help='token file path')

    u = sub.add_parser('upload', help='Upload a local file')
    u.add_argument('video', help='Path to MP4')
    u.add_argument('--channel', default=None)
    u.add_argument('--title', required=True)
    u.add_argument('--description', default='')
    u.add_argument('--tags', default='', help='Comma-separated tags')
    u.add_argument('--privacy', default=None,
                   choices=['public', 'private', 'unlisted'])
    u.add_argument('--credentials', default=None)
    u.add_argument('--token', default=None)

    args = parser.parse_args(argv)

    if args.cmd == 'auth':
        channel_id = YouTubeUploader.auth_for_channel(
            args.channel, credentials_path=args.credentials,
            token_file=args.token,
        )
        print(f"Channel '{args.channel}' authenticated: {channel_id}")
        return 0

    if args.cmd == 'upload':
        up = YouTubeUploader(
            channel=args.channel, credentials_path=args.credentials,
            token_file=args.token, privacy_status=args.privacy,
        )
        tags = [t.strip() for t in args.tags.split(',') if t.strip()]
        video_id = up.upload_short(
            args.video, args.title, args.description, tags,
            privacy_status=args.privacy,
        )
        if not video_id:
            print("Upload FAILED (see log).")
            return 1
        print(f"Uploaded: https://youtu.be/{video_id}")
        return 0
    return 2


if __name__ == '__main__':
    import sys
    sys.exit(main())
