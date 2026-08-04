"""
harness.py — make Milo the same agent in every tool.
====================================================

A *harness* is whatever program actually runs the model: OpenCode, Claude
Code, Codex, Cursor, Gemini CLI, or a bare API script. Each one wants the
persona in a slightly different place, in a slightly different format.

Milo's answer is to keep exactly one source of truth (:mod:`miloctl.persona`)
and give every harness an adapter that:

``detect()``   is this tool installed on this machine?
``sync()``     write the persona + MCP + slash commands where the tool expects
``invoke()``   build the argv to run a one-shot prompt through it
``status()``   what does this look like right now?

Adding a new tool means adding one small subclass — never touching the
persona, the memory or the skills.

OpenCode is primary because that is what Allan runs, but nothing above the
adapter layer knows or cares.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import env, paths, persona
from .naming import agent_name as agent_slug
from .naming import display_name

__all__ = [
    "Harness",
    "OpenCodeHarness",
    "ClaudeCodeHarness",
    "CodexHarness",
    "CursorHarness",
    "GeminiHarness",
    "GenericHarness",
    "all_harnesses",
    "get_harness",
    "detect_installed",
    "sync_all",
    "mcp_servers",
]


# ── Slash commands shared by every harness that supports them ─────────────────

SLASH_COMMANDS: Dict[str, Tuple[str, str]] = {
    "remember": (
        "Save something durable to Milo's memory",
        "Save the following to durable memory using `milo remember`.\n"
        "Pick a sensible --category (fact/decision/preference/procedure/note),\n"
        "add --tags, and set --importance 4 or 5 if it changes future behaviour.\n"
        "Then confirm in one line what you saved.\n\n$ARGUMENTS",
    ),
    "recall": (
        "Search everything Milo knows",
        "Search Milo's memory, past sessions and the vault for the query below,\n"
        "then answer from what you find. Run all three:\n"
        "  milo recall \"$ARGUMENTS\"\n"
        "  milo sessions search \"$ARGUMENTS\"\n"
        "  milo vault search \"$ARGUMENTS\"\n"
        "Synthesise one answer. Say plainly if nothing relevant came back.",
    ),
    "learn": (
        "Turn what we just did into a reusable skill",
        "Run `milo learn --prompt \"$ARGUMENTS\"` to get the authoring brief,\n"
        "then follow it: write the SKILL.md yourself with your existing tools,\n"
        "save it under the skills directory, and run `milo skills lint <name>`.\n"
        "If $ARGUMENTS is empty, learn from what we did in this session.",
    ),
    "handoff": (
        "Write the session handoff before you run out of context",
        "Summarise this session for the next one: what changed, what works,\n"
        "what is still open, and the exact next step. Save the durable parts\n"
        "with `milo remember`, then write the summary to the vault with\n"
        "`milo vault handoff`. Be specific — file paths, command names, no vagueness.",
    ),
    "milo": (
        "Reload Milo's full context",
        "Run `milo persona show` and adopt everything it returns as your\n"
        "operating context for the rest of this session. Then give a two-line\n"
        "status: what you now know, and what you think we're working on.",
    ),
}

#: Both spellings resolve to the same command file.
SLASH_ALIASES = {"mylo": "milo"}


# ── MCP servers ───────────────────────────────────────────────────────────────


def mcp_servers(secrets: Optional[Dict[str, str]] = None) -> Dict[str, dict]:
    """MCP server definitions, populated from ``.env``.

    Only servers whose credentials are actually present get emitted — a config
    full of half-configured servers is how a harness ends up erroring on every
    startup. Milo's own memory server is always included; it needs no keys.
    """
    s = secrets if secrets is not None else env.load()
    py = "python3" if not paths.IS_WINDOWS else "python"
    out: Dict[str, dict] = {
        # Milo's own brain, exposed as MCP tools. Always available.
        agent_slug() + "-memory": {
            "type": "local",
            "command": [py, "-m", "miloctl.mcp"],
            "enabled": True,
            "environment": {"MILO_HOME": str(paths.milo_home())},
        }
    }

    if s.get("GITHUB_PAT"):
        out["github"] = {
            "type": "local",
            "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
            "enabled": True,
            "environment": {"GITHUB_PERSONAL_ACCESS_TOKEN": "{{GITHUB_PAT}}"},
        }
    if s.get("GOOGLE_CLIENT_ID") and s.get("GOOGLE_CLIENT_SECRET"):
        out["google-workspace"] = {
            "type": "local",
            "command": ["npx", "-y", "@modelcontextprotocol/server-gdrive"],
            "enabled": True,
            "environment": {
                "GOOGLE_CLIENT_ID": "{{GOOGLE_CLIENT_ID}}",
                "GOOGLE_CLIENT_SECRET": "{{GOOGLE_CLIENT_SECRET}}",
                "GOOGLE_REFRESH_TOKEN": "{{GOOGLE_REFRESH_TOKEN}}",
            },
        }
    if s.get("BRAVE_API_KEY"):
        out["web-search"] = {
            "type": "local",
            "command": ["npx", "-y", "@modelcontextprotocol/server-brave-search"],
            "enabled": True,
            "environment": {"BRAVE_API_KEY": "{{BRAVE_API_KEY}}"},
        }
    # Filesystem access to the vault, scoped — no key needed.
    if paths.vault_dir().is_dir():
        out["vault"] = {
            "type": "local",
            "command": [
                "npx", "-y", "@modelcontextprotocol/server-filesystem",
                str(paths.vault_dir()),
            ],
            "enabled": True,
        }
    return out


# ── Base ──────────────────────────────────────────────────────────────────────


@dataclass
class SyncResult:
    harness: str
    written: List[Path] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def render(self) -> str:
        if self.error:
            return f"  {self.harness:<14} error: {self.error}"
        files = ", ".join(p.name for p in self.written) or "nothing"
        return f"  {self.harness:<14} {files}"


class Harness:
    """Base adapter. Subclasses override the class attrs and ``sync``."""

    name: str = "generic"
    label: str = "Generic"
    #: Executables that indicate this harness is installed.
    binaries: Tuple[str, ...] = ()
    #: Filename the tool reads its persona from.
    persona_filename: str = "AGENTS.md"
    #: Extension for exported agents/commands. Cursor insists on .mdc.
    export_suffix: str = ".md"

    # -- detection -------------------------------------------------------------

    @classmethod
    def which(cls) -> Optional[str]:
        for b in cls.binaries:
            found = shutil.which(b)
            if found:
                return found
        return None

    @classmethod
    def detect(cls) -> bool:
        """Installed if the binary is on PATH, or we have already synced here.

        Deliberately *not* "the config dir exists" — several of these dirs get
        created as a side effect of other tooling, and a false positive means
        writing config for a program that isn't installed.
        """
        if cls.which() is not None:
            return True
        return (cls.config_dir() / cls.persona_filename).is_file()

    @classmethod
    def config_dir(cls) -> Path:
        return paths.milo_home() / "harness" / cls.name

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _write(path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.rstrip() + "\n", encoding="utf-8")
        return path

    @staticmethod
    def _write_json(path: Path, data: dict, merge: bool = True) -> Path:
        """Write JSON, merging into an existing file rather than clobbering it.

        Users hand-edit these configs. Blowing away their settings on every
        `milo sync` is the fastest way to make a tool untrustworthy.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if merge and path.is_file():
            try:
                raw = path.read_text(encoding="utf-8")
                existing = json.loads(_strip_jsonc(raw)) or {}
            except (OSError, json.JSONDecodeError):
                existing = {}
        merged = _deep_merge(existing, data)
        path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        return path

    def _slash_dir(self) -> Optional[Path]:
        return None

    def _write_slash_commands(self, out: List[Path]) -> None:
        d = self._slash_dir()
        if d is None:
            return
        for cmd, (desc, body) in SLASH_COMMANDS.items():
            text = f"---\ndescription: {desc}\n---\n\n{body}\n"
            out.append(self._write(d / f"{cmd}.md", text))
        for alias, target in SLASH_ALIASES.items():
            desc, body = SLASH_COMMANDS[target]
            out.append(
                self._write(
                    d / f"{alias}.md",
                    f"---\ndescription: {desc} (alias of /{target})\n---\n\n{body}\n",
                )
            )

    # -- pack export -----------------------------------------------------------
    # An enabled pack agent should be a real subagent in the tool the user is
    # actually running, and a pack command a real slash command. Leaving them as
    # index entries only would mean importing `agency-agents` gave you 270 things
    # the model can read about and none it can delegate to.

    def _agent_dir(self) -> Optional[Path]:
        """Where this tool looks for extra subagents. None = no such concept."""
        return None

    def _agent_frontmatter(self, name: str, desc: str,
                           meta: Dict[str, object]) -> Dict[str, object]:
        """Frontmatter keys for a subagent. Tools disagree; subclasses adjust."""
        return {"name": name, "description": desc}

    def _command_frontmatter(self, name: str, desc: str,
                             meta: Dict[str, object]) -> Dict[str, object]:
        return {"description": desc}

    @staticmethod
    def _fm(data: Dict[str, object]) -> str:
        lines = ["---"]
        for k, v in data.items():
            if v in ("", None, [], {}):
                continue
            if isinstance(v, list):
                v = ", ".join(str(x) for x in v)
            # Descriptions routinely contain a colon, which turns a scalar into
            # a broken mapping and makes the tool ignore the whole file.
            s = str(v).replace("\n", " ").strip()
            if any(c in s for c in ':#"') and not s.startswith('"'):
                s = '"' + s.replace('"', "'") + '"'
            lines.append(f"{k}: {s}")
        return "\n".join(lines) + "\n---\n"

    def _export_packs(self, out: List[Path]) -> None:
        """Write enabled pack agents/commands in this tool's native format.

        Also removes what a previous sync wrote and this one did not, so that
        `milo packs disable x` actually makes `x` disappear from the tool. The
        list of previously written files is tracked explicitly rather than by
        globbing the directory: these folders hold the user's own agents too,
        and deleting one of those would be unforgivable.
        """
        from . import packs

        agent_dir, slash_dir = self._agent_dir(), self._slash_dir()
        if agent_dir is None and slash_dir is None:
            return

        reserved = set(SLASH_COMMANDS) | set(SLASH_ALIASES) | {agent_slug(), "mylo"}
        written: List[str] = []
        try:
            items = packs.exportable(kinds=("agent", "command"))
        except Exception:                       # a broken registry must not
            items = []                          # take the whole sync down

        for item in items:
            to_agent = item.kind == "agent" and agent_dir is not None
            target_dir = agent_dir if to_agent else slash_dir
            if target_dir is None:
                continue
            # Never shadow Milo's own commands or persona agent — a pack called
            # `learn` would otherwise silently replace /learn.
            fname = f"{item.pack}-{item.name}" if item.name in reserved else item.name
            fm = (self._agent_frontmatter if to_agent else self._command_frontmatter)(
                fname, item.description, item.meta
            )
            text = self._fm(fm) + "\n" + item.body.strip() + "\n"
            try:
                p = self._write(target_dir / f"{fname}{self.export_suffix}", text)
            except OSError:
                continue
            out.append(p)
            written.append(str(p))

        self._reap_exports(written)

    def _exports_manifest(self) -> Path:
        return paths.milo_home() / "harness" / "exports.json"

    def _reap_exports(self, written: List[str]) -> None:
        """Delete files a previous export wrote that this one no longer wants."""
        mf = self._exports_manifest()
        try:
            state = json.loads(mf.read_text(encoding="utf-8")) if mf.is_file() else {}
        except (OSError, json.JSONDecodeError):
            state = {}
        for old in state.get(self.name, []):
            if old not in written:
                try:
                    Path(old).unlink()
                except OSError:
                    pass
        state[self.name] = sorted(written)
        try:
            mf.parent.mkdir(parents=True, exist_ok=True)
            mf.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass

    # -- api -------------------------------------------------------------------

    def sync(self, ctx: Optional[persona.PersonaContext] = None) -> SyncResult:
        ctx = ctx or persona.build()
        res = SyncResult(self.name)
        try:
            res.written.append(
                self._write(self.config_dir() / self.persona_filename, ctx.render())
            )
            # Any harness that declares a slash directory should get the
            # commands in it. Only OpenCode and Claude Code called this, so
            # Codex advertised a prompts dir it never filled.
            self._write_slash_commands(res.written)
            self._export_packs(res.written)
        except OSError as exc:
            res.error = str(exc)
        return res

    def invoke(self, prompt: str, *, model: str = "") -> List[str]:
        """argv for a one-shot prompt. Empty list = not runnable."""
        return []

    def run(self, prompt: str, *, model: str = "", timeout: int = 900) -> Tuple[int, str]:
        argv = self.invoke(prompt, model=model)
        if not argv:
            return 1, f"{self.label} does not support one-shot invocation"
        # Windows: CreateProcess only finds .exe/.bat on PATH, never .CMD. The
        # npm/pip shims live as *.CMD, so resolve the first binary to its full
        # path when subprocess would otherwise miss it.
        resolved = self.which()
        if resolved and argv and not Path(argv[0]).is_absolute():
            argv = [resolved, *argv[1:]]
        try:
            p = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout,
                cwd=str(paths.workspace_dir()) if paths.workspace_dir().is_dir() else None,
            )
        except FileNotFoundError:
            return 127, f"{self.binaries[0] if self.binaries else self.name} not found on PATH"
        except subprocess.TimeoutExpired:
            return 124, f"{self.label} timed out after {timeout}s"
        return p.returncode, (p.stdout or p.stderr).strip()

    def status(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "label": self.label,
            "installed": self.detect(),
            "binary": self.which() or "",
            "config_dir": str(self.config_dir()),
            "synced": (self.config_dir() / self.persona_filename).is_file(),
        }


