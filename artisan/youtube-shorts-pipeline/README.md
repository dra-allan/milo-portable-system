# YouTube Shorts Automation Pipeline

An automated system for discovering YouTube videos from specified niches, extracting engaging segments, transforming them into YouTube Shorts format (9:16 vertical), adding captions and branding, and uploading them automatically with proper metadata.

## Features

- 🔍 **Automatic Discovery**: Finds recent videos from configured YouTube channels in target niches
- 🎬 **Intelligent Clipping**: Uses speech-to-text and NLP to identify engaging moments
- 📹 **Vertical Formatting**: Automatically crops videos to 9:16 aspect ratio for Shorts
- 💬 **Burned-in Captions**: Adds synchronized captions from audio transcription
- 🎨 **Branding Support**: Optional intro/outro overlays for channel branding
- 🔊 **Audio Optimization**: Normalizes audio levels for consistent playback
- ☁️ **YouTube Upload**: Automatic upload to YouTube with optimized metadata
- ⏰ **Scheduled Runs**: Configure to run automatically 3x daily (morning, afternoon, evening)
- 🧹 **Auto Cleanup**: Temporary files cleaned up after processing

## System Requirements

- **OS**: Windows 10/11 (Linux/macOS should work with minor adjustments)
- **Python**: 3.8+ (recommended 3.9-3.11)
- **FFmpeg**: Must be installed and available in PATH
- **YouTube API**: Google Cloud project with YouTube Data API v3 enabled

## Installation

### 1. Clone the Repository

```bash
git clone <repository-url>
cd youtube-shorts-pipeline
```

### 2. Install FFmpeg

**Using Chocolatey (recommended):**
```bash
choco install ffmpeg
```

**Manual Installation:**
1. Download FFmpeg from https://ffmpeg.org/download.html
2. Extract the ZIP file
3. Add the `bin` directory to your system PATH
4. Verify installation: `ffmpeg -version`

### 3. Set Up Python Environment

```bash
# Create virtual environment
python -m venv venv
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 4. Configure YouTube API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing one
3. Enable YouTube Data API v3
4. Create credentials:
   - **Option A (API Key)**: Create API key under "APIs & Services > Credentials"
   - **Option B (OAuth 2.0)**: Create OAuth 2.0 Client ID (Desktop app type)
5. Copy credentials to the project:
   - For API key: Set `YOUTUBE_API_KEY` in `.env` file
   - For OAuth: Download JSON credentials and set `GOOGLE_APPLICATION_CREDENTIALS` in `.env`

### 5. Configure Niches and Channels

Edit `config\niches.yaml` to specify your target niches, channels, and keywords:

```yaml
gaming:
  channels:
    - UCXuqSBlHAE6Xw-yeJA0Tunw  # Example: PewDiePie
    - UC-lHJZR3Gqxm24_Vd_AJ5Yw   # Example: Markiplier
  keywords: ["gameplay", "highlight", "clutch", "epic", "win", "moment"]
  min_duration: 600    # 10 minutes minimum
  max_duration: 7200   # 2 hours maximum
```

### 6. Set Environment Variables

Copy the template and fill in your values:

```bash
copy config\.env.template config\.env
# Edit .env with your YouTube API key or credentials path
```

## Usage

### Run Once (for testing)

```bash
# Process all configured niches
python main.py --mode once

# Process specific niche
python main.py --mode once --niche gaming --videos 3
```

### Run on Schedule (3x daily)

```bash
python main.py --mode schedule
```
This will run the pipeline at 9 AM, 2 PM, and 7 PM daily.
*Press Ctrl+C to stop the scheduler*

### Test Components

```bash
python main.py --mode test
```

## Directory Structure

```
youtube-shorts-pipeline/
├── config/
│   ├── .env.template          # Template for environment variables
│   ├── niches.yaml            # Niche-specific channel and keyword configs
│   └── logging.conf           # Logging configuration (optional)
├── data/
│   ├── logs/                  # Pipeline logs
│   ├── processed_videos.db    # SQLite database for tracking
│   └── temp/                  # Temporary processing files (auto-cleaned)
├── src/
│   ├── __init__.py
│   ├── config.py              # Configuration loading
│   ├── downloader.py          # YouTube video downloading (yt-dlp)
│   ├── transcriber.py         # Speech-to-text transcription (faster-whisper)
│   ├── processor.py           # Content analysis and highlight detection
│   ├── video_editor.py        # Video processing (FFmpeg wrappers)
│   ├── uploader.py            # YouTube upload (Google API v3)
│   ├── scheduler.py           # Job scheduling (APScheduler)
│   ├── main.py                # Entry point and pipeline orchestration
│   └── utils.py               # Helper functions (logging, paths, etc.)
├── niches/                    # Optional: niche-specific keyword files
│   ├── gaming/
│   │   └── keywords.txt
│   ├── trading/
│   │   └── keywords.txt
│   └── movies/
│       └── keywords.txt
├── requirements.txt           # Python dependencies
├── README.md                  # This file
└── tests/                     # Unit tests
    ├── test_processor.py
    └── run_tests.py
