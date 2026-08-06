import logging
import sys
from pathlib import Path
from typing import Optional
import time

def setup_logger(name: str, log_file: Optional[Path] = None) -> logging.Logger:
    """Set up logging configuration"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Prevent duplicate handlers
    if logger.handlers:
        return logger

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_format = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    # File handler (if specified)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.INFO)
        file_format = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
        )
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)

    return logger

def get_data_dir() -> Path:
    """Get the data directory path"""
    return Path(__file__).parent.parent / 'data'

def get_temp_dir() -> Path:
    """Get the temp directory path"""
    return Path(__file__).parent.parent / 'data' / 'temp'

def cleanup_temp_files(max_age_hours: int = 24):
    """Clean up temporary files older than specified hours"""
    import time
    temp_dir = get_temp_dir()
    current_time = time.time()

    for file_path in temp_dir.rglob('*'):
        if file_path.is_file():
            file_age = current_time - file_path.stat().st_mtime
            if file_age > (max_age_hours * 3600):
                try:
                    file_path.unlink()
                except OSError as e:
                    logging.warning(f"Could not delete {file_path}: {e}")

def format_timestamp(seconds: float) -> str:
    """Convert seconds to HH:MM:SS format for FFmpeg"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

def sanitize_filename(filename: str) -> str:
    """Sanitize string for use as filename"""
    import re
    # Remove or replace invalid characters
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # Remove leading/trailing spaces and dots
    filename = filename.strip(' .')
    return filename