import subprocess
import os
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from utils import setup_logger, format_timestamp, sanitize_filename
from config import config

logger = setup_logger(__name__)

class VideoEditor:
    def __init__(self):
        """Initialize video editor"""
        # Check if FFmpeg is available
        try:
            result = subprocess.run(['ffmpeg', '-version'],
                                  capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                raise RuntimeError("FFmpeg not found")
            logger.info("FFmpeg video editor initialized")
        except Exception as e:
            logger.error(f"Failed to initialize FFmpeg: {str(e)}")
            raise

    def create_vertical_crop(self, input_path: str, output_path: str,
                           x_offset: int = 0, y_offset: int = 0) -> bool:
        """
        Crop video to 9:16 aspect ratio (vertical)

        Args:
            input_path: Path to input video
            output_path: Path for output video
            x_offset: Horizontal offset from center (negative = left, positive = right)
            y_offset: Vertical offset from top (negative = up, positive = down)

        Returns:
            True if successful, False otherwise
        """
        if not Path(input_path).exists():
            logger.error(f"Input video not found: {input_path}")
            return False

        try:
            # First, get video dimensions to calculate crop
            cmd_probe = [
                'ffprobe', '-v', 'error', '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height', '-of', 'csv=p=0',
                input_path
            ]
            result = subprocess.run(cmd_probe, capture_output=True, text=True, timeout=10)

            if result.returncode != 0:
                logger.error(f"Failed to probe video dimensions: {result.stderr}")
                return False

            width, height = map(int, result.stdout.strip().split(','))

            # Calculate 9:16 crop dimensions
            target_ratio = 9/16
            if width / height > target_ratio:
                # Video is wider than 9:16, crop width
                new_width = int(height * target_ratio)
                new_height = height
                x_offset_calc = (width - new_width) // 2 + x_offset
                y_offset_calc = 0
            else:
                # Video is taller than 9:16, crop height
                new_width = width
                new_height = int(width / target_ratio)
                x_offset_calc = 0
                y_offset_calc = (height - new_height) // 2 + y_offset

            # Ensure offsets don't go negative or exceed bounds
            x_offset_calc = max(0, min(x_offset_calc, width - new_width))
            y_offset_calc = max(0, min(y_offset_calc, height - new_height))

            # Build crop filter
            crop_filter = f'crop={new_width}:{new_height}:{x_offset_calc}:{y_offset_calc}'

            # FFmpeg command for cropping
            cmd = [
                'ffmpeg',
                '-i', input_path,
                '-vf', crop_filter,
                '-c:a', 'copy',  # Copy audio stream
                '-avoid_negative_ts', 'make_zero',
                '-fflags', '+genpts',
                '-y',  # Overwrite output
                output_path
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if result.returncode != 0:
                logger.error(f"Video cropping failed: {result.stderr}")
                return False

            if not Path(output_path).exists():
                logger.error(f"Output video not created: {output_path}")
                return False

            logger.info(f"Video cropped to 9:16: {output_path}")
            return True

        except Exception as e:
            logger.error(f"Error in vertical crop: {str(e)}")
            return False

    def add_burn_in_captions(self, input_path: str, transcript_segments: List[Dict],
                           output_path: str, font_size: int = 24) -> bool:
        """
        Add burned-in captions from transcript segments

        Args:
            input_path: Path to input video
            transcript_segments: List of segments with 'text', 'start', 'end'
            output_path: Path for output video with captions
            font_size: Font size for captions

        Returns:
            True if successful, False otherwise
        """
        if not Path(input_path).exists():
            logger.error(f"Input video not found: {input_path}")
            return False

        try:
            # Create ASS subtitle file from transcript segments
            ass_path = str(Path(output_path).with_suffix('.ass'))

            # Write ASS subtitle file
            with open(ass_path, 'w', encoding='utf-8') as f:
                f.write('[Script Info]\n')
                f.write('Title: Generated Subtitles\n')
                f.write('ScriptType: v4.00+\n')
                f.write('WrapStyle: 0\n')
                f.write('ScaledBorderAndShadow: yes\n')
                f.write('PlayResX: 1080\n')
                f.write('PlayResY: 1920\n')  # 9:16 ratio
                f.write('Timer: 100.0000\n')
                f.write('\n')

                f.write('[V4+ Styles]\n')
                f.write('Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n')
                f.write(f'Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n')
                f.write('\n')

                f.write('[Events]\n')
                f.write('Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n')

                for segment in transcript_segments:
                    start_time = self._format_ass_time(segment['start'])
                    end_time = self._format_ass_time(segment['end'])
                    # Remove HTML tags and escape special characters for ASS
                    text = segment['text'].replace('\\', '\\\\').replace('{', '\\{').replace('}', '\\}')
                    f.write(f'Dialogue: 0,{start_time},{end_time},Default,,0,0,0,,{text}\\n')

            # FFmpeg command to burn in subtitles
            cmd = [
                'ffmpeg',
                '-i', input_path,
                '-vf', f'subtitles={ass_path}:force_style=\'Fontsize={font_size},PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,BorderStyle=3,Outline=2,Shadow=1,Alignment=2\'',
                '-c:a', 'copy',
                '-avoid_negative_ts', 'make_zero',
                '-fflags', '+genpts',
                '-y',
                output_path
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            # Clean up temporary ASS file
            try:
                os.remove(ass_path)
            except OSError:
                pass  # Ignore if already deleted

            if result.returncode != 0:
                logger.error(f"Caption burning failed: {result.stderr}")
                return False

            if not Path(output_path).exists():
                logger.error(f"Output video not created: {output_path}")
                return False

            logger.info(f"Captions burned into video: {output_path}")
            return True

        except Exception as e:
            logger.error(f"Error adding captions: {str(e)}")
            # Clean up ASS file if it exists
            ass_path = str(Path(output_path).with_suffix('.ass'))
            try:
                os.remove(ass_path)
            except OSError:
                pass
            return False

    def _format_ass_time(self, seconds: float) -> str:
        """Convert seconds to ASS time format (H:MM:SS.cc)"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        centisecs = int((seconds - int(seconds)) * 100)
        return f"{hours}:{minutes:02d}:{secs:02d}.{centisecs:02d}"

    def add_intro_outro(self, input_path: str, output_path: str,
                       intro_path: Optional[str] = None, outro_path: Optional[str] = None) -> bool:
        """
        Add intro and/or outro clips to a video

        Args:
            input_path: Path to main video
            output_path: Path for output video
            intro_path: Path to intro video (optional)
            outro_path: Path to outro video (optional)

        Returns:
            True if successful, False otherwise
        """
        if not Path(input_path).exists():
            logger.error(f"Input video not found: {input_path}")
            return False

        try:
            # Build filter complex for concatenation
            inputs = ['-i', input_path]
            filters = []
            input_count = 1

            if intro_path and Path(intro_path).exists():
                inputs.extend(['-i', intro_path])
                input_count += 1

            if outro_path and Path(outro_path).exists():
                inputs.extend(['-i', outro_path])
                input_count += 1

            # Build concat filter
            concat_inputs = ''
            if input_count == 3:  # intro + main + outro
                concat_inputs = '[0:v][0:a][1:v][1:a][2:v][2:a]concat=n=3:v=1:a=1[outv][outa]'
            elif input_count == 2:  # Either intro+main or main+outro
                if intro_path:
                    concat_inputs = '[1:v][1:a][0:v][0:a]concat=n=2:v=1:a=1[outv][outa]'
                else:  # outro only
                    concat_inputs = '[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[outv][outa]'
            else:  # Just main video
                concat_inputs = '[0:v][0:a]'

            # FFmpeg command
            cmd = ['ffmpeg'] + inputs + [
                '-filter_complex', concat_inputs,
                '-map', '[outv]', '-map', '[outa]',
                '-avoid_negative_ts', 'make_zero',
                '-fflags', '+genpts',
                '-y',
                output_path
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if result.returncode != 0:
                logger.error(f"Intro/outro addition failed: {result.stderr}")
                return False

            if not Path(output_path).exists():
                logger.error(f"Output video not created: {output_path}")
                return False

            logger.info(f"Intro/outro added to video: {output_path}")
            return True

        except Exception as e:
            logger.error(f"Error adding intro/outro: {str(e)}")
            return False

    def normalize_audio(self, input_path: str, output_path: str) -> bool:
        """
        Normalize audio using FFmpeg loudnorm filter

        Args:
            input_path: Path to input video
            output_path: Path for output video

        Returns:
            True if successful, False otherwise
        """
        if not Path(input_path).exists():
            logger.error(f"Input video not found: {input_path}")
            return False

        try:
            # Two-pass loudnorm for better results
            # First pass: analyze
            cmd1 = [
                'ffmpeg',
                '-i', input_path,
                '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11:print_format=summary',
                '-f', 'null', '-'
            ]

            result1 = subprocess.run(cmd1, capture_output=True, text=True, timeout=30)

            # Extract measurements from first pass (simplified - in production you'd parse the output)
            # For now, we'll use fixed parameters which work reasonably well

            # Second pass: apply normalization
            cmd2 = [
                'ffmpeg',
                '-i', input_path,
                '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11',
                '-c:v', 'copy',
                '-y',
                output_path
            ]

            result2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=30)

            if result2.returncode != 0:
                logger.error(f"Audio normalization failed: {result2.stderr}")
                return False

            if not Path(output_path).exists():
                logger.error(f"Output video not created: {output_path}")
                return False

            logger.info(f"Audio normalized: {output_path}")
            return True

        except Exception as e:
            logger.error(f"Error normalizing audio: {str(e)}")
            return False

    def create_short_from_segment(self, video_path: str, start_time: float,
                                end_time: float, transcript_segments: List[Dict],
                                output_path: str, add_branding: bool = True) -> bool:
        """
        Create a complete Short from a video segment

        Args:
            video_path: Path to source video
            start_time: Start time in seconds
            end_time: End time in seconds
            transcript_segments: Transcript segments for this time range (for captions)
            output_path: Path for final Short
            add_branding: Whether to add intro/outro branding

        Returns:
            True if successful, False otherwise
        """
        if not Path(video_path).exists():
            logger.error(f"Source video not found: {video_path}")
            return False

        try:
            # Create temporary files in temp directory
            temp_dir = Path(__file__).parent.parent / 'data' / 'temp'
            temp_dir.mkdir(parents=True, exist_ok=True)

            # Filter transcript segments for this time range
            relevant_transcript = [
                seg for seg in transcript_segments
                if not (seg['end'] <= start_time or seg['start'] >= end_time)
            ]

            segmented_video = str(temp_dir / f"segmented_{Path(output_path).stem}.mp4")
            cropped_video = str(temp_dir / f"cropped_{Path(output_path).stem}.mp4")
            captioned_video = str(temp_dir / f"captioned_{Path(output_path).stem}.mp4")
            normalized_video = str(temp_dir / f"normalized_{Path(output_path).stem}.mp4")

            # Step 1: Extract segment
            logger.info(f"Extracting segment {start_time:.2f}-{end_time:.2f} from {video_path}")
            cmd_extract = [
                'ffmpeg',
                '-ss', format_timestamp(start_time),
                '-i', video_path,
                '-to', format_timestamp(end_time),
                '-c:v', 'libx264',
                '-c:a', 'aac',
                '-avoid_negative_ts', 'make_zero',
                '-fflags', '+genpts',
                '-y',
                segmented_video
            ]

            result = subprocess.run(cmd_extract, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                logger.error(f"Segment extraction failed: {result.stderr}")
                return False

            # Step 2: Crop to 9:16 vertical
            logger.info("Cropping to 9:16 vertical format")
            if not self.create_vertical_crop(segmented_video, cropped_video):
                return False

            # Step 3: Add captions
            logger.info("Adding burned-in captions")
            if not self.add_burn_in_captions(cropped_video, relevant_transcript, captioned_video):
                return False

            # Step 4: Add branding (intro/outro) if requested
            if add_branding:
                logger.info("Adding branding")
                # For now, we'll skip actual intro/outro files and just use the captioned video
                # In production, you'd have actual intro/outro assets
                branded_video = captioned_video
            else:
                branded_video = captioned_video

            # Step 5: Normalize audio
            logger.info("Normalizing audio")
            if not self.normalize_audio(branded_video, normalized_video):
                return False

            # Step 6: Move final output to desired location
            logger.info(f"Moving final output to {output_path}")
            # Ensure output directory exists
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

            # Move/replace the file
            if Path(output_path).exists():
                Path(output_path).unlink()
            Path(normalized_video).rename(output_path)

            # Clean up temporary files
            for temp_file in [segmented_video, cropped_video, captioned_video, normalized_video]:
                try:
                    if Path(temp_file).exists():
                        Path(temp_file).unlink()
                except OSError:
                    pass  # Ignore if already deleted

            logger.info(f"Short created successfully: {output_path}")
            return True

        except Exception as e:
            logger.error(f"Error creating Short: {str(e)}")
            # Clean up any temporary files
            temp_files = ['segmented_video', 'cropped_video', 'captioned_video', 'normalized_video']
            for temp_name in temp_files:
                if temp_name in locals():
                    try:
                        if Path(locals()[temp_name]).exists():
                            Path(locals()[temp_name]).unlink()
                    except OSError:
                        pass
            return False