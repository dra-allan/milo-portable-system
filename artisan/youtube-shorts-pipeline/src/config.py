import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

class Config:
    def __init__(self, env_file=None):
        # Find the project root (this file's parent.parent)
        project_root = Path(__file__).parent.parent

        # Load environment variables
        if env_file is None:
            env_file = project_root / 'config' / '.env'
        load_dotenv(env_file)

        # YouTube API settings
        self.youtube_api_key = os.getenv('YOUTUBE_API_KEY')
        self.google_credentials_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')

        # Processing limits
        self.max_concurrent_videos = int(os.getenv('MAX_CONCURRENT_VIDEOS', '3'))
        self.min_segment_length = int(os.getenv('MIN_SEGMENT_LENGTH', '15'))
        self.max_segment_length = int(os.getenv('MAX_SEGMENT_LENGTH', '60'))
        self.temp_dir = Path(os.getenv('TEMP_DIR', './data/temp'))
        self.data_dir = Path(os.getenv('DATA_DIR', './data'))
        self.logs_dir = Path(os.getenv('LOG_DIR', './data/logs'))
        self.log_level = os.getenv('LOG_LEVEL', 'INFO')

        # Ensure directories exist
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        # Load niche configurations
        niches_file = project_root / 'config' / 'niches.yaml'
        with open(niches_file, 'r') as f:
            self.niches = yaml.safe_load(f)

    def get_niche_config(self, niche_name):
        return self.niches.get(niche_name, {})

# Global config instance
config = Config()