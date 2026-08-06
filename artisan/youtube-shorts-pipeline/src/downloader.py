import yt_dlp
import json
import os
from pathlib import Path
from typing import Dict, List, Optional
try:  # package-relative first (python -m src.main)
    from .utils import get_temp_dir, setup_logger, sanitize_filename
    from .config import config
except ImportError:  # pragma: no cover - direct script execution
    from utils import get_temp_dir, setup_logger, sanitize_filename
    from config import config

logger = setup_logger(__name__)

class YouTubeDownloader:
    def __init__(self):
        self.temp_dir = get_temp_dir()
        self.ydl_opts = {
            'format': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
            'outtmpl': str(self.temp_dir / '%(id)s.%(ext)s'),
            'writeautosub': True,
            'subtitleslangs': ['en'],
            'skip_unavailable_fragments': True,
            'quiet': True,
            'no_warnings': True,
        }

    def _get_existing_video_files(self, video_id: str) -> List[Path]:
        """Return list of existing files that start with video_id + '.' in temp_dir."""
        pattern = f"{video_id}.*"
        return list(self.temp_dir.glob(pattern))

    def _rename_to_title(self, file_path: Path, title: str) -> Path:
        """Rename file to sanitized title while preserving extension.
        Returns the new path (may be same if already named correctly)."""
        if not title:
            return file_path
        safe_title = sanitize_filename(title)
        # If file already has the desired name, return as-is
        if file_path.stem == safe_title and file_path.suffix:
            return file_path
        new_path = file_path.with_name(f"{safe_title}{file_path.suffix}")
        # Avoid overwriting existing file: add a counter if needed
        counter = 1
        while new_path.exists() and new_path != file_path:
            new_path = file_path.with_name(f"{safe_title}_{counter}{file_path.suffix}")
            counter += 1
        if new_path != file_path:
            try:
                file_path.rename(new_path)
                logger.info(f"Renamed '{file_path.name}' to '{new_path.name}'")
            except Exception as e:
                logger.warning(f"Failed to rename {file_path} to {new_path}: {e}")
                return file_path
        return new_path

    def _write_info_json(self, video_path: Path, metadata: dict):
        """Write metadata to a JSON file alongside the video."""
        info_path = video_path.with_suffix('.info.json')
        try:
            with open(info_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
            logger.debug(f"Wrote info file: {info_path.name}")
        except Exception as e:
            warning_msg = f"Failed to write info file for {video_path.name}: {e}"
            logger.warning(warning_msg)

    def download_video(self, video_id: str) -> Optional[Dict]:
        """
        Download a YouTube video (or reuse existing) and return metadata.
        The video file is renamed to a sanitized version of its title for easy identification.
        An accompanying .info.json file is written with metadata.
        """
        url = f"https://www.youtube.com/watch?v={video_id}"

        try:
            # Extract info first (needed for title whether we download or reuse)
            with yt_dlp.YoutubeDL({'skip_download': True, 'quiet': True, 'no_warnings': True}) as ydl:
                info = ydl.extract_info(url, download=False)
            if info is None:
                logger.error(f"Could not extract info for video ID {video_id}")
                return None
            title = info.get('title', '')
            # Determine if we already have a usable file
            existing_files = self._get_existing_video_files(video_id)
            video_path: Optional[Path] = None
            subtitle_path: Optional[Path] = None

            if existing_files:
                # Choose the most recently modified file
                existing_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                video_path = existing_files[0]
                logger.info(f"Re-using existing video file: {video_path.name}")
                # Rename to title-based name
                video_path = self._rename_to_title(video_path, title)
                # Also try to rename associated subtitle if present
                sub_candidate = self.temp_dir / f"{video_id}.en.vtt"
                if sub_candidate.exists():
                    subtitle_path = self._rename_to_title(sub_candidate, title)
                else:
                    # maybe subtitle has different language code; we ignore for now
                    subtitle_path = None
            else:
                # Need to download
                with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                    ydl.download([url])

                # After download, find the file(s) that match the video_id
                downloaded_files = self._get_existing_video_files(video_id)
                if not downloaded_files:
                    logger.error(f"Downloaded video not found for {video_id}")
                    return None
                # yt-dlp should have produced exactly one file (maybe with different extension)
                video_path = downloaded_files[0]
                # Rename to title-based name
                video_path = self._rename_to_title(video_path, title)
                # Rename subtitle if any
                sub_candidate = self.temp_dir / f"{video_id}.en.vtt"
                if sub_candidate.exists():
                    subtitle_path = self._rename_to_title(sub_candidate, title)
                else:
                    subtitle_path = None

            # Build metadata
            metadata = {
                'id': video_id,
                'title': title,
                'duration': info.get('duration', 0),
                'upload_date': info.get('upload_date'),
                'description': info.get('description', ''),
                'uploader': info.get('uploader', ''),
                'video_path': str(video_path) if video_path else None,
                'subtitle_path': str(subtitle_path) if subtitle_path else None,
                'thumbnail': info.get('thumbnail', ''),
                'tags': info.get('tags', []),
            }

            # Write info.json
            if video_path:
                self._write_info_json(Path(video_path), metadata)

            logger.info(f"Prepared video: {metadata['title']} ({video_id}) -> {Path(metadata['video_path']).name if metadata['video_path'] else 'None'}")
            return metadata

        except Exception as e:
            logger.error(f"Failed to process video {video_id}: {str(e)}")
            return None

    def search_videos_by_channel(self, channel_id: str, published_after: str, max_results: int = 10) -> List[Dict]:
        """
        Search for recent videos from a channel using yt-dlp's search capabilities
        Note: For production, consider using YouTube Data API directly for search
        """
        # This is a simplified version - in practice, you might want to use
        # the YouTube Data API for more reliable search
        logger.warning("Channel search via yt-dlp is limited; consider using YouTube Data API for search")
        return []