#!/usr/bin/env python
"""Test script to validate highlight detection with sample data"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from src.processor import ContentProcessor

def test_highlight_detection_basic():
    """Test basic highlight detection with clear highlights"""
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
    # Use appropriate parameters for short test video
    highlights = processor.find_highlight_segments(
        transcript,
        niche_keywords=keywords,
        min_segment_length=5,      # 5 seconds minimum
        max_segment_length=10,     # 10 seconds maximum
        min_gap_between=5          # 5 seconds gap between highlights
    )

    # Should find highlights in the exciting middle section
    assert len(highlights) > 0, "Should detect at least one highlight"
    assert all('start' in h and 'end' in h and 'score' in h and 'text' in h for h in highlights), "Each highlight should have required fields"

    # Check that highlights are in chronological order
    for i in range(len(highlights) - 1):
        assert highlights[i]['start'] <= highlights[i+1]['start'], "Highlights should be sorted by start time"

    print("+ Basic highlight detection test passed: found {} highlights".format(len(highlights)))
    return highlights

def test_highlight_detection_no_highlights():
    """Test highlight detection with boring content"""
    processor = ContentProcessor()

    # Sample transcript with no exciting content
    transcript = [
        {'text': 'hello nice day today', 'start': 0.0, 'end': 3.0, 'confidence': 0.9},
        {'text': 'i went to the store and bought some milk', 'start': 3.0, 'end': 6.0, 'confidence': 0.9},
        {'text': 'then i came home and watched some television', 'start': 6.0, 'end': 9.0, 'confidence': 0.9},
        {'text': 'nothing exciting happened today really', 'start': 9.0, 'end': 12.0, 'confidence': 0.8},
    ]

    keywords = ['amazing', 'incredible', 'unbelievable']
    highlights = processor.find_highlight_segments(transcript, niche_keywords=keywords)

    # May find some segments due to other scoring factors, but should handle gracefully
    assert isinstance(highlights, list), "Should return a list"
    assert all('start' in h and 'end' in h and 'score' in h for h in highlights), "Each highlight should have required fields"

    print("+ No highlights test passed: found {} highlights (expected low count)".format(len(highlights)))
    return highlights

def test_highlight_detection_timing_constraints():
    """Test that highlights respect timing constraints"""
    processor = ContentProcessor()

    # Create a longer transcript to test timing
    transcript = []
    for i in range(20):
        transcript.append({
            'text': f'This is segment number {i} with some exciting content wow amazing',
            'start': float(i * 2),
            'end': float((i + 1) * 2),
            'confidence': 0.9
        })

    keywords = ['exciting', 'wow', 'amazing']
    highlights = processor.find_highlight_segments(
        transcript,
        niche_keywords=keywords,
        min_segment_length=5,    # 5 seconds minimum
        max_segment_length=15,   # 15 seconds maximum
        min_gap_between=5        # 5 seconds gap between highlights
    )

    # Check that all highlights respect duration constraints
    for highlight in highlights:
        duration = highlight['end'] - highlight['start']
        assert 5 <= duration <= 15, "Highlight duration {}s not in range [5,15]".format(duration)

    # Check that highlights respect minimum gap
    for i in range(len(highlights) - 1):
        gap = highlights[i+1]['start'] - highlights[i]['end']
        assert gap >= 5, "Gap between highlights {}s less than minimum 5s".format(gap)

    print("+ Timing constraints test passed: found {} highlights respecting constraints".format(len(highlights)))
    return highlights

def test_highlight_scoring_components():
    """Test that different scoring components work correctly"""
    processor = ContentProcessor()

    # Test speech density scoring
    segment_fast = {
        'text': ' '.join(['word'] * 50),  # 50 words
        'start': 0.0,
        'end': 5.0,  # 10 words/second - too fast
        'confidence': 0.9
    }

    segment_slow = {
        'text': 'hello world',  # 2 words
        'start': 0.0,
        'end': 5.0,  # 0.4 words/second - too slow
        'confidence': 0.9
    }

    segment_ideal = {
        'text': ' '.join(['word'] * 15),  # 15 words
        'start': 0.0,
        'end': 5.0,  # 3 words/second - ideal
        'confidence': 0.9
    }

    score_fast = processor.score_segment(segment_fast)
    score_slow = processor.score_segment(segment_slow)
    score_ideal = processor.score_segment(segment_ideal)

    # Ideal speech rate should score higher than too fast or too slow
    assert score_ideal > score_fast, "Ideal speech rate ({}) should score higher than too fast ({})".format(score_ideal, score_fast)
    assert score_ideal > score_slow, "Ideal speech rate ({}) should score higher than too slow ({})".format(score_ideal, score_slow)

    print("+ Speech density scoring: fast={:.2f}, slow={:.2f}, ideal={:.2f}".format(score_fast, score_slow, score_ideal))

    # Test keyword scoring
    segment_no_keywords = {
        'text': 'the quick brown fox jumps over the lazy dog',
        'start': 0.0,
        'end': 5.0,
        'confidence': 0.9
    }

    segment_with_keywords = {
        'text': 'amazing incredible unbelievable clutch play',
        'start': 0.0,
        'end': 5.0,
        'confidence': 0.9
    }

    keywords = ['amazing', 'incredible', 'unbelievable', 'clutch']
    score_no_kw = processor.score_segment(segment_no_keywords, niche_keywords=keywords)
    score_with_kw = processor.score_segment(segment_with_keywords, niche_keywords=keywords)

    assert score_with_kw > score_no_kw, "Segment with keywords ({}) should score higher than without ({})".format(score_with_kw, score_no_kw)

    print("+ Keyword scoring: no keywords={:.2f}, with keywords={:.2f}".format(score_no_kw, score_with_kw))

    # Test enthusiasm scoring
    segment_boring = {
        'text': 'this is a normal sentence with no excitement',
        'start': 0.0,
        'end': 5.0,
        'confidence': 0.9
    }

    segment_excited = {
        'text': 'THIS IS AMAZING!!! INCREDIBLE!!! WOW!!!',
        'start': 0.0,
        'end': 5.0,
        'confidence': 0.9
    }

    score_boring = processor.score_segment(segment_boring)
    score_excited = processor.score_segment(segment_excited)

    assert score_excited > score_boring, "Excited segment ({}) should score higher than boring ({})".format(score_excited, score_boring)

    print("+ Enthusiasm scoring: boring={:.2f}, excited={:.2f}".format(score_boring, score_excited))

    print("+ Scoring components test passed")
    return True

def test_edge_cases():
    """Test edge cases"""
    processor = ContentProcessor()

    # Empty transcript
    assert processor.find_highlight_segments([]) == [], "Empty transcript should return empty list"

    # Single segment
    single_segment = [{'text': 'hello world', 'start': 0.0, 'end': 5.0, 'confidence': 0.9}]
    result = processor.find_highlight_segments(single_segment)
    assert isinstance(result, list), "Single segment should return a list"

    # Segment with zero duration
    zero_duration = [{'text': 'hello', 'start': 0.0, 'end': 0.0, 'confidence': 0.9}]
    result = processor.find_highlight_segments(zero_duration)
    assert isinstance(result, list), "Zero duration segment should return a list"

    print("+ Edge cases test passed")
    return True

def test_merge_close_segments():
    """Test merging of close segments"""
    processor = ContentProcessor()

    # Segments that should be merged (close together)
    segments_close = [
        {'start': 0.0, 'end': 5.0, 'text': 'First segment', 'score': 0.8},
        {'start': 5.5, 'end': 10.0, 'text': 'Second segment', 'score': 0.7},  # 0.5 gap
        {'start': 11.0, 'end': 15.0, 'text': 'Third segment', 'score': 0.9},  # 1.0 gap
    ]

    # Merge with max gap of 1.0 seconds
    merged = processor.merge_close_segments(segments_close, max_gap=1.0)

    # Should merge first two (0.5 < 1.0 and 1.0 <= 1.0), leave decision on third
    # Actually: 0.5 < 1.0 -> merge first two
    # Then: 11.0 - 10.0 = 1.0 <= 1.0 -> merge result with third
    # So all three should merge into one
    assert len(merged) == 1, "Expected 1 segment after merging with max_gap=1.0, got {}".format(len(merged))
    assert merged[0]['start'] == 0.0, "Merged segment should start at 0"
    assert merged[0]['end'] == 15.0, "Merged segment should end at 15"

    # Segments that should NOT be merged (far apart)
    segments_far = [
        {'start': 0.0, 'end': 5.0, 'text': 'First segment', 'score': 0.8},
        {'start': 20.0, 'end': 25.0, 'text': 'Second segment', 'score': 0.7},  # 15 second gap
    ]

    merged_far = processor.merge_close_segments(segments_far, max_gap=1.0)
    assert len(merged_far) == 2, "Expected 2 segments after merging with max_gap=1.0 (far apart), got {}".format(len(merged_far))

    print("+ Merge close segments test passed")
    return True

def test_integration_scenario():
    """Test a realistic scenario with mixed content"""
    processor = ContentProcessor()

    # Simulate a 60-second video with various moments
    transcript = []

    # Boring intro (0-10s)
    for i in range(0, 10, 2):
        transcript.append({
            'text': f'welcome to our channel today we will be showing you something',
            'start': float(i),
            'end': float(i + 2),
            'confidence': 0.8
        })

    # Exciting moment (10-20s) - should get high score
    for i in range(10, 20, 2):
        transcript.append({
            'text': f'oh my god that was incredible amazing unbelievable play wow',
            'start': float(i),
            'end': float(i + 2),
            'confidence': 0.95
        })

    # Boring middle (20-40s)
    for i in range(20, 40, 2):
        transcript.append({
            'text': f'and now we will continue with our regular programming nothing special here',
            'start': float(i),
            'end': float(i + 2),
            'confidence': 0.8
        })

    # Another exciting moment (40-50s) - should get high score
    for i in range(40, 50, 2):
        transcript.append({
            'text': f'incredible clutch move insane play amazing reaction wow',
            'start': float(i),
            'end': float(i + 2),
            'confidence': 0.9
        })

    # Boring outro (50-60s)
    for i in range(50, 60, 2):
        transcript.append({
            'text': f'thanks for watching please subscribe and see you next time',
            'start': float(i),
            'end': float(i + 2),
            'confidence': 0.8
        })

    keywords = ['incredible', 'amazing', 'unbelievable', 'wow', 'clutch', 'insane']
    highlights = processor.find_highlight_segments(
        transcript,
        niche_keywords=keywords,
        min_segment_length=5,
        max_segment_length=30,
        min_gap_between=5
    )

    # Should find highlights in the exciting regions (10-20s and 40-50s)
    assert len(highlights) >= 1, "Should find at least one highlight in exciting regions"

    # Check that highlights are in the expected time ranges
    highlight_times = [(h['start'], h['end']) for h in highlights]

    # At least one should overlap with exciting regions
    exciting_regions = [(10, 20), (40, 50)]
    found_in_exciting = False
    for h_start, h_end in highlight_times:
        for e_start, e_end in exciting_regions:
            # Check for overlap
            if not (h_end <= e_start or h_start >= e_end):
                found_in_exciting = True
                break
        if found_in_exciting:
            break

    assert found_in_exciting, "No highlights found in exciting regions {}. Found hits at: {}".format(exciting_regions, highlight_times)

    print("+ Integration scenario test passed: found {} highlights in expected regions".format(len(highlights)))
    return highlights

def run_all_tests():
    """Run all highlight detection tests"""
    print("Running highlight detection tests...\n")

    try:
        test_highlight_detection_basic()
        test_highlight_detection_no_highlights()
        test_highlight_detection_timing_constraints()
        test_highlight_scoring_components()
        test_edge_cases()
        test_merge_close_segments()
        test_integration_scenario()

        print("\n+ All highlight detection tests passed!")
        return True

    except Exception as e:
        print("\nX Test failed: {}".format(e))
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)