#!/usr/bin/env python3
"""
Rebuild processed_videos.db by scraping source video IDs from Short descriptions.
Each Short has "Full video: https://youtube.com/watch?v=VIDEO_ID" in its description.
"""
import os
import sys
import sqlite3
import json
import re
from pathlib import Path
from datetime import datetime, timezone

# Add artisan to path
ARTISAN = Path(__file__).resolve().parent
sys.path.insert(0, str(ARTISAN))

from yt_secrets.auth import load_channels
from yt_secrets.auth import token_path
from yt_secrets.auth import LEGACY_DIR
from yt_secrets.auth import load_channels
from yt_secrets.auth import SCOPES
from yt_secrets.auth import installed_client

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.auth.transport.requests import Request

# Regex to extract source video ID from description
SOURCE_URL_RE = re.compile(
    r'Full video:\s*https?://(?:www\.)?youtube\.com/watch\?v=([A-Za-z0-9_-]{11})',
    re.IGNORECASE
)

REPO_ROOT = ARTISAN.parent
DB_PATH = ARTISAN / "youtube-shorts-pipeline" / "data" / "processed_videos.db"

# Channel list from registry (active channels)
def get_active_channels():
    channels = load_channels()
    active = {k: v for k, v in channels.get("channels", {}).items() if v.get("active", True)}
    return active

def get_token_for_channel(channel_key, info):
    """Load and refresh token for a channel."""
    token_file = REPO_ROOT / info["token_dir"] / f"youtube_token_{channel_key}.json"
    if not token_file.exists():
        raise FileNotFoundError(f"Token not found: {token_file}")
    
    creds_data = json.loads(token_file.read_text())
    creds = Credentials(
        token=creds_data.get("token"),
        refresh_token=creds_data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=creds_data.get("client_id"),
        client_secret=creds_data.get("client_secret"),
        scopes=SCOPES
    )
    
    # Refresh if needed
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Save refreshed token
        token_file.write_text(creds.to_json())
    
    return creds

def build_youtube_client(channel_key, info):
    """Build YouTube API client for a channel using its token."""
    creds = get_token_for_channel(channel_key, info)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)

def get_uploads_playlist_id(youtube, channel_id):
    """Get the uploads playlist ID for a channel."""
    resp = youtube.channels().list(
        part="contentDetails",
        id=channel_id
    ).execute()
    items = resp.get("items", [])
    if not items:
        return None
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

def list_shorts_in_playlist(youtube, playlist_id):
    """List all videos in a playlist (handles pagination)."""
    videos = []
    next_page = None
    while True:
        resp = youtube.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=next_page
        ).execute()
        for item in resp.get("items", []):
            vid = item["contentDetails"]["videoId"]
            title = item["snippet"]["title"]
            published = item["snippet"]["publishedAt"]
            videos.append((vid, title, published))
        next_page = resp.get("nextPageToken")
        if not next_page:
            break
    return videos

def get_video_descriptions(youtube, video_ids):
    """Fetch descriptions for a batch of video IDs (max 50 per call)."""
    descriptions = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        resp = youtube.videos().list(
            part="snippet",
            id=",".join(batch)
        ).execute()
        for item in resp.get("items", []):
            vid = item["id"]
            desc = item["snippet"].get("description", "")
            descriptions[vid] = desc
    return descriptions

# Regex to extract source video ID from description
SOURCE_URL_RE = re.compile(
    r'Full video:\s*https?://(?:www\.)?youtube\.com/watch\?v=([A-Za-z0-9_-]{11})',
    re.IGNORECASE
)

def extract_source_id(description):
    """Extract source video ID from description."""
    m = SOURCE_URL_RE.search(description)
    if m:
        return m.group(1)
    return None

def get_active_channels():
    channels = load_channels()
    active = {k: v for k, v in channels.get("channels", {}).items() if v.get("active", True)}
    return active

def init_db():
    """Initialize processed_videos.db with the expected schema."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_videos (
            youtube_video_id TEXT PRIMARY KEY,
            niche TEXT,
            segment_index INTEGER,
            youtube_short_id TEXT,
            upload_channel TEXT,
            uploaded_at TEXT,
            local_path TEXT,
            status TEXT DEFAULT 'uploaded',
            source_video_id TEXT,
            title TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_processed_source ON processed_videos(source_video_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_processed_uploaded ON processed_videos(uploaded_at)
    """)
    conn.commit()
    return conn

