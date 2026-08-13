"""SCRIPT GATE regression tests - wordcount handles both script marker formats.

Two formats are produced by different models and both must count the same way:
  A) marker on its own line, text follows:   [NAR-003]\nSalt bites your knuckles...
  B) marker inline with the text:            [NAR-003] Salt bites your knuckles...

Regression: format B used to count 0 words (the whole line was skipped), which
gate-failed every Nemotron script with a useless "wordcount 0" report.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]
                       / "artisan" / "pov_pipeline"))
from run_pov_pipeline import script_gate  # noqa: E402

NARR = ("The cold bites hard against the glass while the water climbs and the "
        "lantern holds steady through the storm outside. ")
NARR_WORDS = 20


def _write(project, segments, claim="1620"):
    head = ("=== SEGMENT MANIFEST ===\nVIDEO_ID: POV-2026-T\n"
            "TARGET_RUNTIME: 12-15min\nTARGET_WORDCOUNT: 1620-2025\n"
            "=== END MANIFEST ===\n")
    (project / "01_SCRIPT_RAW.txt").write_text(
        f"# WORDCOUNT: {claim}\n{head}\n" + "\n".join(segments), encoding="utf-8")


def _inline(segments):
    return [f"[NAR-{i:03d}] {NARR}" for i in range(segments)]


def _marker_line(segments):
    out = []
    for i in range(segments):
        out.append(f"[NAR-{i:03d}]")
        out.append(NARR.rstrip())
    return out


def test_gate_passes_inline_format(tmp_path):
    _write(tmp_path, _inline(90))  # 1800 words
    assert script_gate(tmp_path) is True


def test_gate_passes_marker_on_own_line(tmp_path):
    _write(tmp_path, _marker_line(90))
    assert script_gate(tmp_path) is True


def test_gate_fails_under_budget(tmp_path):
    _write(tmp_path, _inline(50))  # 1000 words < 1620
    assert script_gate(tmp_path) is False


def test_gate_fails_over_budget(tmp_path):
    _write(tmp_path, _inline(140))  # 2800 words > 2025
    assert script_gate(tmp_path) is False


def test_both_formats_count_identically(tmp_path):
    a = pathlib.Path(tmp_path) / "a"
    b = pathlib.Path(tmp_path) / "b"
    a.mkdir()
    b.mkdir()
    _write(a, _inline(70))
    _write(b, _marker_line(70))
    assert script_gate(a) == script_gate(b)