# ── JSON helpers ──────────────────────────────────────────────────────────────


def _strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments so JSONC parses with the stdlib."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    text = re.sub(r"(?<![:\"\w])//[^\n\"]*$", "", text, flags=re.M)
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ── OpenCode (primary) ────────────────────────────────────────────────────────


class OpenCodeHarness(Harness):
    name = "opencode"
    label = "OpenCode"
    binaries = ("opencode",)
    persona_filename = "AGENTS.md"

    @classmethod
    def config_dir(cls) -> Path:
        return paths.opencode_config_dir()

    def _slash_dir(self) -> Optional[Path]:
        return self.config_dir() / "command"

    def _agent_dir(self) -> Optional[Path]:
        return self.config_dir() / "agent"

    def _agent_frontmatter(self, name, desc, meta):
        # OpenCode keys the agent off the filename and needs an explicit mode;
        # without `subagent` it would be offered as a top-level persona and
        # compete with Milo itself.
        fm = {"description": desc, "mode": "subagent"}
        if meta.get("model"):
            fm["model"] = meta["model"]
        return fm

    def sync(self, ctx: Optional[persona.PersonaContext] = None) -> SyncResult:
        ctx = ctx or persona.build()
        res = SyncResult(self.name)
        slug = agent_slug()
        try:
            cfg_dir = self.config_dir()
            # 1. Persona, read automatically by OpenCode.
            res.written.append(self._write(cfg_dir / "AGENTS.md", ctx.render()))

            # 2. Named agent profiles — `opencode run --agent milo`, and the
            #    same content under `mylo` so either spelling works.
            agent_body = (
                f"---\ndescription: {display_name()} — Allan's assistant and "
                f"chief of stuff\nmode: primary\n---\n\n{ctx.render()}"
            )
            for alias in (slug, "mylo" if slug == "milo" else slug):
                res.written.append(
                    self._write(cfg_dir / "agent" / f"{alias}.md", agent_body)
                )

            # 3. MCP + defaults, merged into any existing config.
            secrets = env.load()
            config = {
                "$schema": "https://opencode.ai/config.json",
                "mcp": mcp_servers(secrets),
                "agent": {slug: {"mode": "primary"}},
            }
            model = secrets.get("MILO_MODEL", "").strip()
            if model:
                config["model"] = model
            rendered = env.render(json.dumps(config, indent=2), secrets)
            target = cfg_dir / "opencode.json"
            merged = _deep_merge(
                json.loads(_strip_jsonc(target.read_text(encoding="utf-8")))
                if target.is_file() else {},
                json.loads(rendered),
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
            res.written.append(target)

            # 4. Slash commands, then anything enabled from a pack.
            self._write_slash_commands(res.written)
            self._export_packs(res.written)
        except (OSError, json.JSONDecodeError) as exc:
            res.error = str(exc)
        return res

    def invoke(self, prompt: str, *, model: str = "") -> List[str]:
        argv = ["opencode", "run", "--agent", agent_slug()]
        if model:
            argv += ["--model", model]
        argv.append(prompt)
        return argv


# ── Claude Code ───────────────────────────────────────────────────────────────


class ClaudeCodeHarness(Harness):
    name = "claude-code"
    label = "Claude Code"
    binaries = ("claude",)
    persona_filename = "CLAUDE.md"

    @classmethod
    def config_dir(cls) -> Path:
        return paths.claude_config_dir()

    def _slash_dir(self) -> Optional[Path]:
        return self.config_dir() / "commands"

    def _agent_dir(self) -> Optional[Path]:
        return self.config_dir() / "agents"

    def _agent_frontmatter(self, name, desc, meta):
        fm = {"name": name, "description": desc}
        # Claude Code honours a tool allowlist on subagents. Most pack authors
        # wrote these for Claude Code, so the value is already in the right
        # vocabulary — passing it through preserves their intent.
        for key in ("tools", "model"):
            if meta.get(key):
                fm[key] = meta[key]
        return fm

    def sync(self, ctx: Optional[persona.PersonaContext] = None) -> SyncResult:
        ctx = ctx or persona.build()
        res = SyncResult(self.name)
        try:
            cfg = self.config_dir()
            res.written.append(self._write(cfg / "CLAUDE.md", ctx.render()))

            # Claude Code reads MCP servers from ~/.claude.json (mcpServers).
            secrets = env.load()
            servers = {
                k: {
                    "command": v["command"][0],
                    "args": v["command"][1:],
                    **({"env": v["environment"]} if v.get("environment") else {}),
                }
                for k, v in mcp_servers(secrets).items()
            }
            rendered = json.loads(env.render(json.dumps({"mcpServers": servers}), secrets))
            res.written.append(self._write_json(cfg.parent / ".claude.json", rendered))

            # SessionStart hook: inject fresh boot context (memory, handoff,
            # priorities, recent sessions) so Claude boots the way OpenCode
            # does — with today's state, not a static persona snapshot.
            res.written.append(
                self._write_json(
                    cfg / "settings.json",
                    {
                        "hooks": {
                            "SessionStart": [
                                {
                                    "matcher": "",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "milo context --hook",
                                        }
                                    ],
                                }
                            ]
                        }
                    },
                )
            )

            # Subagent definition so `@milo` works inside Claude Code.
            for alias in (agent_slug(), "mylo"):
                res.written.append(
                    self._write(
                        cfg / "agents" / f"{alias}.md",
                        f"---\nname: {alias}\ndescription: {display_name()} — Allan's "
                        f"assistant and chief of stuff. Use for anything personal, "
                        f"cross-project, or memory-related.\n---\n\n{ctx.render()}",
                    )
                )
            self._write_slash_commands(res.written)
            self._export_packs(res.written)
        except (OSError, json.JSONDecodeError) as exc:
            res.error = str(exc)
        return res

    def invoke(self, prompt: str, *, model: str = "") -> List[str]:
        argv = ["claude", "-p", prompt]
        if model:
            argv += ["--model", model]
        return argv