def rebuild():
    channels = get_active_channels()
    print(f"Rebuilding DB for {len(channels)} active channels...")
    
    conn = init_db()
    total_recovered = 0
    
    for key, info in channels.items():
        if not info.get("active", True):
            continue
        
        channel_id = info.get("channel_id")
        if not channel_id:
            print(f"  {key}: no channel_id in registry, skipping")
            continue
        
        print(f"  Processing {key} (channel_id={channel_id})...")
        
        try:
            # Load token and build YouTube client
            creds = get_token_for_channel(key, info)
            youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
            
            # Get uploads playlist
            playlist_id = None
            resp = youtube.channels().list(
                part="contentDetails",
                id=channel_id
            ).execute()
            items = resp.get("items", [])
            if items:
                playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
            
            if not playlist_id:
                print(f"  No uploads playlist for {key}")
                continue
            
            print(f"  Processing {key} (channel_id={channel_id}, playlist={playlist_id})...")
            
            # List all videos in playlist
            videos = []
            next_page = None
            while True:
                resp = youtube.playlistItems().list(
                    part="snippet,contentDetails",
                    playlistId=playlist_id,
                    maxResults=50,
                    pageToken=next_page
                ).execute()
                for item in resp.get("items", []):
                    vid = item["contentDetails"]["videoId"]
                    title = item["snippet"]["title"]
                    published = item["snippet"]["publishedAt"]
                    videos.append((vid, title, published))
                next_page = resp.get("nextPageToken")
                if not next_page:
                    break
            
            print(f"  Found {len(videos)} videos in playlist")
            
            if not videos:
                continue
            
            # Get descriptions in batches
            video_ids = [v[0] for v in videos]
            descriptions = {}
            for i in range(0, len(video_ids), 50):
                batch = video_ids[i:i+50]
                resp = youtube.videos().list(
                    part="snippet",
                    id=",".join(batch)
                ).execute()
                for item in resp.get("items", []):
                    vid = item["id"]
                    desc = item["snippet"].get("description", "")
                    descriptions[vid] = desc
            
            # Extract source IDs and insert
            recovered = 0
            for vid, title, published in videos:
                desc = descriptions.get(vid, "")
                source_id = None
                m = re.search(r'Full video:\s*https?://(?:www\.)?youtube\.com/watch\?v=([A-Za-z0-9_-]{11})', desc, re.IGNORECASE)
                if m:
                    source_id = m.group(1)
                
                if not source_id:
                    continue
                
                # Parse published date
                try:
                    dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
                    uploaded_at = dt.astimezone(timezone.utc).isoformat()
                except Exception:
                    uploaded_at = datetime.now(timezone.utc).isoformat()
                
                # Insert into DB (ignore duplicates)
                try:
                    conn = sqlite3.connect(DB_PATH)
                    conn.execute("""
                        INSERT OR IGNORE INTO processed_videos 
                        (youtube_video_id, niche, segment_index, youtube_short_id, 
                         upload_channel, uploaded_at, source_video_id, title)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        vid,  # the Short's video ID as youtube_video_id
                        key,  # niche
                        0,    # segment_index - unknown, default 0
                        vid,  # youtube_short_id (the Short itself)
                        key,  # upload_channel
                        datetime.fromisoformat(published.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat(),
                        source_id,  # the source video ID we extracted
                        title
                    ))
                    conn.commit()
                    conn.close()
                    recovered += 1
                except sqlite3.Error as e:
                    print(f"    DB error for {vid}: {e}")
            
            print(f"  Recovered {recovered} source links for {key}")
            total_recovered += recovered
            
        except HttpError as e:
            print(f"  API error for {key}: {e}")
        except Exception as e:
            print(f"  Error processing {key}: {e}")
    
    print(f"\nDone. Total recovered: {total_recovered} entries")
    print(f"DB written to: {DB_PATH}")

if __name__ == "__main__":
    import re
    import json
    from datetime import datetime, timezone
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from google.oauth2.credentials import Credentials
    from googleapiclient.errors import HttpError
    from google.auth.transport.requests import Request
    import sqlite3
    
    rebuild()