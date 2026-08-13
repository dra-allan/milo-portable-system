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


def _gate(project):
    ok, report = script_gate(project)
    return ok, report


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


def _table(segments, role="BODY"):
    rows = []
    for i in range(segments):
        rows.append(f"NAR-{i+3:03d} | {role} | YES | YES | 1:15 | [NAR-{i+3:03d}] {NARR}")
    return rows


def test_gate_passes_inline_format(tmp_path):
    _write(tmp_path, _inline(90))  # 1800 words
    assert _gate(tmp_path)[0] is True


def test_gate_passes_marker_on_own_line(tmp_path):
    _write(tmp_path, _marker_line(90))
    assert _gate(tmp_path)[0] is True


def test_gate_passes_table_format(tmp_path):
    _write(tmp_path, _table(90))  # 1800 words in SUMMARY column
    assert _gate(tmp_path)[0] is True


def test_gate_fails_under_budget(tmp_path):
    _write(tmp_path, _inline(50))  # 1000 words < 1620
    assert _gate(tmp_path)[0] is False


def test_gate_fails_over_budget(tmp_path):
    _write(tmp_path, _inline(140))  # 2800 words > 2025
    assert _gate(tmp_path)[0] is False


def test_gate_ignores_table_metadata_and_headers(tmp_path):
    rows = []
    for i in range(40):
        rows.append(f"NAR-{i+3:03d} | BODY | YES | YES | 1:15 | [NAR-{i+3:03d}] {NARR}")
    rows.append("HEADER-001 | HEADER | NO | NO | 0:05 | ACT ONE THE GAUGE")
    rows.append("NAR-000 | TRANSITION | NO | YES | 0:15 | The tide turns")
    _write(tmp_path, rows)  # 800 narration words < 1620
    ok, report = _gate(tmp_path)
    assert ok is False
    assert "800" in report  # metadata words (NAR/BODY/YES/1:15) never counted


def test_both_formats_count_identically(tmp_path):
    a = pathlib.Path(tmp_path) / "a"
    b = pathlib.Path(tmp_path) / "b"
    a.mkdir()
    b.mkdir()
    _write(a, _inline(70))
    _write(b, _marker_line(70))
    assert _gate(a)[0] == _gate(b)[0]


def test_report_carries_guidance(tmp_path):
    _write(tmp_path, _inline(50))  # under budget
    ok, report = _gate(tmp_path)
    assert ok is False
    assert "SHORT" in report
    assert "1620" in report