```

## How It Works

### 1. Content Discovery
- Scheduler triggers pipeline runs at configured times
- For each niche, searches YouTube for recent videos from specified channels
- Filters out already processed videos using local SQLite database

### 2. Download & Preparation
- Downloads selected videos using yt-dlp (best available quality up to 1080p)
- Extracts audio for transcription
- Saves video metadata for tracking

### 3. Content Analysis
- Transcribes audio using faster-whisper (local, no API costs)
- Analyzes transcript for engaging segments using:
  - Speech density (words per second)
  - Niche-specific keyword matching
  - Enthusiasm signals (exclamations, questions, caps)
  - Natural pause boundaries
  - Optimal length preferences (15-60 seconds)
- Applies non-maximum suppression to avoid overlapping clips

### 4. Video Processing
- Extracts segment from source video
- Crops to 9:16 vertical format (center-weighted or face-detection if OpenCV available)
- Adds burned-in captions from transcript
- Optional: Adds intro/outro branding clips
- Normalizes audio using FFmpeg loudnorm filter

### 5. Upload & Tracking
- Generates optimized title, description, and tags
- Uploads to YouTube using Data API v3
- Records uploaded Short in database with source mapping
- Cleans up temporary files

### 6. Cleanup
- Removes downloaded source videos after processing
- Removes temporary segment files
- Periodically cleans up old files (>24 hours)

## Configuration Options

### `.env` File
```env
YOUTUBE_API_KEY=your_youtube_data_api_key_here
# OR
GOOGLE_APPLICATION_CREDENTIALS=path/to/credentials.json

MAX_CONCURRENT_VIDEOS=3
MIN_SEGMENT_LENGTH=15
MAX_SEGMENT_LENGTH=60
TEMP_DIR=./data/temp
LOG_LEVEL=INFO

RUN_TIMES=0 9 * * *,0 14 * * *,0 19 * * *  # 9AM, 2PM, 7PM
```

### `niches.yaml` File
```yaml
NICHE_NAME:
  channels:
    -CHANNEL_ID_1
    -CHANNEL_ID_2
  keywords: ["keyword1", "keyword2", "keyword3"]
  min_duration: 300   # 5 minutes
  max_duration: 3600  # 1 hour
  ranking_mode: false # optional; see "Ranking / countdown niches" below
```

#### Keyword matching is word-boundary based
`keywords` and `negative_keywords` match **whole words and phrases**, not raw
substrings. This matters most for short negative keywords: with substring
matching, `live` rejected *"Cars Ever De**live**red"*, `dance` rejected
*"Abun**dance**"*, and `guide` rejected *"**Guide**d Missiles"* — which threw
away most legitimate sources on list-style channels. Multi-word phrases still
work (`live stream` matches *"Live stream: full show"*), and keywords with
punctuation are handled (`#shorts`, `vs`).

Because of this, prefer **format-level** negative keywords (`compilation`,
`reaction`, `gameplay`, `#shorts`) over bare topic words.

### Ranking / countdown niches

Set `ranking_mode: true` to switch highlight scoring into countdown-aware mode.
Use it for Top-10 / "ranked worst to best" sources.

A list video is only clippable at **item boundaries** — a clip starting halfway
through item four has no setup and no payoff. With `ranking_mode` enabled the
scorer:

