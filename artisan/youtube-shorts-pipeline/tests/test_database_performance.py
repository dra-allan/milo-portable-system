# tests/test_database_performance.py
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.database import PipelineDatabase, SCHEMA

def make_db():
    tmp = tempfile.mkdtemp()
    return PipelineDatabase(os.path.join(tmp, 'test.db'))

def seed_short(db, source_id, segment_index, youtube_short_id):
    db.record_short(
        source_video_id=source_id,
        segment_index=segment_index,
        start_time=10.0,
        end_time=40.0,
        title='Test clip',
        local_path='',
        score=1.5,
        youtube_short_id=youtube_short_id,
    )

def test_performance_table_exists():
    """The short_performance table must be created by the schema."""
    db = make_db()
    with db._connect() as conn:
        tables = [r['name'] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
    assert 'short_performance' in tables

def test_record_and_report_performance():
    """Recording metrics then reporting must return the clip joined with stats."""
    db = make_db()
    seed_short(db, 'src1', 1, 'yid1')
    seed_short(db, 'src1', 2, 'yid2')

    db.record_performance('yid1', 'src1', 1, views=1000, likes=50, comments=5, favorites=2)
    db.record_performance('yid2', 'src1', 2, views=2000, likes=100, comments=10, favorites=4)

    report = db.performance_report(limit=10)
    assert len(report) == 2
    # Ordered by views DESC: yid2 (2000) first
    assert report[0]['youtube_short_id'] == 'yid2'
    assert report[0]['views'] == 2000
    assert report[0]['source_video_id'] == 'src1'
    assert report[0]['segment_index'] == 2
    assert report[0]['title'] == 'Test clip'

def test_record_performance_upserts():
    """Recording the same short twice must update, not duplicate."""
    db = make_db()
    seed_short(db, 'src1', 1, 'yid1')

    db.record_performance('yid1', 'src1', 1, views=100, likes=10, comments=1, favorites=0)
    db.record_performance('yid1', 'src1', 1, views=300, likes=30, comments=3, favorites=0)

    summary = db.performance_summary()
    assert summary['tracked'] == 1
    assert summary['total_views'] == 300
    report = db.performance_report()
    assert report[0]['views'] == 300

def test_shorts_needing_stats():
    """A short with a YouTube ID but no metrics row must be returned."""
    db = make_db()
    seed_short(db, 'src1', 1, 'yid1')          # uploaded, no stats yet
    db.record_short(
        source_video_id='src2', segment_index=1,
        start_time=0.0, end_time=20.0, title='Local only',
        youtube_short_id=None,
    )                                            # never uploaded: skip

    pending = db.shorts_needing_stats(limit=50, max_age_hours=24)
    ids = {p['youtube_short_id'] for p in pending}
    assert ids == {'yid1'}

def test_shorts_needing_stats_skips_fresh():
    """A short fetched within the max age must not be refetched."""
    db = make_db()
    seed_short(db, 'src1', 1, 'yid1')
    db.record_performance('yid1', 'src1', 1, views=10, likes=1, comments=0, favorites=0)

    pending = db.shorts_needing_stats(limit=50, max_age_hours=24)
    assert pending == []

def test_performance_summary():
    db = make_db()
    seed_short(db, 'src1', 1, 'yid1')
    seed_short(db, 'src1', 2, 'yid2')
    db.record_performance('yid1', 'src1', 1, views=500, likes=5, comments=1, favorites=0)
    db.record_performance('yid2', 'src1', 2, views=1500, likes=15, comments=2, favorites=0)

    summary = db.performance_summary()
    assert summary['tracked'] == 2
    assert summary['with_views'] == 2
    assert summary['total_views'] == 2000
    assert summary['avg_views'] == 1000.0

if __name__ == '__main__':
    test_performance_table_exists()
    test_record_and_report_performance()
    test_record_performance_upserts()
    test_shorts_needing_stats()
    test_shorts_needing_stats_skips_fresh()
    test_performance_summary()
    print("All performance DB tests passed!")