# ── Codex ─────────────────────────────────────────────────────────────────────


class CodexHarness(Harness):
    name = "codex"
    label = "OpenAI Codex CLI"
    binaries = ("codex",)
    persona_filename = "AGENTS.md"

    @classmethod
    def config_dir(cls) -> Path:
        return paths.codex_config_dir()

    def _slash_dir(self) -> Optional[Path]:
        return self.config_dir() / "prompts"

    def invoke(self, prompt: str, *, model: str = "") -> List[str]:
        argv = ["codex", "exec"]
        if model:
            argv += ["-m", model]
        argv.append(prompt)
        return argv


# ── Cursor ────────────────────────────────────────────────────────────────────


class CursorHarness(Harness):
    name = "cursor"
    label = "Cursor"
    binaries = ("cursor",)
    persona_filename = "milo.mdc"
    export_suffix = ".mdc"

    @classmethod
    def config_dir(cls) -> Path:
        return paths.cursor_rules_dir()

    # Cursor has no subagents or slash commands, only rules — so both kinds
    # land in the rules directory. That is a real mapping rather than a
    # consolation prize: a rule with a description and alwaysApply off is
    # attached by relevance, which is close to how a subagent gets picked.
    def _agent_dir(self) -> Optional[Path]:
        return self.config_dir()

    def _slash_dir(self) -> Optional[Path]:
        return self.config_dir()

    def _agent_frontmatter(self, name, desc, meta):
        return {"description": desc, "alwaysApply": "false"}

    def _command_frontmatter(self, name, desc, meta):
        return {"description": desc, "alwaysApply": "false"}

    def sync(self, ctx: Optional[persona.PersonaContext] = None) -> SyncResult:
        ctx = ctx or persona.build()
        res = SyncResult(self.name)
        try:
            header = (
                "---\ndescription: Milo — Allan's assistant. Also spelled Mylo.\n"
                "alwaysApply: true\n---\n\n"
            )
            res.written.append(
                self._write(self.config_dir() / "milo.mdc", header + ctx.render())
            )
            self._export_packs(res.written)
        except OSError as exc:
            res.error = str(exc)
        return res


