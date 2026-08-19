"""The 2026-08-19 burst, as a test.

five of the six clips flick_shorts published that day came from one source video
(uUAH82U_jXU) and six of capital_mindset's came from another (yveLqk3DCNs).
Both runs were inside the 6/channel/day cap and both broke the 3/source/day
cadence rule, because the drain loop only consulted the channel budget.

These tests exercise the selection directly -- no database, no Google -- so the
cap is pinned by something cheaper than noticing it on YouTube the next morning.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from safe_upload import select_uploads  # noqa: E402


def _rows(source: str, count: int, niche: str = 'money'):
    return [{'source_video_id': source, 'segment_index': i, 'niche': niche,
             'local_path': f'/tmp/{source}_{i}.mp4', 'title': f'clip {i}'}
            for i in range(1, count + 1)]


def _one_channel(_row):
    return 'flick_shorts'


def test_single_source_cannot_exceed_its_daily_cap():
    """Six clips from one source, 6 channel budget, 3 source budget -> 3."""
    rows = _rows('uUAH82U_jXU', 6)
    plan, skips = select_uploads(rows, {'flick_shorts': 6},
                                {'uUAH82U_jXU': 3}, _one_channel)
    assert len(plan) == 3, 'the per-source cap has to bind before the channel cap'
    assert skips['source_cap'] == 3
    assert skips['channel_cap'] == 0


def test_channel_budget_is_still_the_outer_limit():
    rows = _rows('a', 4) + _rows('b', 4) + _rows('c', 4)
    plan, skips = select_uploads(rows, {'flick_shorts': 5},
                                {'a': 3, 'b': 3, 'c': 3}, _one_channel)
    assert len(plan) == 5
    assert skips['channel_cap'] > 0


def test_sources_are_interleaved_not_drained_one_at_a_time():
    """A per-source cap alone still yields 'AAA BBB'. Interleaving is the point.

    Three clips from A back to back is the same identical-source burst the
    cadence rule exists to prevent, even when the count is legal.
    """
    rows = _rows('a', 3) + _rows('b', 3)
    plan, _ = select_uploads(rows, {'flick_shorts': 6},
                            {'a': 3, 'b': 3}, _one_channel)
    order = [row['source_video_id'] for _, row in plan]
    assert len(plan) == 6
    assert order[0] != order[1], f'expected alternating sources, got {order}'


def test_a_source_already_at_cap_today_is_skipped_entirely():
    """Budgets come from what was published earlier today, across all sweeps.

    The 9AM/2PM/7PM sweeps each see a fresh queue, so a source that already
    spent its three slots at 9AM must contribute nothing at 2PM.
    """
    rows = _rows('spent', 4) + _rows('fresh', 2)
    plan, skips = select_uploads(rows, {'flick_shorts': 6},
                                {'spent': 0, 'fresh': 2}, _one_channel)
    chosen = {row['source_video_id'] for _, row in plan}
    assert chosen == {'fresh'}
    assert len(plan) == 2
    assert skips['source_cap'] == 1  # one probe per exhausted source, then dropped


def test_multiple_channels_keep_independent_budgets():
    rows = ([{**r, 'niche': 'money'} for r in _rows('uUAH82U_jXU', 6)]
            + [{**r, 'niche': 'wealth'} for r in _rows('yveLqk3DCNs', 6)])

    def channel_of(row):
        return {'money': 'flick_shorts', 'wealth': 'capital_mindset'}[row['niche']]

    plan, _ = select_uploads(
        rows, {'flick_shorts': 6, 'capital_mindset': 6},
        {'uUAH82U_jXU': 3, 'yveLqk3DCNs': 3}, channel_of)
    per_channel = {}
    for channel, _row in plan:
        per_channel[channel] = per_channel.get(channel, 0) + 1
    # This is the exact 8/19 scenario: it used to produce 6 and 6.
    assert per_channel == {'flick_shorts': 3, 'capital_mindset': 3}


def test_rows_without_a_channel_binding_are_counted_not_uploaded():
    plan, skips = select_uploads(_rows('a', 2), {}, {'a': 3}, lambda _r: '')
    assert plan == []
    assert skips['no_channel'] == 2


def test_caller_budgets_are_not_mutated():
    """The caller prints these as the STARTING budget after planning."""
    channel_budgets = {'flick_shorts': 6}
    source_budgets = {'a': 3}
    select_uploads(_rows('a', 6), channel_budgets, source_budgets, _one_channel)
    assert channel_budgets == {'flick_shorts': 6}
    assert source_budgets == {'a': 3}
