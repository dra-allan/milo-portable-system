CREATE TABLE IF NOT EXISTS processed_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    youtube_video_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    published_at TIMESTAMP NOT NULL,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    niche TEXT NOT NULL,
    duration INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS generated_shorts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_video_id TEXT NOT NULL,
    segment_index INTEGER NOT NULL,
    start_time REAL NOT NULL,
    end_time REAL NOT NULL,
    youtube_short_id TEXT UNIQUE,
    uploaded_at TIMESTAMP,
    title TEXT NOT NULL,
    FOREIGN KEY (source_video_id) REFERENCES processed_videos(youtube_video_id)
);

CREATE INDEX IF NOT EXISTS idx_processed_videos_id ON processed_videos(youtube_video_id);
CREATE INDEX IF NOT EXISTS idx_generated_shorts_source ON generated_shorts(source_video_id);