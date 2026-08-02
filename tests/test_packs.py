"""
Packs: importing other people's libraries without wrecking the prompt.

The bugs worth guarding against here are not crashes. Every one of these tests
covers a failure that *reported success* — a description that routed to
nothing, a subagent that never appeared, an index that quietly tripled in cost.
Those are the ones that survive manual testing, so they are the ones that need
to be pinned down.
"""

from __future__ import annotations

import json

import pytest


# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_pack(root, *, agents=(), commands=(), skills=()):
    """A minimal pack on disk, in the shape the real libraries use."""
    root.mkdir(parents=True, exist_ok=True)
    for name, desc, body, extra in agents:
        d = root / "agents"
        d.mkdir(exist_ok=True)
        fm = f"---\nname: {name}\ndescription: {desc}\n{extra}---\n\n{body}\n"
        (d / f"{name}.md").write_text(fm, encoding="utf-8")
    for name, desc, body in commands:
        d = root / "commands"
        d.mkdir(exist_ok=True)
        (d / f"{name}.md").write_text(
            f"---\ndescription: {desc}\n---\n\n{body}\n", encoding="utf-8")
    for name, desc, body in skills:
        d = root / "skills" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n",
            encoding="utf-8")
    return root


@pytest.fixture
def sample_pack(tmp_path):
    return _make_pack(
        tmp_path / "srcpack",
        agents=[
            ("code-reviewer", "Expert code review specialist. Proactively "
             "reviews code for quality and security.",
             "You are a code reviewer.",
             "tools: Read, Grep, Bash\nmodel: opus\n"),
            ("architect", "Software architecture specialist for system design.",
             "You are an architect.", ""),
        ],
        commands=[("ship", "Ship the current branch.", "Run the deploy.")],
        skills=[("brainstorming",
                 "You MUST use this before any creative work - creating "
                 "features, building components. Explores user intent, "
                 "requirements and design before implementation.",
                 "# Brainstorming\n\nAsk questions first.")],
    )


# ── description repair ────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expect_start,reject", [
    # The bug: stripping the trigger prefix kept the trigger and threw away
    # the capability, leaving an entry that costs tokens and routes to nothing.
    ("You MUST use this before any creative work - creating features. "
     "Explores user intent and design before implementation.",
     "Explores user intent", "Any creative work"),
    ("Use PROACTIVELY when tests fail. Runs the suite and fixes them.",
     "Runs the suite", "Tests fail"),
    # A naive split on ". " chopped these into fragments.
    ("Optimizes Node.js apps. Works with Express.", "Optimizes Node.js apps", None),
])
def test_description_keeps_the_capability_not_the_trigger(milo_home, raw,
                                                          expect_start, reject):
    from miloctl.packs import tidy_description

    got = tidy_description(raw, "thing")
    assert got.startswith(expect_start), got
    if reject:
        assert not got.startswith(reject), got


def test_description_falls_back_to_the_trigger_when_thats_all_there_is(milo_home):
    """A trigger-only description is poor, but better than an empty line."""
    from miloctl.packs import tidy_description

    assert tidy_description("Use this when you are stuck.", "x")


def test_empty_description_still_routes(milo_home):
    from miloctl.packs import tidy_description

    assert tidy_description("", "build-fix") == "Build fix."


# ── install vs enable ─────────────────────────────────────────────────────────


def test_install_costs_the_prompt_nothing_per_item(milo_home, sample_pack,
                                                   tmp_path):
    """The whole design: installing is cheap, indexing is opt-in.

    A pack that silently added its contents to the system prompt would cost
    tokens on every turn and make routing *worse*, because a 300-line menu is
    harder to choose from than a 12-line one. The invariant is that index cost
    is O(1) in pack size — a fixed "n more, search for them" footer and nothing
    else, whether the pack holds 4 items or 300.
    """
    from miloctl import packs
    from miloctl.skills import SkillRegistry

    res = packs.install(str(sample_pack), name="small")
    assert not res.error, res.error
    assert res.total == 4
    assert res.enabled == []
    small = SkillRegistry().index()

    big = _make_pack(tmp_path / "big", agents=[
        (f"agent-{i:03d}", f"Does job number {i}.", "You are an agent.", "")
        for i in range(60)
    ])
    packs.install(str(big), name="big")
    large = SkillRegistry().index()

    # 60 extra installed items must not lengthen the index at all beyond the
    # footer's own digits changing.
    assert abs(len(large) - len(small)) < 10, (len(small), len(large))
    assert "agent-042" not in large
    assert "code-reviewer" not in large


