"""
Runtime contract, routing, golden evals, and the computer-use MCP layer.
"""
from __future__ import annotations

import json
import os

import pytest
from miloctl.runtime import ModelProfile, Runtime, TaskContract, contract_for
from miloctl import evals

# Module-level singletons: paths are resolved from MILO_HOME at import time.
_STATEFUL = ("miloctl.runtime", "miloctl.evals")


@pytest.fixture
def runtime(milo_home, monkeypatch):
    from miloctl import paths

    state = milo_home / "runtime"
    rt = Runtime(state)
    return rt, state


# ── Runtime contract ──────────────────────────────────────────────────────────


def test_default_profiles_cover_browser_capability(runtime):
    rt, _ = runtime
    assert ModelProfile("x", capabilities=["browser"]).supports("browser")
    capable = [p for p in rt.profiles if p.supports("browser")]
    assert any(p.name == "gpt-5-codex" for p in capable)


def test_contract_for_builds_need_sets():
    c = contract_for("check the dashboard", computer=True, vision=True)
    assert "browser" in c.needs
    assert "vision" in c.needs
    assert c.require_approval is False
    d = contract_for("delete the prod DB dump", destructive=True)
    assert d.risk == "high"
    assert d.require_approval is True


def test_select_respects_preferred_and_capabilities(runtime):
    rt, _ = runtime
    c = TaskContract("a", needs=["text", "tools"])
    prof = rt.select(c, preferred="deepseek-chat")
    assert prof.name == "deepseek-chat"


def test_select_rejects_when_no_model_fits(runtime):
    rt, _ = runtime
    c = TaskContract("teleport those atoms", needs=["text", "tools", "quantum"])
    with pytest.raises(RuntimeError):
        rt.select(c)


def test_finish_writes_event_and_updates_profile(runtime):
    rt, state = runtime
    c = TaskContract("run the suite", needs=["text", "tools"])
    before = next(p for p in rt.profiles if p.name == "deepseek-chat").quality
    rt.finish(c, model="deepseek-chat", success=True, tests_passed=True)
    after = next(p for p in rt.profiles if p.name == "deepseek-chat").quality
    assert after > before
    events = [json.loads(l) for l in (state / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    finished = [e for e in events if e["kind"] == "task_finished"]
    assert finished and finished[-1]["data"]["task"] == "run the suite"


def test_learn_downgrades_tool_score_on_failure(runtime):
    rt, _ = runtime
    c = TaskContract("wreck it", needs=["text", "tools"])
    before = next(p for p in rt.profiles if p.name == "deepseek-chat").tool_score
    rt.finish(c, model="deepseek-chat", success=False)
    after = next(p for p in rt.profiles if p.name == "deepseek-chat").tool_score
    assert after < before


# ── Golden evals ──────────────────────────────────────────────────────────────


def test_run_static_marks_browser_task(runtime):
    rt, state = runtime
    results = evals.run_static("gpt-5-codex", state)
    by_name = {r.name: r for r in results}
    assert by_name["browser_navigation"].passed is True
    assert by_name["repo_inspection"].passed is True


def test_run_static_without_browser_model(runtime):
    rt, state = runtime
    results = evals.run_static("deepseek-chat", state)
    by_name = {r.name: r for r in results}
    assert by_name["browser_navigation"].passed is False
    assert by_name["visual_task"].passed is False


def test_report_counts():
    r = evals.report([
        evals.EvalResult("a", True, 1.0),
        evals.EvalResult("b", False, 0.0),
    ])
    assert r["passed"] == 1 and r["total"] == 2 and r["score"] == 0.5


# ── Computer-use MCP ──────────────────────────────────────────────────────────


def _call(name, args, monkeypatch, tmp_path, approval="tok"):
    import miloctl.computer_mcp as cm
    monkeypatch.setenv("MILO_COMPUTER_APPROVAL", approval)
    return cm.call(name, args)


def test_browser_download_gates_on_approval(monkeypatch, tmp_path):
    import miloctl.computer_mcp as cm
    monkeypatch.setenv("MILO_COMPUTER_APPROVAL", "secret")
    with pytest.raises(PermissionError):
        cm.call("browser_download", {"pattern": "x.pdf"})
    with pytest.raises(PermissionError):
        cm.call("browser_download", {"pattern": "x.pdf", "approval": "wrong"})


def test_browser_download_requires_pattern(monkeypatch, tmp_path):
    import miloctl.computer_mcp as cm
    monkeypatch.setenv("MILO_COMPUTER_APPROVAL", "secret")
    with pytest.raises(ValueError):
        cm.call("browser_download", {"approval": "secret"})


def test_browser_scroll_rejects_bad_direction(monkeypatch, tmp_path):
    import miloctl.computer_mcp as cm
    with pytest.raises(ValueError):
        cm.call("browser_scroll", {"direction": "sideways"})


def test_unknown_tool_raises(monkeypatch, tmp_path):
    import miloctl.computer_mcp as cm
    with pytest.raises(KeyError):
        cm.call("browser_teleport", {})


def test_browser_open_builds_bridge_call(monkeypatch, tmp_path):
    """Without the bridge the call fails loudly — but only after behaving like
    the real command, so an installed opencli routes correctly."""
    import miloctl.computer_mcp as cm
    captured = {}

    def fake_opencli():
        return "opencli"

    def fake_bridge(*parts, timeout=120):
        captured["parts"] = parts
        return {"command": " ".join(parts), "output": "ok"}

    monkeypatch.setattr(cm, "_opencli", fake_opencli)
    monkeypatch.setattr(cm, "_bridge", fake_bridge)
    result = cm.call("browser_open", {"url": "https://x.com"})
    assert captured["parts"] == ("open", "https://x.com")


def test_mcp_handshake_lists_tools(monkeypatch):
    """Drive the real stdio server: initialize, then ask for tools."""
    import io
    import sys
    import contextlib
    import miloctl.computer_mcp as cm

    lines = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n"
    in_buf = io.StringIO(lines)
    out_buf = io.StringIO()
    monkeypatch.setattr(sys, "stdin", in_buf)
    with contextlib.redirect_stdout(out_buf):
        cm.serve()
    resp = json.loads(out_buf.getvalue().strip().splitlines()[0])
    assert resp["result"]["serverInfo"]["name"] == "milo-computer"