import os
from pathlib import Path
from typing import Optional, List, Dict
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2 import service_account
try:  # package-relative first (python -m src.main)
    from .utils import setup_logger, format_timestamp
    from .config import config
except ImportError:  # pragma: no cover - direct script execution
    from utils import setup_logger, format_timestamp
    from config import config

logger = setup_logger(__name__)

class YouTubeUploader:
    def __init__(self):
        """Initialize YouTube API client"""
        if config.google_credentials_path and Path(config.google_credentials_path).exists():
            # Service account authentication
            credentials = service_account.Credentials.from_service_account_file(
                config.google_credentials_path,
                scopes=['https://www.googleapis.com/auth/youtube.upload']
            )
            self.youtube = build('youtube', 'v3', credentials=credentials)
        elif config.youtube_api_key:
            # API key authentication (limited functionality)
            self.youtube = build('youtube', 'v3', developerKey=config.youtube_api_key)
        else:
            raise ValueError("No YouTube authentication method configured")

        logger.info("YouTube API client initialized")

    def upload_short(self, video_path: str, title: str, description: str, tags: List[str]) -> Optional[str]:
        """
        Upload a Short to YouTube

        Args:
            video_path: Path to the video file
            title: Title for the Short (will be truncated to 100 chars)
            description: Description for the Short
            tags: List of tags (will be filtered to valid ones)

        Returns:
            YouTube video ID if successful, None otherwise
        """
        if not Path(video_path).exists():
            logger.error(f"Video file not found: {video_path}")
            return None

        # YouTube constraints
        title = title[:100]  # YouTube title limit
        description = description[:5000]  # YouTube description limit

        # Filter and limit tags
        valid_tags = [tag for tag in tags if tag and len(tag.strip()) > 0][:30]  # Max 30 tags

        try:
            request_body = {
                'snippet': {
                    'title': title,
                    'description': description,
                    'tags': valid_tags,
                    'categoryId': '24',  # Entertainment category
                    'defaultLanguage': 'en',
                },
                'status': {
                    'privacyStatus': 'public',
                    'madeForKids': False,
                    'selfDeclaredMadeForKids': False,
                }
            }

            media_file = MediaFileUpload(video_path, chunksize=-1, resumable=True)

            request = self.youtube.videos().insert(
                part='snippet,status',
                body=request_body,
                media_body=media_file
            )

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    logger.info(f"Upload progress: {int(status.progress() * 100)}%")

            video_id = response.get('id')
            logger.info(f"Successfully uploaded Short: {video_id}")
            return video_id

        except Exception as e:
            logger.error(f"Failed to upload Short: {str(e)}")
            return None

    def get_video_details(self, video_id: str) -> Optional[Dict]:
        """Get details of an uploaded video"""
        try:
            request = self.youtube.videos().list(
                part='snippet,statistics,status',
                id=video_id
            )
            response = request.execute()

            if response['items']:
                return response['items'][0]
            return None
        except Exception as e:
            logger.error(f"Failed to get video details for {video_id}: {str(e)}")
            return None

    def fetch_statistics(self, video_id: str) -> Optional[Dict]:
        """Pull view/like/comment counts for a video ID.

        The feedback loop: called after upload and on `--mode stats` to record
        how a clip actually performed. Returns a flat dict of ints, or None if
        the video can't be read (deleted, private to another account, etc).

        NOTE: statistics are only visible for the uploader's own videos under
        the same credentials used to upload. Reading them for arbitrary public
        videos may require the Data API to report them; private videos return
        nothing.
        """
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