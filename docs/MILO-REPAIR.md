# Milo repair contract

This branch establishes the behavior layer Milo was missing while copying the Hermes-Agent ideas.

## What is now defined

- `miloctl.runtime.Runtime` owns model profiles, capability matching, run events, and automatic post-run learning.
- `miloctl.computer_mcp` exposes browser navigation, snapshots, screenshots, clicks, typing, scrolling, watching, and approval-gated downloads over MCP.
- `miloctl.evals` provides a small golden-task matrix so a model can be rejected for poor tool use instead of being selected because its name sounds impressive.

## Automatic learning loop

Every completed run should call `Runtime.finish(...)`. Milo updates the model's observed quality, records an event, and keeps the evidence in `~/.milo/runtime/events.jsonl`. Skills should be generated only after a successful, repeatable task, then linted and indexed. This avoids the Hermes-style failure where self-learning exists in code but is never invoked.

## Computer use

Start a Chromium instance with remote debugging enabled, set `MILO_CDP_URL`, and register `python -m miloctl.computer_mcp` as an MCP server. Keep downloads and other irreversible actions behind `MILO_COMPUTER_APPROVAL`; extend the same approval gate to desktop control before adding mouse/keyboard automation outside the browser.

## Next wiring pass

1. Register the computer MCP server in the shared harness configuration.
2. Add `Runtime` lifecycle hooks to `milo run`, routines, and Telegram tasks.
3. Replace static model flags with profile selection and eval thresholds.
4. Add real Playwright tests and provider-backed coding benchmark tasks.
5. Remove all plaintext secrets and rotate anything previously committed.