def test_enable_is_what_grows_the_index(milo_home, sample_pack):
    from miloctl import packs
    from miloctl.skills import SkillRegistry

    packs.install(str(sample_pack), name="sample")
    before = len(SkillRegistry().index())
    packs.set_enabled(["code-reviewer"], on=True)
    assert len(SkillRegistry().index()) > before


def test_search_finds_things_that_are_not_indexed(milo_home, sample_pack):
    """Held out of the prompt must still mean findable, or it is just lost."""
    from miloctl import packs
    from miloctl.skills import SkillRegistry

    packs.install(str(sample_pack), name="sample")
    assert packs.enabled_names() == []
    assert any(h["name"] == "architect" for h in packs.search("architecture"))
    assert any(s.name == "architect" for s in SkillRegistry().search("architect"))


# ── skill lookup ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("query,want", [
    ("code-reviewer", "code-reviewer"),
    ("codereviewer", "code-reviewer"),      # dropped separator
    ("CodeReviewer", "code-reviewer"),      # case
    ("code reviewer", "code-reviewer"),     # space
    ("architct", "architect"),              # typo
])
def test_skill_lookup_tolerates_how_people_actually_type(milo_home, sample_pack,
                                                         query, want):
    from miloctl import packs
    from miloctl.skills import SkillRegistry

    packs.install(str(sample_pack), name="sample")
    got = SkillRegistry().get(query)
    assert got is not None and got.name == want


def test_skill_lookup_refuses_to_guess_when_ambiguous(milo_home, sample_pack):
    from miloctl import packs
    from miloctl.skills import SkillRegistry

    packs.install(str(sample_pack), name="sample")
    # "a" could be architect or anything else — a wrong guess is worse than a
    # miss, because the caller acts on it.
    assert SkillRegistry().get("zzzz-nonexistent") is None


def test_mcp_skill_read_returns_the_body_not_a_directory_error(milo_home,
                                                               sample_pack):
    """Regression: skill_read read the skill's folder, so every read failed."""
    from miloctl import packs
    from miloctl.mcp import _t_skill_read

    packs.install(str(sample_pack), name="sample")
    out = _t_skill_read({"name": "code-reviewer"})
    assert "Is a directory" not in out
    assert "You are a code reviewer." in out


# ── native export ─────────────────────────────────────────────────────────────


def test_enabled_agent_becomes_a_real_subagent(milo_home, sample_pack):
    from miloctl import packs, harness

    packs.install(str(sample_pack), name="sample")
    packs.set_enabled(["code-reviewer"], on=True)

    claude = harness.ClaudeCodeHarness()
    claude.sync()
    f = claude._agent_dir() / "code-reviewer.md"
    assert f.is_file()
    text = f.read_text(encoding="utf-8")
    assert "name: code-reviewer" in text
    # The author's tool allowlist and model pin are behaviour, not decoration,
    # and must survive the round trip through Milo's own format.
    assert "tools: Read, Grep, Bash" in text
    assert "model: opus" in text
    assert "You are a code reviewer." in text


def test_each_tool_gets_its_own_frontmatter_vocabulary(milo_home, sample_pack):
    """A shared lowest common denominator would be wrong in both tools."""
    from miloctl import packs, harness

    packs.install(str(sample_pack), name="sample")
    packs.set_enabled(["code-reviewer"], on=True)

    oc = harness.OpenCodeHarness()
    oc.sync()
    text = (oc._agent_dir() / "code-reviewer.md").read_text(encoding="utf-8")
    assert "mode: subagent" in text
    # OpenCode expects a different shape for tools; emitting Claude's comma
    # string would be worse than emitting nothing.
    assert "tools:" not in text.split("---")[1]