# ── Gemini CLI ────────────────────────────────────────────────────────────────


class GeminiHarness(Harness):
    name = "gemini"
    label = "Gemini CLI"
    binaries = ("gemini",)
    persona_filename = "GEMINI.md"

    @classmethod
    def config_dir(cls) -> Path:
        return Path.home() / ".gemini"

    def invoke(self, prompt: str, *, model: str = "") -> List[str]:
        argv = ["gemini"]
        if model:
            argv += ["-m", model]
        return argv + ["-p", prompt]


# ── Generic ───────────────────────────────────────────────────────────────────


class GenericHarness(Harness):
    """Fallback: write plain markdown anyone can paste or `cat` into a prompt."""

    name = "generic"
    label = "Generic / any tool"
    persona_filename = "MILO.md"

    @classmethod
    def detect(cls) -> bool:
        return True

    @classmethod
    def config_dir(cls) -> Path:
        return paths.milo_home() / "persona"

    # Nothing consumes these automatically, which is the point: a tool Milo has
    # never heard of can still be pointed at a folder of plain markdown.
    def _agent_dir(self) -> Optional[Path]:
        return self.config_dir() / "agents"

    def _slash_dir(self) -> Optional[Path]:
        return self.config_dir() / "commands"

    def sync(self, ctx: Optional[persona.PersonaContext] = None) -> SyncResult:
        ctx = ctx or persona.build()
        res = super().sync(ctx)
        try:
            d = self.config_dir()
            # Every common filename, same content — drop into any repo.
            for extra in ("AGENTS.md", "CLAUDE.md", "GEMINI.md", ".cursorrules"):
                res.written.append(self._write(d / extra, ctx.render()))
            # JSON for programmatic consumers (API scripts, custom bots).
            payload = {
                "name": display_name(),
                "aliases": ["milo", "mylo", "Milo Sage", "Mylo Sage"],
                "generated_at": ctx.generated_at,
                "system_prompt": ctx.render(),
                "sections": {n: b for n, b in ctx.sections() if b.strip()},
            }
            p = d / "persona.json"
            p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            res.written.append(p)
        except OSError as exc:
            res.error = str(exc)
        return res


