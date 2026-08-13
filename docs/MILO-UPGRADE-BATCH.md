# Milo upgrade batch

This branch adds the durable runtime layer without forcing a heavyweight daemon on every install.

- `miloctl.daemon`: one authenticated loopback service for status and resumable jobs.
- `miloctl.ipc`: cross-platform JSON-lines IPC with bounded payloads and constant-time token checks.
- `miloctl.agents`: isolated Git worktrees, atomic job state, and restart recovery.
- `miloctl.secrets`: authenticated local secret envelope with 0600 permissions and no committed values.
- `miloctl.dashboard`: read-only loopback Mission Control view.
- `.github/workflows/cross-platform.yml`: Windows and Ubuntu CI across supported Python versions.

Run the daemon with `python -m miloctl.daemon`. Keep it bound to loopback. The dashboard is intentionally read-only; mutations go through authenticated IPC. Voice remains optional and existing local providers continue to work as fallbacks.
