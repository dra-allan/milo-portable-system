"""
The acceptance test for the entire project.

If this passes, Milo genuinely moves between machines. If it fails, nothing
else in the repo matters, because the one promise it makes is broken.

It is written as a *story* rather than a set of isolated unit tests, because
the failures that actually bite are integration failures: a snapshot that
writes to the wrong directory, a restore that clobbers local state, a tier that
nobody remembered to include.
"""

from __future__ import annotations

from conftest import switch_machine, reload_miloctl


def _seed_machine_a():
    """Everything a real user would have accumulated."""
    from miloctl.memory import store as brain
    from miloctl.curated import CuratedMemory
    from miloctl.skills import registry
    from miloctl.profile import Profile

    brain().save("Allan runs an MT5 trade copier across 3 accounts",
                 category="fact", tags=["trading"], pinned=True)
    brain().save("Collapsed 4 repos into milo-portable-system",
                 category="decision", tags=["architecture"])

    notes = CuratedMemory()
    notes.add("memory", "Milo runs on OpenCode; agent profile is 'milo'.")
    notes.add("user", "Allan interchanges Milo and Mylo; never correct it.")

    registry().create("deploy-bot", "Deploy the Telegram bot and confirm it replies.",
                      tags=["telegram"])

    prof = Profile()
    prof.observe("tone", "direct, no preamble", section="working_style",
                 source="stated")
    prof.save()


def test_machine_to_machine_migration(milo_home, git_repo, tmp_path, monkeypatch):
    """A -> snapshot -> B. Everything survives; B's own state is not destroyed."""
    monkeypatch.setenv("MILO_REPO_ROOT", str(git_repo))
    reload_miloctl()
    from miloctl import paths, backup
    paths.ensure_tree()

    _seed_machine_a()

    snap = backup.snapshot()
    assert not snap.error, snap.error
    assert snap.counts["memories"] == 2
    assert snap.counts["notes"] == 2, "the always-loaded tier must be captured"
    assert snap.counts["skills"] == 1
    assert snap.counts["traits"] == 1

    # Secrets must never reach the snapshot, only the key names.
    template = (git_repo / "state" / "env.template").read_text()
    assert "=" in template
    assert not any(line.split("=", 1)[1].strip()
                   for line in template.splitlines()
                   if "=" in line and not line.startswith("#")), \
        "env.template leaked a value"

    # ---- move to a different computer -------------------------------------
    switch_machine(monkeypatch, tmp_path, "machine_b", git_repo)
    import miloctl.backup as backup_b
    from miloctl.curated import CuratedMemory
    from miloctl.memory import store as brain_b
    from miloctl.skills import registry as reg_b
    from miloctl.profile import Profile as Profile_b

    # B has learned something of its own *before* the restore. This is the
    # case a naive file-copy restore silently destroys.
    CuratedMemory().add("memory", "This laptop has no GPU; never suggest local models.")

    res = backup_b.restore(pull=False)
    assert not res.error, res.error

    assert brain_b().search("MT5"), "durable memory did not survive"
    assert reg_b().get("deploy-bot") is not None, "skill did not survive"
    assert "preamble" in Profile_b().prompt_block(), "user model did not survive"

    notes_b = CuratedMemory()
    memory_entries = notes_b.entries["memory"]
    assert any("OpenCode" in e for e in memory_entries), "note did not survive"
    assert any("no GPU" in e for e in memory_entries), \
        "restore destroyed a note that only existed on this machine"
    assert any("Mylo" in e for e in notes_b.entries["user"])


def test_restore_is_idempotent(milo_home, git_repo, tmp_path, monkeypatch):
    """Running restore twice must not duplicate or inflate counts."""
    monkeypatch.setenv("MILO_REPO_ROOT", str(git_repo))
    reload_miloctl()
    from miloctl import paths, backup
    paths.ensure_tree()
    _seed_machine_a()
    backup.snapshot()

    switch_machine(monkeypatch, tmp_path, "machine_c", git_repo)
    import miloctl.backup as backup_c
    from miloctl.curated import CuratedMemory

    first = backup_c.restore(pull=False)
    second = backup_c.restore(pull=False)

    assert first.counts["notes"] == 2
    assert second.counts["notes"] == 0, \
        "a second restore reported new notes it did not actually add"
    assert len(CuratedMemory().entries["memory"]) == 1


def test_backup_blocks_a_leaked_credential(milo_home):
    """A token saved into memory must stop the push, not ride along with it."""
    from miloctl.memory import store as brain
    from miloctl import backup, paths

    brain().save("my token is EXAMPLE_GITHUB_TOKEN_FOR_TESTS")
    res = backup.snapshot(paths.milo_home() / "snap")

    assert res.leaks, "the leak scanner did not catch a GitHub PAT"
    kinds = {kind for _, kind, _ in res.leaks}
    assert "github-pat" in kinds
    # The sample must be masked — a scanner that prints the secret it found is
    # a leak of its own, into logs and CI output.
    assert not any("abcdefghijklmnop" in sample for _, _, sample in res.leaks)