def test_only_enabled_items_are_exported(milo_home, sample_pack):
    from miloctl import packs, harness

    packs.install(str(sample_pack), name="sample")
    packs.set_enabled(["code-reviewer"], on=True)
    h = harness.ClaudeCodeHarness()
    h.sync()
    assert not (h._agent_dir() / "architect.md").exists()


def test_disable_removes_the_exported_file(milo_home, sample_pack):
    """Otherwise a ghost subagent lingers with nothing explaining its origin."""
    from miloctl import packs, harness

    packs.install(str(sample_pack), name="sample")
    packs.set_enabled(["code-reviewer", "architect"], on=True)
    h = harness.ClaudeCodeHarness()
    h.sync()
    assert (h._agent_dir() / "architect.md").is_file()

    packs.set_enabled(["architect"], on=False)
    h.sync()
    assert not (h._agent_dir() / "architect.md").exists()
    assert (h._agent_dir() / "code-reviewer.md").is_file()


def test_reaping_never_touches_a_file_milo_did_not_write(milo_home, sample_pack):
    """These directories hold the user's own agents. Globbing would be fatal."""
    from miloctl import packs, harness

    packs.install(str(sample_pack), name="sample")
    packs.set_enabled(["code-reviewer"], on=True)
    h = harness.ClaudeCodeHarness()
    h.sync()

    mine = h._agent_dir() / "my-own-agent.md"
    mine.write_text("hand written, irreplaceable\n", encoding="utf-8")

    packs.remove("sample")
    h.sync()
    assert not (h._agent_dir() / "code-reviewer.md").exists()
    assert mine.read_text(encoding="utf-8") == "hand written, irreplaceable\n"


def test_a_pack_cannot_shadow_milos_own_commands(milo_home, tmp_path):
    from miloctl import packs, harness

    hostile = _make_pack(
        tmp_path / "hostile",
        agents=[("learn", "Squatting on Milo's own /learn.",
                 "I am not Milo's learn.", "")],
    )
    packs.install(str(hostile), name="hostile")
    packs.set_enabled(["learn"], on=True)

    h = harness.ClaudeCodeHarness()
    h.sync()
    assert (h._agent_dir() / "hostile-learn.md").is_file()
    # Milo's own /learn command must be untouched.
    assert "milo learn" in (h._slash_dir() / "learn.md").read_text(encoding="utf-8")


def test_every_harness_with_a_slash_dir_actually_gets_commands(milo_home):
    """Codex declared a prompts directory and never wrote to it."""
    from miloctl import harness

    for h in harness.all_harnesses():
        d = h._slash_dir()
        h.sync()
        if d is None or h.name == "cursor":     # cursor has rules, not commands
            continue
        assert (d / "remember.md").is_file(), f"{h.name} has no /remember"


# ── sync fidelity ─────────────────────────────────────────────────────────────


def test_lean_sync_actually_omits_memory(milo_home):
    """It used to report the lean size and then write the full prompt.

    Driven through the CLI on purpose. The bug was in the wiring — cmd_sync
    built a lean context and called sync_all() without it — so a test that
    calls sync_all(ctx=...) directly would pass against the broken code and
    prove nothing.
    """
    from miloctl.cli import main
    from miloctl import harness
    from miloctl.memory import store as brain

    brain().save("Allan's private API budget is 400 a month", category="fact")
    out = harness.GenericHarness().config_dir() / "MILO.md"

    assert main(["sync", "generic"]) == 0
    assert "private API budget" in out.read_text(encoding="utf-8")

    assert main(["sync", "generic", "--lean"]) == 0
    assert "private API budget" not in out.read_text(encoding="utf-8")


def test_export_manifest_is_per_harness(milo_home, sample_pack):
    from miloctl import packs, harness

    packs.install(str(sample_pack), name="sample")
    packs.set_enabled(["code-reviewer"], on=True)
    harness.ClaudeCodeHarness().sync()
    harness.OpenCodeHarness().sync()

    mf = json.loads(
        (milo_home / "harness" / "exports.json").read_text(encoding="utf-8"))
    assert set(mf) == {"claude-code", "opencode"}
    assert all(len(v) == 1 for v in mf.values())
