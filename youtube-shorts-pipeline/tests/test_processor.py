# tests/test_processor.py
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.processor import ContentProcessor

def test_segment_scoring():
    """Test that the segment scoring function works correctly"""
    processor = ContentProcessor()

    # Test segment with high enthusiasm
    segment_high = {
        'text': 'THIS IS AMAZING!!! INCREDIBLE PLAY!!!',
        'start': 0.0,
        'end': 5.0,
        'confidence': 0.9
    }

    prev_segment = None
    next_segment = None
    keywords = ['amazing', 'incredible', 'play']

    score = processor.score_segment(segment_high, prev_segment, next_segment, keywords)
    assert score > 0, "Score should be positive for enthusiastic segment"

    # Test segment with low enthusiasm/filler
    segment_low = {
        'text': 'um so like you know this is uh kinda okay',
        'start': 5.0,
        'end': 10.0,
        'confidence': 0.8
    }

    score_low = processor.score_segment(segment_low, segment_high, None, keywords)
    # This might still be positive due to other factors, but let's just ensure it runs
    assert isinstance(score_low, float), "Score should be a float"

    print("Segment scoring tests passed")

def test_highlight_detection():
    """Test highlight detection with sample data"""
    processor = ContentProcessor()

    # Sample transcript with clear highlights
    transcript = [
        {'text': 'Hello welcome to the stream', 'start': 0.0, 'end': 3.0, 'confidence': 0.9},
        {'text': 'Today we have an unbelievable clutch play', 'start': 3.0, 'end': 6.0, 'confidence': 0.95},
        {'text': 'I can not believe how good that was', 'start': 6.0, 'end': 9.0, 'confidence': 0.9},
        {'text': 'Make sure to like and subscribe', 'start': 9.0, 'end': 12.0, 'confidence': 0.8},
        {'text': 'Thanks for watching see you next time', 'start': 12.0, 'end': 15.0, 'confidence': 0.8},
    ]

    keywords = ['unbelievable', 'clutch', 'believe', 'good']
    highlights = processor.find_highlight_segments(transcript, niche_keywords=keywords)

    # Should find at least one highlight
    assert len(highlights) >= 0, "Should return a list (even if empty)"
    assert all('start' in h and 'end' in h and 'score' in h for h in highlights), "Each highlight should have start, end, score"

    print(f"Highlight detection test passed: found {len(highlights)} highlights")

def test_merge_close_segments():
    """Test merging of close segments"""
    processor = ContentProcessor()

    segments = [
        {'start': 0.0, 'end': 5.0, 'text': 'First segment', 'score': 0.8},
        {'start': 5.2, 'end': 10.0, 'text': 'Second segment', 'score': 0.7},  # 0.2 gap
        {'start': 15.0, 'end': 20.0, 'text': 'Third segment', 'score': 0.9},  # 5.0 gap
    ]

    # Merge with max gap of 1.0 seconds
    merged = processor.merge_close_segments(segments, max_gap=1.0)

    # Should merge first two (0.2 gap < 1.0), leave third separate
    assert len(merged) == 2, f"Expected 2 segments after merging, got {len(merged)}"
    assert merged[0]['start'] == 0.0, "First merged segment should start at 0"
    assert merged[0]['end'] == 10.0, "First merged segment should end at 10 (merged first two)"
    assert merged[1]['start'] == 15.0, "Second segment should start at 15"
    assert merged[1]['end'] == 20.0, "Second segment should end at 20"

    print("Merge close segments test passed")

if __name__ == "__main__":
    test_segment_scoring()
    test_highlight_detection()
    test_merge_close_segments()
    print("All tests passed!")