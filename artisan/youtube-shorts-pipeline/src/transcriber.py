import os
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from faster_whisper import WhisperModel
try:  # package-relative first (python -m src.main)
    from .utils import setup_logger, format_timestamp
    from .config import config
except ImportError:  # pragma: no cover - direct script execution
    from utils import setup_logger, format_timestamp
    from config import config

logger = setup_logger(__name__)

class VideoTranscriber:
    def __init__(self, model_size: str = "base", device: str = "cpu"):
        """
        Initialize the Whisper transcription model

        Args:
            model_size: Size of Whisper model (tiny, base, small, medium, large)
            device: Device to run on (cpu, cuda)
        """
        self.model_size = model_size
        self.device = device

        # Initialize model
        try:
            self.model = WhisperModel(
                model_size,
                device=device,
                compute_type="int8" if device == "cpu" else "float16"
            )
            logger.info(f"Whisper model '{model_size}' loaded on {device}")
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {str(e)}")
            raise

    def _get_audio_duration(self, audio_path: str) -> float:
        """
        Get audio duration in seconds using ffprobe

        Args:
            audio_path: Path to audio file

        Returns:
            Duration in seconds
        """
        try:
            cmd = [
                'ffprobe', '-v', 'error', '-show_entries',
                'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1',
                audio_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                return float(result.stdout.strip())
            else:
                logger.warning(f"Could not get audio duration: {result.stderr}")
                return 0.0
        except Exception as e:
            logger.warning(f"Error getting audio duration: {str(e)}")
            return 0.0

    def _extract_audio_chunk(self, input_path: str, output_path: str, start_time: float, duration: float) -> bool:
        """
        Extract a chunk from audio file using ffmpeg

        Args:
            input_path: Path to input audio file
            output_path: Path for output audio chunk
            start_time: Start time in seconds
            duration: Duration in seconds

        Returns:
            True if successful, False otherwise
        """
        try:
            cmd = [
                'ffmpeg',
                '-ss', str(start_time),
                '-i', input_path,
                '-t', str(duration),
                '-acodec', 'pcm_s16le',  # PCM 16-bit little-endian
                '-ar', '16000',  # 16kHz sample rate
                '-ac', '1',  # Mono
                '-y',  # Overwrite output file
                output_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"FFmpeg audio chunk extraction failed: {result.stderr}")
                return False
            return Path(output_path).exists()
        except Exception as e:
            logger.error(f"Error extracting audio chunk: {str(e)}")
            return False

    def transcribe_audio(self, audio_path: str) -> Optional[List[Dict]]:
        """
        Transcribe audio file to text with timestamps, handling long files by chunking

        Args:
            audio_path: Path to audio file (WAV format recommended)

        Returns:
            List of segments with 'text', 'start', 'end', 'confidence' keys
            or None if transcription failed
        """
        if not Path(audio_path).exists():
            logger.error(f"Audio file not found: {audio_path}")
            return None

        try:
            # Check audio duration
            duration = self._get_audio_duration(audio_path)
            logger.info(f"Audio duration: {duration:.2f} seconds")

            # Define maximum chunk duration (30 seconds) to avoid memory issues
            MAX_CHUNK_DURATION = 30.0  # 30 seconds

            if duration <= MAX_CHUNK_DURATION:
                # Short audio, process normally
                return self._transcribe_audio_segment(audio_path, 0.0)
            else:
                # Long audio, process in chunks
                logger.info(f"Audio is long ({duration:.2f}s), processing in chunks of {MAX_CHUNK_DURATION}s")

                all_segments = []
                chunk_duration = MAX_CHUNK_DURATION
                overlap_duration = 2.0  # 2 seconds overlap to avoid missing speech at boundaries

                start_time = 0.0
                chunk_index = 0

                while start_time < duration:
                    # Calculate actual chunk duration (avoid going beyond file end)
                    actual_duration = min(chunk_duration, duration - start_time)
                    if actual_duration <= 0:
                        break

                    # Create temporary file for this chunk
                    chunk_path = str(Path(audio_path).parent / f"{Path(audio_path).stem}_chunk_{chunk_index}.wav")

                    logger.info(f"Processing chunk {chunk_index}: {start_time:.2f}-{start_time + actual_duration:.2f}s")

                    # Extract chunk
                    if not self._extract_audio_chunk(audio_path, chunk_path, start_time, actual_duration):
                        logger.error(f"Failed to extract chunk {chunk_index}")
                        # Clean up any temporary files
                        self._cleanup_chunks(Path(audio_path).parent, Path(audio_path).stem)
                        return None

                    # Transcribe chunk
                    chunk_segments = self._transcribe_audio_segment(chunk_path, start_time)
                    if chunk_segments is None:
                        logger.error(f"Failed to transcribe chunk {chunk_index}")
                        # Clean up any temporary files
                        self._cleanup_chunks(Path(audio_path).parent, Path(audio_path).stem)
                        return None

                    all_segments.extend(chunk_segments)

                    # Clean up chunk file
                    try:
                        os.remove(chunk_path)
                    except OSError:
                        pass

                    # Move to next chunk (with overlap)
                    start_time += actual_duration - overlap_duration
                    chunk_index += 1

                    # Avoid infinite loop
                    if chunk_index > 1000:  # Safety limit
                        logger.error("Too many chunks, aborting")
                        break

                logger.info(f"Transcribed audio in {chunk_index} chunks: {len(all_segments)} total segments")
                return all_segments

        except Exception as e:
            logger.error(f"Transcription failed for {audio_path}: {str(e)}")
            return None

    def _transcribe_audio_segment(self, audio_path: str, time_offset: float) -> Optional[List[Dict]]:
        """
        Transcribe a single audio segment (internal method)

        Args:
            audio_path: Path to audio file segment
            time_offset: Time offset to add to segment timestamps

        Returns:
            List of segments with adjusted timestamps or None if failed
        """
        if not Path(audio_path).exists():
            logger.error(f"Audio segment not found: {audio_path}")
            return None

        try:
            # First attempt: Transcribe with word timestamps and VAD filter
            segments, info = self.model.transcribe(
                audio_path,
                beam_size=5,
                word_timestamps=True,
                vad_filter=True,  # Voice activity detection
                vad_parameters=dict(min_silence_duration_ms=500)
            )

            # Convert generator to list and format
            transcript_segments = []
            for segment in segments:
                transcript_segments.append({
                    'text': segment.text.strip(),
                    'start': segment.start + time_offset,
                    'end': segment.end + time_offset,
                    'confidence': getattr(segment, 'avg_logprob', 0.0),  # Convert log prob to confidence-ish
                    'words': [
                        {
                            'word': word.word,
                            'start': word.start + time_offset,
                            'end': word.end + time_offset,
                            'probability': word.probability
                        } for word in segment.words
                    ] if hasattr(segment, 'words') and segment.words else []
                })

            # If no segments found with VAD, try without VAD
            if len(transcript_segments) == 0:
                logger.warning(f"No speech segments found with VAD for segment, trying without VAD")
                segments2, info2 = self.model.transcribe(
                    audio_path,
                    beam_size=5,
                    word_timestamps=True,
                    vad_filter=False  # Disable VAD
                )
                transcript_segments = []
                for segment in segments2:
                    transcript_segments.append({
                        'text': segment.text.strip(),
                        'start': segment.start + time_offset,
                        'end': segment.end + time_offset,
                        'confidence': getattr(segment, 'avg_logprob', 0.0),  # Convert log prob to confidence-ish
                        'words': [
                            {
                                'word': word.word,
                                'start': word.start + time_offset,
                                'end': word.end + time_offset,
                                'probability': word.probability
                            } for word in segment.words
                        ] if hasattr(segment, 'words') and segment.words else []
                    })

            return transcript_segments

        except Exception as e:
            logger.error(f"Transcription failed for segment {audio_path}: {str(e)}")
            return None

    def _cleanup_chunks(self, directory: Path, stem: str) -> None:
        """
        Clean up temporary chunk files

        Args:
            directory: Directory containing chunk files
            stem: Stem of the original audio file
        """
        try:
            for chunk_file in directory.glob(f"{stem}_chunk_*.wav"):
                try:
                    chunk_file.unlink()
                except OSError:
                    pass
        except Exception as e:
            logger.warning(f"Error cleaning up chunk files: {str(e)}")

    def extract_audio_from_video(self, video_path: str) -> Optional[str]:
        """
        Extract audio from video file using FFmpeg

        Args:
            video_path: Path to video file

        Returns:
            Path to extracted audio file or None if failed
        """
        import subprocess

        if not Path(video_path).exists():
            logger.error(f"Video file not found: {video_path}")
            return None

        # Create audio file path
        video_stem = Path(video_path).stem
        audio_path = str(Path(video_path).parent / f"{video_stem}_audio.wav")

        try:
            # FFmpeg command to extract audio as 16kHz mono WAV
            cmd = [
                'ffmpeg',
                '-i', video_path,
                '-vn',  # No video
                '-acodec', 'pcm_s16le',  # PCM 16-bit little-endian
                '-ar', '16000',  # 16kHz sample rate
                '-ac', '1',  # Mono
                '-y',  # Overwrite output file
                audio_path
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode != 0:
                logger.error(f"FFmpeg audio extraction failed: {result.stderr}")
                return None

            if not Path(audio_path).exists():
                logger.error(f"Audio file not created: {audio_path}")
                return None

            logger.info(f"Audio extracted to: {audio_path}")
            return audio_path

        except Exception as e:
            logger.error(f"Error extracting audio: {str(e)}")
            return None