- **boosts** clips that *open* on an enumeration cue (*"Coming in at number
  four…"*), because those are self-contained,
- **boosts** clips containing the #1 payoff (*"and the number one spot goes
  to…"*), the retention peak of any countdown,
- **penalises** mid-item narration with no enumeration cue,
- **penalises** clips spanning many boundaries (that's a compilation, and it
  loses the payoff structure).

The flag defaults to `false`, so every other niche scores exactly as before.
The reference implementation is the `ranking_general_commentary` niche.

**Onboarding a ranking channel:**
```bash
# 1. Authenticate the upload channel (creates config/youtube_token_<niche>.json)
python -m src.add_channel ranking_general_commentary

# 2. Dry-run discovery: no downloads, just show what would be picked up
python -m src.main --mode discover --niche ranking_general_commentary

# 3. Render locally without uploading, to sanity-check clip quality first
python -m src.main --mode once --niche ranking_general_commentary --no-upload
```

## Database Schema

The pipeline uses a SQLite database (`data/processed_videos.db`) with two tables:

```sql
CREATE TABLE processed_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    youtube_video_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    published_at TIMESTAMP NOT NULL,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    niche TEXT NOT NULL,
    duration INTEGER NOT NULL
);

CREATE TABLE generated_shorts (
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
```

## Legal and Fair Use Considerations

This tool is designed to help create transformative content that complies with fair use guidelines:

1. **Transformation**: Clips are significantly transformed through:
   - Temporal extraction (using only interesting segments)
   - Format conversion (landscape to vertical 9:16)
   - Added value (captions, branding, commentary context)
   - Different purpose/audience (Shorts format vs original long-form)

2. **Amount Used**: Clips are typically 15-60 seconds, representing a small portion of the original work

3. **Market Effect**: Shorts drive viewers to original content rather than replacing it

4. **Attribution**: Descriptions include links to full videos and channel recommendations

**Users should still:**
- Review generated content for compliance
- Consider adding explicit transformation commentary when possible
- Respect copyright takedown notices
- Avoid excessive use of any single creator's content
- Focus on content that is genuinely transformed and provides new value

## Extending the Pipeline

### Adding New Niches
1. Add entry to `config\niches.yaml`
2. Optionally create keyword files in `niches/NICHE_NAME/keywords.txt`
3. Restart the pipeline

### Customizing Processing
- Adjust `MIN_SEGMENT_LENGTH` and `MAX_SEGMENT_LENGTH` in `.env`
- Modify scoring algorithm in `src\processor.py`
- Change Whisper model size in `main.py` (trade speed vs accuracy)
- Add actual intro/outro assets and update `video_editor.py`

### Improving Video Discovery
- Replace placeholder video IDs in `main.py` with actual YouTube API search
- Implement channel-based search using `downloader.py` (to be enhanced)
- Add video filtering by views, likes, or engagement metrics

## Troubleshooting

### Common Issues

**FFmpeg not found**
- Ensure FFmpeg is installed and in PATH
- Test with: `ffmpeg -version`
- On Windows, you may need to restart your terminal after installation

**YouTube API quota exceeded**
- Check quota usage in Google Cloud Console
- The pipeline is designed to stay within free limits (~10,000 units/day)
- Consider reducing frequency or batch size if consistently hitting limits

**Transcription failures**
- Ensure audio was extracted properly
- Try a different Whisper model size (tiny/base/small)
- Check audio file format and quality

**Upload failures**
- Verify YouTube API credentials are correct
- Check that the video file meets YouTube requirements
- Look at detailed logs for specific API error messages

### Logs
- Console output shows real-time progress
- Detailed logs saved to `data/logs/pipeline.log`
- Check logs for debugging information

## Future Enhancements

- [ ] Face detection for smart cropping (keep speaker centered)
- [ ] Scene detection for better segment boundaries
- [ ] Duplicate content detection (hash-based)
- [ ] Performance analytics dashboard
- [ ] Multiple upload accounts/rotation
- [ ] Custom intro/outro assets per niche
- [ ] Language detection and translation for captions
- [ ] Integration with video hosting platforms beyond YouTube
- [ ] Web dashboard for monitoring and manual override

## License

MIT License - see LICENSE file for details

## Disclaimer

This tool is for educational purposes only. Users are responsible for ensuring their use complies with all applicable laws, regulations, and platform terms of service. Always verify that your content constitutes fair use and provides sufficient transformation.