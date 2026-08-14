"""Spec merging, caption enforcement and the pre-submit gate.

ffprobe is stubbed so these run with no FFmpeg and no rendered file. That keeps
them honest about their scope: they cover the gate logic, not the render.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('VIDEO_FACTORY_ROOT',
                      tempfile.mkdtemp(prefix='clipper-test-'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import captions  # noqa: E402
from src import validator as validation  # noqa: E402
from src.spec import CampaignSpec  # noqa: E402


def make_spec(**overrides):
    data = {
        'campaign': {'id': 'test', 'name': 'Test'},
        'sources': {'content_folders': ['https://example.com/folder']},
        'render': {'min_duration': 10, 'max_duration': 60,
                   'own_text_required': True, 'platforms': ['youtube']},
        'caption': {'required_keywords': ['#brand']},
    }
    for section, values in overrides.items():
        data.setdefault(section, {}).update(values)
    return CampaignSpec.from_dict(data)


class TestSpecMerge(unittest.TestCase):
    def test_strictest_wins_and_records(self):
        spec = make_spec()
        spec.merge_limit('min_duration', 8)
        self.assertEqual(spec.render.min_duration, 10)
        self.assertTrue(spec.conflicts)

    def test_stricter_replaces(self):
        spec = make_spec()
        spec.merge_limit('min_duration', 15)
        self.assertEqual(spec.render.min_duration, 15)

    def test_hashtags_fold_into_keywords(self):
        spec = CampaignSpec.from_dict({
            'campaign': {'id': 'x'},
            'caption': {'required_hashtags': ['#tag']}})
        self.assertIn('#tag', spec.caption.required_keywords)
        self.assertFalse(spec.caption.required_hashtags)

    def test_logo_folder_implies_required(self):
        spec = CampaignSpec.from_dict({
            'campaign': {'id': 'x'},
            'assets': {'logo_folders': ['https://example.com/logo']}})
        self.assertTrue(spec.assets.logo_required)

    def test_unknown_audience_is_not_a_pass(self):
        spec = make_spec(account_gates={'audience': [
            {'country': 'US', 'operator': '>=', 'percent': 40}]})
        problems = spec.audience_problems({})
        self.assertTrue(problems)
        self.assertTrue(problems[0].startswith('unknown'))

    def test_audience_pass_and_fail(self):
        spec = make_spec(account_gates={'audience': [
            {'country': 'US', 'operator': '>=', 'percent': 40}]})
        self.assertFalse(spec.audience_problems({'US': 55}))
        self.assertTrue(spec.audience_problems({'US': 12}))


class TestCaptionEnforcement(unittest.TestCase):
    def test_missing_token_is_appended(self):
        spec = make_spec()
        text, added = captions.enforce_caption('great clip', spec)
        self.assertIn('#brand', text)
        self.assertEqual(added, ['#brand'])

    def test_present_token_is_not_duplicated(self):
        spec = make_spec()
        text, added = captions.enforce_caption('look #brand go', spec)
        self.assertEqual(text.count('#brand'), 1)
        self.assertFalse(added)

    def test_trim_never_drops_required_tokens(self):
        spec = make_spec(caption={'required_keywords': ['#brand'],
                                  'max_length': 40})
        text, _ = captions.enforce_caption('x' * 200, spec)
        self.assertIn('#brand', text)

    def test_banned_words_are_word_bounded(self):
        spec = make_spec(policy={'banned_topics': ['politics']})
        self.assertTrue(captions.banned_hits('talking politics here', spec))
        self.assertFalse(captions.banned_hits('apolitical stance', spec))


class StubProbe:
    """Stand-in for ffprobe output."""

    def __init__(self, **kwargs):
        self.data = {'duration': 22.0, 'width': 1080, 'height': 1920,
                     'fps': 30.0, 'has_audio': True, 'has_video': True,
                     'vcodec': 'h264', 'acodec': 'aac', 'size': 1024}
        self.data.update(kwargs)

    def __call__(self, path):
        return self.data


class TestValidator(unittest.TestCase):
    def setUp(self):
        self.original = validation.probe_media
        handle, name = tempfile.mkstemp(suffix='.mp4')
        os.close(handle)
        self.file = Path(name)
        self.file.write_bytes(b'non-empty-placeholder')
        self.copy = {'overlay_text': 'WAIT FOR IT',
                     'caption': 'wild moment #brand'}
        self.report = {'sheets': [{'path': 'x.png', 'ink': 5000}],
                       'logo_stamped': False, 'logo_path': ''}

    def tearDown(self):
        validation.probe_media = self.original
        if self.file.exists():
            self.file.unlink()

    def test_compliant_clip_passes(self):
        validation.probe_media = StubProbe()
        result = validation.validate(make_spec(), self.file, self.copy,
                                     self.report)
        self.assertTrue(result['passed'], result['errors'])

    def test_short_clip_blocks_with_no_tolerance(self):
        validation.probe_media = StubProbe(duration=9.97)
        result = validation.validate(make_spec(), self.file, self.copy,
                                     self.report)
        self.assertFalse(result['passed'])
        self.assertTrue(any('below campaign minimum' in item
                            for item in result['errors']))

    def test_landscape_output_blocks(self):
        validation.probe_media = StubProbe(width=1920, height=1080)
        result = validation.validate(make_spec(), self.file, self.copy,
                                     self.report)
        self.assertFalse(result['passed'])

    def test_empty_text_sheet_blocks(self):
        validation.probe_media = StubProbe()
        report = {**self.report, 'sheets': [{'path': 'x.png', 'ink': 0}]}
        result = validation.validate(make_spec(), self.file, self.copy,
                                     report)
        self.assertFalse(result['passed'])

    def test_missing_caption_token_blocks(self):
        validation.probe_media = StubProbe()
        copy = {**self.copy, 'caption': 'no token here'}
        result = validation.validate(make_spec(), self.file, copy,
                                     self.report)
        self.assertFalse(result['passed'])

    def test_missing_in_video_phrase_blocks(self):
        validation.probe_media = StubProbe()
        spec = make_spec(render={'must_appear_in_video': ['Kingdom Clash']})
        result = validation.validate(spec, self.file, self.copy, self.report)
        self.assertFalse(result['passed'])

    def test_in_video_phrase_present_passes(self):
        validation.probe_media = StubProbe()
        spec = make_spec(render={'must_appear_in_video': ['Kingdom Clash']})
        copy = {'overlay_text': 'THIS IS INSANE Kingdom Clash',
                'caption': 'wild #brand'}
        result = validation.validate(spec, self.file, copy, self.report)
        self.assertTrue(result['passed'], result['errors'])

    def test_banned_topic_in_copy_blocks(self):
        validation.probe_media = StubProbe()
        spec = make_spec(policy={'banned_topics': ['gambling']})
        copy = {'overlay_text': 'GAMBLING RUN', 'caption': 'wild #brand'}
        result = validation.validate(spec, self.file, copy, self.report)
        self.assertFalse(result['passed'])

    def test_non_youtube_campaign_blocks_this_pipeline(self):
        validation.probe_media = StubProbe()
        spec = make_spec(render={'platforms': ['tiktok']})
        result = validation.validate(spec, self.file, self.copy, self.report)
        self.assertFalse(result['passed'])

    def test_post_publish_metrics_are_unverifiable_not_failures(self):
        validation.probe_media = StubProbe()
        spec = make_spec(account_gates={'min_engagement_pct': 1.0,
                                        'min_views_for_earnings': 3000})
        result = validation.validate(spec, self.file, self.copy, self.report)
        self.assertTrue(result['passed'], result['errors'])
        self.assertGreaterEqual(len(result['unverifiable']), 2)


class TestPreflight(unittest.TestCase):
    def test_no_sources_blocks(self):
        spec = CampaignSpec.from_dict({'campaign': {'id': 'x'}})
        self.assertFalse(validation.preflight(spec)['ok'])

    def test_logo_required_without_folder_blocks(self):
        spec = CampaignSpec.from_dict({
            'campaign': {'id': 'x'},
            'sources': {'content_folders': ['https://example.com/f']},
            'assets': {'logo_required': True}})
        self.assertFalse(validation.preflight(spec)['ok'])


if __name__ == '__main__':
    unittest.main()
