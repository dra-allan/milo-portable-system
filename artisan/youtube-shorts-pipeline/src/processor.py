import re
from typing import List, Dict, Tuple, Optional
from utils import setup_logger
from config import config

logger = setup_logger(__name__)

class ContentProcessor:
    def __init__(self):
        """Initialize content processor with niche-specific settings"""
        pass

    def score_segment(self, segment: Dict, prev_segment: Optional[Dict] = None,
                     next_segment: Optional[Dict] = None, niche_keywords: List[str] = None) -> float:
        """
        Score a transcript segment for "interestingness"

        Args:
            segment: Dictionary with 'text', 'start', 'end', 'confidence' keys
            prev_segment: Previous segment (for pause detection)
            next_segment: Next segment (for pause detection)
            niche_keywords: List of keywords relevant to the niche

        Returns:
            Score (higher = more interesting)
        """
        if niche_keywords is None:
            niche_keywords = []

        score = 0.0
        text = segment['text'].lower()
        duration = segment['end'] - segment['start']

        # 1. Speech density (words per second) - ideal range 2-4 wps
        words = len(segment['text'].split())
        if duration > 0:
            wps = words / duration
            # Optimal range: 2-4 words per second
            if 2 <= wps <= 4:
                score += wps * 2  # Bonus for good speech rate
            elif wps < 2:
                score += wps  # Penalize too slow
            else:
                score += max(0, 8 - wps)  # Penalize too fast (but cap)

        # 2. Keyword matches (niche-specific)
        keyword_hits = sum(1 for kw in niche_keywords if kw.lower() in text)
        score += keyword_hits * 3

        # 3. Enthusiasm signals (exclamations, questions, caps)
        excited = text.count('!') + text.count('?') * 0.5
        excited += sum(1 for word in segment['text'].split()
                      if len(word) > 2 and word.isupper() and word.isalpha())
        score += excited * 2

        # 4. Pause boundaries (natural breaks)
        if prev_segment:
            pause_gap = segment['start'] - prev_segment['end']
            if 0.3 <= pause_gap <= 2.0:  # Natural breathing pause
                score += 2
            elif pause_gap < 0.3:
                score -= 1  # Too rushed

        if next_segment:
            pause_gap = next_segment['start'] - segment['end']
            if 0.3 <= pause_gap <= 2.0:  # Natural breathing pause
                score += 1

        # 5. Length preference (15-60 seconds ideal for Shorts)
        if 15 <= duration <= 60:
            score += 5
        elif duration < 10:
            score -= 2  # Too short
        elif duration > 90:
            score -= 3  # Too long (might need splitting)

        # 6. Content richness (unique words ratio)
        if words > 0:
            unique_words = len(set(re.findall(r'\b[a-z]+\b', text)))
            richness = unique_words / words
            score += richness * 2  # Bonus for diverse vocabulary

        # 7. Avoid filler words (simple heuristic)
        filler_words = ['um', 'uh', 'like', 'you know', 'so', 'well']
        filler_count = sum(text.count(fw) for fw in filler_words)
        if words > 0:
            filler_ratio = filler_count / words
            score -= filler_ratio * 3  # Penalize fillers

        return max(0, score)  # Ensure non-negative

    def find_highlight_segments(self, transcript: List[Dict],
                               niche_keywords: List[str] = None,
                               min_segment_length: int = 15,
                               max_segment_length: int = 60,
                               min_gap_between: int = 30) -> List[Dict]:
        """
        Find interesting segments in a transcript using sliding window scoring

        Args:
            transcript: List of transcript segments from Whisper
            niche_keywords: Keywords boost score for this niche
            min_segment_length: Minimum clip length in seconds
            max_segment_length: Maximum clip length in seconds
            min_gap_between: Minimum gap between selected clips (seconds)

        Returns:
            List of selected segments with 'start', 'end', 'text', 'score' keys
        """
        if niche_keywords is None:
            niche_keywords = []

        if not transcript or len(transcript) == 0:
            return []

        # Create 5-second sliding windows with 2.5 second step
        window_size = 5.0  # seconds
        step_size = 2.5   # seconds

        # Get video duration from last segment
        video_end = transcript[-1]['end'] if transcript else 0

        # Generate sliding windows
        windows = []
        start_time = 0
        while start_time < video_end:
            end_time = min(start_time + window_size, video_end)

            # Find segments that overlap with this window
            overlapping_segments = [
                seg for seg in transcript
                if not (seg['end'] <= start_time or seg['start'] >= end_time)
            ]

            if overlapping_segments:
                # Combine text from overlapping segments
                combined_text = ' '.join([seg['text'] for seg in overlapping_segments])

                # Calculate average confidence
                avg_confidence = sum([seg.get('confidence', 0) for seg in overlapping_segments]) / len(overlapping_segments)

                # Create window segment
                window_segment = {
                    'text': combined_text,
                    'start': start_time,
                    'end': end_time,
                    'confidence': avg_confidence
                }

                # Score this window (need previous and next windows for pause detection)
                prev_window = windows[-1] if windows else None
                # Next window unknown yet, will be set in next iteration

                score = self.score_segment(
                    window_segment,
                    prev_window,
                    None,  # Next window unknown yet
                    niche_keywords
                )

                window_segment['score'] = score
                windows.append(window_segment)

            start_time += step_size

        # Now go back and set proper next_window references for scoring
        for i, window in enumerate(windows):
            prev_window = windows[i-1] if i > 0 else None
            next_window = windows[i+1] if i < len(windows)-1 else None

            window['score'] = self.score_segment(
                window,
                prev_window,
                next_window,
                niche_keywords
            )

        # Sort windows by score (descending)
        windows.sort(key=lambda x: x['score'], reverse=True)

        # Select non-overlapping windows using greedy algorithm
        selected_windows = []
        for window in windows:
            # Check if this window overlaps with any already selected window
            overlaps = False
            for selected in selected_windows:
                # Check for overlap considering minimum gap
                # Two windows are too close if:
                # window starts before selected ends + min_gap AND selected starts before window ends + min_gap
                if window['start'] < selected['end'] + min_gap_between and \
                   selected['start'] < window['end'] + min_gap_between:
                    overlaps = True
                    break

            # Also check length constraints
            duration = window['end'] - window['start']
            if min_segment_length <= duration <= max_segment_length and not overlaps:
                selected_windows.append({
                    'start': window['start'],
                    'end': window['end'],
                    'text': window['text'],
                    'score': window['score']
                })

        # Sort selected windows by start time
        selected_windows.sort(key=lambda x: x['start'])

        logger.info(f"Found {len(selected_windows)} highlight segments from {len(windows)} windows")
        return selected_windows

    def merge_close_segments(self, segments: List[Dict], max_gap: float = 5.0) -> List[Dict]:
        """
        Merge segments that are close together to create longer clips

        Args:
            segments: List of segments with 'start', 'end' keys
            max_gap: Maximum gap between segments to merge (seconds)

        Returns:
            List of merged segments
        """
        if not segments:
            return []

        # Sort by start time
        segments.sort(key=lambda x: x['start'])

        merged = []
        current = segments[0].copy()

        for next_seg in segments[1:]:
            # If gap is small enough, merge
            if next_seg['start'] - current['end'] <= max_gap:
                # Extend current segment
                current['end'] = next_seg['end']
                current['text'] += ' ' + next_seg['text']
                # Average the scores
                current['score'] = (current['score'] + next_seg['score']) / 2
            else:
                # No overlap, add current and move to next
                merged.append(current)
                current = next_seg.copy()

        # Don't forget the last segment
        merged.append(current)

        return merged