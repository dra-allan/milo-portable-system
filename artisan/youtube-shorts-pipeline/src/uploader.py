"""YouTube Shorts uploader with OAuth, identity verification and cleanup.

FOUR SAFETY PROPERTIES THIS FILE NOW HAS
----------------------------------------
1. **It cannot upload to the wrong channel.** The uploader already asked YouTube
   "who am I?" during auth and threw the answer away. On 2026-08-16 that cost
   four clips published to Chop UG under the ``wealth_mindset`` key. The answer
   is now checked against the key's recorded binding before any upload, and a
   mismatch raises instead of publishing. See :mod:`channel_guard`.

2. **It cannot hang a daemon on a browser prompt.** The old credential path fell
   through to ``InstalledAppFlow.run_local_server(port=0)`` whenever a token was
   missing or unrefreshable. In an unattended 9AM run that blocks forever
   waiting for a consent screen nobody will ever see -- which presents as "the
   sweep is still running" rather than as a failure. Interactive auth is now
   opt-in (``MILO_ALLOW_INTERACTIVE_AUTH=1``); otherwise it fails fast and names
   the channel to re-auth.

3. **It cannot borrow the default token (added 2026-08-23).** The token path was
   ``candidate if candidate.exists() else base``: a channel whose own token was
   missing or expired silently authenticated with the shared default token
   instead. That token belongs to a real channel, so the guard would then compare
   it against the requested key -- and because every ``channel_id`` in
   channels.yaml was still blank, ``learn`` mode *bound the wrong channel to the
   key* instead of rejecting it. The 8/16 failure mode with an extra layer of
   indirection. A missing token is now a refusal naming the channel to re-auth.

4. **It cannot publish with no channel key at all (added 2026-08-23).**
   ``verify_identity and channel`` meant ``channel=None`` skipped verification
   entirely and uploaded via the default token -- to whichever channel that
   happened to be. Twenty niches in ``config/niches.yaml`` declare no
   ``upload_channels``, and each of them constructs the uploader exactly that
   way. Set ``MILO_ALLOW_UNROUTED_UPLOAD=1`` if you ever genuinely want the old
   behaviour; nothing in the pipelines does.

All four are refusals rather than warnings. A failed run costs a sweep; a wrong-
channel upload costs a channel.
"""
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

try:
    from .utils import setup_logger
    from .config import config
    from . import channel_guard
except ImportError:  # pragma: no cover - direct script execution
    from utils import setup_logger
    from config import config
    import channel_guard

logger = setup_logger(__name__)
SCOPES = ['https://www.googleapis.com/auth/youtube.upload',
          'https://www.googleapis.com/auth/youtube',
          'https://www.googleapis.com/auth/youtube.force-ssl']
DEFAULT_CATEGORY_ID = '24'
_VIDEO_ID_RE = re.compile(
    r'youtube\.com/watch\?v=([A-Za-z0-9_-]{11})|youtu\.be/([A-Za-z0-9_-]{11})')


def _flag(name: str) -> bool:
    return (os.getenv(name) or '').strip().lower() in ('1', 'true', 'yes', 'on')


def _interactive_allowed() -> bool:
    return _flag('MILO_ALLOW_INTERACTIVE_AUTH')


def _lane() -> str:
    """Which pipeline is publishing. Shorts and clipper share this uploader."""
    return (os.getenv('MILO_PIPELINE_LANE') or 'shorts').strip().lower()


def _build(credentials):
    from googleapiclient.discovery import build
    return build('youtube', 'v3', credentials=credentials, cache_discovery=False)


