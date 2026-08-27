#!/usr/bin/env python
"""Teach ``miloctl.harness`` to reuse a warm opencode server.

Two edits to ``OpenCodeHarness``, both idempotent:

1. ``invoke()`` learns ``--auto`` and ``--attach $OPENCODE_SERVER_URL``, so
   routines and ``milo prompt`` hit the already-running ``opencode serve``
   instead of booting MCP servers from cold on every call.
2. ``run_sessioned()`` builds its own argv with the flags *before* the
   positional prompt. It used to append ``--session <id>`` after the message,
   which on current opencode parses as part of the prompt - so the session id
   was ignored and every turn started a brand new session. That is the bug
   behind "the bot opens a new session every time I send a message" on the
   ``milo bot`` path.

Run it from the repo root:

    python scripts/daemons/patch_harness_attach.py            # apply
    python scripts/daemons/patch_harness_attach.py --check    # report only
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TARGET = REPO / "miloctl" / "harness.py"

OLD_INVOKE = '''    def invoke(self, prompt: str, *, model: str = "") -> List[str]:
        argv = ["opencode", "run", "--agent", agent_slug()]
        if model:
            argv += ["--model", model]
        argv.append(prompt)
        return argv
'''

NEW_INVOKE = '''    @staticmethod
    def _server_url() -> str:
        """A running ``opencode serve`` to attach to, if one is configured.

        Attaching skips MCP cold boot per invocation, which is the difference
        between a two second reply and a thirty second one on the VPS.
        """
        return env.get("OPENCODE_SERVER_URL").strip()

    def _base_argv(self, *, model: str = "") -> List[str]:
        argv = ["opencode", "run", "--agent", agent_slug(), "--auto"]
        if model:
            argv += ["--model", model]
        url = self._server_url()
        if url:
            argv += ["--attach", url]
        return argv

    def invoke(self, prompt: str, *, model: str = "") -> List[str]:
        # Flags first, prompt last: opencode treats anything after the
        # positional message as more message.
        return self._base_argv(model=model) + [prompt]
'''

OLD_SESSION = '''        argv = self.invoke(prompt, model=model)
        if not argv:
            return 1, f"{self.label} does not support one-shot invocation", ""
        if session:
            argv += ["--session", session]
        else:
            argv += ["--format", "json"]
'''

NEW_SESSION = '''        argv = self._base_argv(model=model)
        if session:
            argv += ["--session", session]
        else:
            argv += ["--format", "json"]
        argv.append(prompt)
'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="report, do not write")
    args = parser.parse_args()

    if not TARGET.is_file():
        print(f"[fail] {TARGET} not found - run this from the repo")
        return 2
    text = TARGET.read_text(encoding="utf-8")

    if "_base_argv" in text and "--attach" in text:
        print("[ok] harness.py already patched (attach + flag order)")
        return 0

    missing = [name for name, blob in (("invoke", OLD_INVOKE), ("run_sessioned", OLD_SESSION))
               if blob not in text]
    if missing:
        print("[fail] could not find the expected code for: " + ", ".join(missing))
        print("       harness.py has diverged. Apply the two edits by hand:")
        print("       1. OpenCodeHarness.invoke  -> add --auto and --attach $OPENCODE_SERVER_URL")
        print("       2. OpenCodeHarness.run_sessioned -> put --session/--format BEFORE the prompt")
        return 1

    if args.check:
        print("[ok] patch applies cleanly (nothing written, --check)")
        return 0

    backup = TARGET.with_suffix(".py.bak")
    backup.write_text(text, encoding="utf-8")
    patched = text.replace(OLD_INVOKE, NEW_INVOKE).replace(OLD_SESSION, NEW_SESSION)
    TARGET.write_text(patched, encoding="utf-8")
    print(f"[ok] patched {TARGET} (backup at {backup.name})")
    print("     verify: python -c \"import miloctl.harness as h; "
          "print(h.OpenCodeHarness().invoke('hi'))\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
