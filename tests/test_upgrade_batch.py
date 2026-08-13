from pathlib import Path
import json
import pytest
from miloctl import ipc, secrets
from miloctl.agents import AgentWorkspace


def test_ipc_rejects_wrong_token():
    with pytest.raises(PermissionError):
        ipc.serve_request(b'{"token":"bad","method":"ping"}\n', "good")


def test_secret_vault_round_trip(tmp_path):
    path = tmp_path / "secrets.json"
    secrets.save(path, {"TELEGRAM_BOT_TOKEN": "redacted-value"}, "a-long-local-passphrase")
    assert secrets.load(path, "a-long-local-passphrase")["TELEGRAM_BOT_TOKEN"] == "redacted-value"
    with pytest.raises(ValueError):
        secrets.load(path, "wrong-passphrase")


def test_agent_recovery_marks_missing_worktree(tmp_path):
    ws = AgentWorkspace(tmp_path / "repo")
    ws.root.mkdir(parents=True, exist_ok=True)
    ws.state.write_text(json.dumps({"job": {"status": "running", "worktree": str(tmp_path / "gone")}}))
    assert ws.recover()["recovered"] == 1