# ── Registry ──────────────────────────────────────────────────────────────────

_HARNESSES: Tuple[type, ...] = (
    OpenCodeHarness,
    ClaudeCodeHarness,
    CodexHarness,
    CursorHarness,
    GeminiHarness,
    GenericHarness,
)


def all_harnesses() -> List[Harness]:
    return [cls() for cls in _HARNESSES]


def get_harness(name: str) -> Optional[Harness]:
    name = (name or "").strip().lower().replace("_", "-")
    aliases = {
        "claude": "claude-code", "claudecode": "claude-code", "cc": "claude-code",
        "oc": "opencode", "open-code": "opencode",
        "codex-cli": "codex", "any": "generic", "all": "generic",
    }
    name = aliases.get(name, name)
    for cls in _HARNESSES:
        if cls.name == name:
            return cls()
    return None


def detect_installed() -> List[Harness]:
    """Every harness present on this machine, generic always last."""
    found = [h for h in all_harnesses() if h.name != "generic" and h.detect()]
    return found + [GenericHarness()]


def sync_all(
    only: Optional[Sequence[str]] = None,
    ctx: Optional[persona.PersonaContext] = None,
) -> List[SyncResult]:
    """Render the persona into every detected (or named) harness."""
    ctx = ctx or persona.build()
    if only:
        targets = [h for h in (get_harness(n) for n in only) if h]
    else:
        targets = detect_installed()
    return [h.sync(ctx) for h in targets]