class YouTubeUploader:
    def __init__(self, channel: Optional[str] = None, credentials_path: Optional[str] = None,
                 token_file: Optional[str] = None, privacy_status: Optional[str] = None,
                 verify_identity: bool = True, niche: str = ''):
        # Canonicalise before anything derives a filename or a binding from it.
        channel = channel_guard.resolve_key(channel) if channel else channel
        self.channel = channel
        self.niche = (niche or '').strip()
        if not channel and not _flag('MILO_ALLOW_UNROUTED_UPLOAD'):
            raise RuntimeError(
                'refusing to upload with no channel key. Without one this would '
                'authenticate with the shared default token and publish to '
                'whichever channel that token owns, with no identity check at '
                'all. Give the niche an upload_channels: entry in '
                'config/niches.yaml pointing at a key from '
                'artisan/yt-secrets/channels.yaml'
                + (f' (niche: {self.niche})' if self.niche else '')
                + '. Override with MILO_ALLOW_UNROUTED_UPLOAD=1 only if you '
                  'genuinely want the old behaviour.')
        self.privacy_status = (privacy_status or config.privacy_status).lower()
        # client_from: in channels.yaml lets a channel borrow another channel's
        # OAuth client. flick_shorts needs this: its own Google Cloud client was
        # deleted, so its own client secrets can never complete a flow again.
        client_key = channel_guard.client_source(channel) if channel else channel
        self.credentials_path = Path(
            credentials_path
            or config.oauth_client_secrets_for(client_key)
            or config.oauth_client_secrets_for(channel)
            or config.oauth_client_secrets
            or (config.project_root / 'credentials.json')
        )
        base = Path(config.oauth_token_file)
        # NO FALLBACK TO `base`. See property 3 in the module docstring: falling
        # back to the default token is how a missing token became a wrong-channel
        # binding rather than an error.
        candidate = base.with_name(f'youtube_token_{channel}.json') if channel else base
        self.token_file = Path(token_file) if token_file else candidate
        self.credentials = self._get_credentials()
        self.youtube = _build(self.credentials)
        self.actual_channel_id, self.actual_channel_title = self._channel_snapshot()
        if verify_identity and channel:
            # Raises ChannelIdentityError on a mismatch. Deliberately before any
            # upload method can be called, so there is no window in which a
            # mis-bound uploader exists and looks usable.
            channel_guard.assert_identity(
                channel, self.actual_channel_id, self.actual_channel_title,
                context='shorts upload')
            # Right channel, wrong content is its own accident: a ranking
            # channel or a Luganda gossip channel can hold a perfectly valid
            # token and still be the wrong home for this clip.
            channel_guard.assert_content(
                channel, pipeline=_lane(), niche=self.niche,
                context=f'{_lane()} upload')
        logger.info('CHANNEL_READY key=%s channel_id=%s title=%s niche=%s',
                    self.channel, self.actual_channel_id or 'unknown',
                    self.actual_channel_title or 'unknown', self.niche or '-')

    # ------------------------------------------------------------------
    def _get_credentials(self):
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        creds = None
        if self.token_file.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self.token_file), SCOPES)
            except (ValueError, OSError) as exc:
                logger.warning('TOKEN_UNREADABLE channel=%s path=%s error=%s',
                               self.channel, self.token_file.name, exc)
                creds = None
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as exc:
                help_text = channel_guard.deleted_client_help(exc)
                logger.error('TOKEN_REFRESH_FAILED channel=%s error=%s',
                             self.channel, str(exc)[:200])
                if help_text:
                    raise RuntimeError(
                        f'{self.channel}: OAuth client deleted.\n{help_text}'
                    ) from exc
                creds = None
        if creds and creds.valid:
            return creds
        return self._interactive_credentials()

    def _interactive_credentials(self):
        """Run the consent flow, or refuse when nobody can answer it.

        The refusal is the feature. An unattended sweep that reaches this point
        used to sit on an open socket until the run timed out, so a revoked
        token looked like a slow pipeline instead of a broken credential.
        """
        if not _interactive_allowed():
            raise RuntimeError(
                f'no usable token for channel {self.channel!r} at '
                f'{self.token_file}. Refusing to open an interactive OAuth '
                'flow in a non-interactive process (it would block until the '
                'run is killed), and refusing to fall back to the shared '
                'default token (that would publish to the wrong channel). '
                'Re-auth it:\n'
                f'  reauth_all_channels.bat --channel {self.channel}\n'
                'Or set MILO_ALLOW_INTERACTIVE_AUTH=1 for a human-run session.'
            )
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
        """``(channel_id, channel_title)`` for the credentials in hand.

        One quota unit. That is a rounding error next to the ~1600 an upload
        costs, and it is the only thing that can prove the target channel.
        """
        try:
            items = self.youtube.channels().list(
                part='snippet', mine=True).execute().get('items') or []
        except Exception as exc:
            logger.error('CHANNEL_LOOKUP_FAILED key=%s error=%s',
                         self.channel, str(exc)[:200])
            return '', ''
        if not items:
            return '', ''
        return (str(items[0].get('id') or ''),
                str((items[0].get('snippet') or {}).get('title') or ''))

    # ------------------------------------------------------------------
    def _credit_description(self, description: str) -> str:
        """Append a creator credit from the source video metadata when possible."""
        if 'Original creator:' in description or 'Original source:' in description:
            return description
        match = _VIDEO_ID_RE.search(description or '')
        if not match:
            return description
        video_id = match.group(1) or match.group(2)
        credit = f'Original source: https://www.youtube.com/watch?v={video_id}'
        try:
            items = self.youtube.videos().list(part='snippet', id=video_id).execute().get('items') or []
            if items:
                snippet = items[0].get('snippet') or {}
                creator = (snippet.get('channelTitle') or '').strip()
                channel_id = (snippet.get('channelId') or '').strip()
                if creator:
                    credit = f'Original creator: {creator}'
                    if channel_id:
                        credit += f' (https://www.youtube.com/channel/{channel_id})'
                    credit += f'\nOriginal source: https://www.youtube.com/watch?v={video_id}'
        except Exception as exc:
            logger.info('Creator lookup unavailable for %s: %s', video_id, exc)
        return (description.rstrip() + '\n\nCredits\n' + credit).strip()

    def upload_short(self, video_path: str, title: str, description: str, tags: List[str],
                     privacy_status: Optional[str] = None, category_id: str = DEFAULT_CATEGORY_ID,
                     publish_at: Optional[str] = None) -> Optional[str]:
        path = Path(video_path)
        if not path.exists():
            logger.error('UPLOAD_SKIP missing_file=%s', path)
            return None
        description = self._credit_description(description)
        status = (privacy_status or self.privacy_status).lower()
        body = {
            'snippet': {'title': title[:100], 'description': description[:5000],
                        'tags': [t for t in tags if t][:30], 'categoryId': str(category_id),
                        'defaultLanguage': 'en'},
            'status': {'privacyStatus': status, 'selfDeclaredMadeForKids': False},
        }
        if publish_at:
            body['status']['publishAt'] = publish_at
        try:
            from googleapiclient.http import MediaFileUpload
            media = MediaFileUpload(str(path), chunksize=10 * 1024 * 1024, resumable=True)
            request = self.youtube.videos().insert(part='snippet,status', body=body, media_body=media)
            response = None
            while response is None:
                _, response = request.next_chunk()
            vid = response.get('id')
            if vid:
                try:
                    path.unlink()
                    logger.info('CLEANUP_DONE channel=%s path=%s', self.channel, path.name)
                except OSError as exc:
                    logger.warning('CLEANUP_WARN channel=%s path=%s error=%s', self.channel, path.name, exc)
            # channel_id is logged on every upload on purpose: it is what makes
            # a wrong-channel incident findable in the log afterwards instead of
            # only visible on YouTube.
            logger.info('UPLOAD_DONE channel=%s channel_id=%s video_id=%s privacy=%s niche=%s',
                        self.channel, self.actual_channel_id or 'unknown', vid, status,
                        self.niche or '-')
            return vid
        except Exception as exc:
            logger.error('UPLOAD_FAIL channel=%s error=%s', self.channel, str(exc)[:240])
            return None

    def get_video_details(self, video_id):
        try:
            return (self.youtube.videos().list(part='snippet,statistics,status', id=video_id).execute().get('items') or [None])[0]
        except Exception:
            return None

    def fetch_statistics(self, video_id):
        details = self.get_video_details(video_id)
        stats = (details or {}).get('statistics') or {}
        def number(key):
            try:
                return int(stats.get(key, 0) or 0)
            except (TypeError, ValueError):
                return 0
        return {'views': number('viewCount'), 'likes': number('likeCount'),
                'comments': number('commentCount'),
                'favorites': number('favoriteCount')} if details else None

    @staticmethod
    def auth_for_channel(channel, credentials_path=None, token_file=None):
        """Authenticate one channel key and return the YouTube channel id.

        Identity is verified here too, so a first-time auth binds the key and a
        re-auth against the wrong Google account is rejected rather than
        silently overwriting a good token.

        Prefer ``reauth_all_channels.bat --channel <key>``: it opens the consent
        page in the right Chrome profile and writes the resolved channel id back
        into channels.yaml, neither of which happens here.
        """
        channel = channel_guard.resolve_key(channel)
        base = Path(config.oauth_token_file)
        token_file = token_file or str(base.with_name(f'youtube_token_{channel}.json'))
        uploader = YouTubeUploader(channel=channel, credentials_path=credentials_path,
                                   token_file=token_file, privacy_status='private')
        return uploader.actual_channel_id or None
