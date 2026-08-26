"""Unit pins for reframe.static_scene_graph — the layout render dispatch.

The layout modules' own suites cover their geometry/filtergraphs upstream;
these pin the wiring decisions that are new in this package:

  * every non-TRACK strategy resolves to SOME graph (never None), so a lost
    payload degrades to GENERAL instead of a tracked crop;
  * WIDE keeps the source's full width (side-cropping disabled is its whole
    point);
  * SCREENCAST stacks content over speaker, SPLIT stacks two speakers, PANEL
    tiles 3-4 people into a 2x2 grid.
"""
import reframe


def _graph(strategy, start_f=0, **payload):
    return reframe.static_scene_graph(
        strategy, start_f, 1920, 1080, 1080, 1920, **payload)


class TestGeneral:
    def test_general_is_the_blurred_background_layout(self):
        graph = _graph('GENERAL')
        assert "gblur" in graph
        assert "overlay=x=(W-w)/2:y=(H-h)/2" in graph

    def test_unknown_strategy_degrades_to_general(self):
        # A strategy name from a newer engine must never crash a render.
        assert _graph('SOMETHING_NEW') == _graph('GENERAL')


class TestWide:
    def test_wide_keeps_full_source_width(self):
        # 16:9 content at 1080 wide is 608 tall — anything less means the
        # sides were cropped, which WIDE exists to stop.
        graph = _graph('WIDE')
        assert "scale=-2:608" in graph

    def test_wide_is_not_a_plain_general(self):
        assert _graph('WIDE') != _graph('GENERAL')


class TestSplit:
    def test_payload_renders_the_stack(self):
        pair = ((600, 400, 200, 200), (1400, 400, 200, 200))
        graph = _graph('SPLIT', splits={0: pair})
        assert "vstack=inputs=2" in graph
        assert "pad=1080:1920" in graph

    def test_missing_payload_degrades_to_general_not_track(self):
        # TRACK would crop one speaker out of frame entirely — exactly what
        # SPLIT was chosen to avoid.
        assert _graph('SPLIT', start_f=7) == _graph('GENERAL')


class TestPanel:
    def test_payload_tiles_into_a_grid(self):
        centres = [(300, 400, 200, 200), (900, 400, 200, 200),
                   (1500, 400, 200, 200)]
        graph = _graph('PANEL', panels={0: centres})
        assert "hstack=inputs=2" in graph
        assert "vstack=inputs=2" in graph

    def test_missing_payload_degrades_to_general(self):
        assert _graph('PANEL', start_f=3) == _graph('GENERAL')


class TestScreencast:
    def test_payload_stacks_content_over_speaker(self):
        graph = _graph('SCREENCAST', screencasts={0: (1748, 832)})
        assert "vstack=inputs=2" in graph
        # The content band is scaled, never cropped.
        content = graph.split("[ca]")[-1].split("[content]")[0]
        assert "crop" not in content

    def test_missing_payload_degrades_to_general(self):
        assert _graph('SCREENCAST', screencasts={}, start_f=5) == \
            _graph('GENERAL')


class TestInset:
    def test_missing_box_degrades_to_general(self):
        assert _graph('INSET', inset=None) == _graph('GENERAL')

    def test_box_renders_an_inset_graph(self):
        box = (1200, 40, 680, 382)
        graph = _graph('INSET', inset=box)
        assert "setsar=1[v]" in graph
