---
name: writing-skills
description: Turn something you just worked out into a reusable skill.
version: 1.0.0
author: Milo
tags: [meta, skills, learning]
pinned: true
---

# Writing skills

A skill is a note to your future self, who will have forgotten everything and
will be in a hurry. Write for that reader.

## When to write one

Write a skill when **all three** are true:

1. You just worked something out that took more than one attempt.
2. It will come up again.
3. The next attempt would otherwise repeat the same discovery.

Do **not** write one for a single lookup, anything already obvious from `--help`,
or something that will be stale next week. A library of near-misses is worse
than a small library of sharp ones: the index is what the model reads to decide,
so noise there costs every future session.

## The description is the whole routing decision

The prompt index shows only `name` + `description`, truncated at 60 characters.
Everything else is invisible until a skill has already been chosen. So the
description must answer one question: *would this help with what I am doing?*

```
good:  Deploy the Telegram bot and confirm it replies.
bad:   A comprehensive skill for bot deployment workflows.
```

Rules that follow from that:

- Start with a verb. Describe the *task*, not the skill.
- One sentence, ending in a period.
- Never repeat the skill's own name — it is already on the line.
- No "powerful", "comprehensive", "seamless", "advanced", "robust". They take up
  the scarcest space in the whole system and say nothing.

Check yourself: `milo skills lint <name>`.

## The body

Assume the reader has the tools but not the context.

- Lead with the shortest thing that works. Caveats go below, not above.
- Real commands, copy-pasteable, with real flags.
- **Write down what bit you.** The error message you hit and the fix. This is
  the highest-value part of a skill and the part everyone skips.
- State preconditions plainly ("needs `TELEGRAM_BOT_TOKEN` in the env").
- Prefer 30 useful lines to 300 thorough ones.

## Never hardcode a path

A skill containing `C:\Users\user\...` breaks the moment Milo moves machines —
the exact failure this system was rebuilt to remove.

```bash
milo path home      # MILO_HOME
milo path vault     # the Obsidian vault, wherever it lives here
milo path memory    # the brain
```

Use those, or the matching environment variables. Assume a different username,
a different OS, and sometimes a phone.

## Keep them alive

- Skill was slightly wrong in use? Fix it now: `milo improve <name>`. The
  friction is fresh; in an hour you will not bother.
- Two skills doing one job? Merge them. `milo curate` flags the pair.
- Genuinely obsolete? `milo skills archive <name>` — never delete. Archiving is
  reversible and costs nothing; deletion is a decision you cannot revisit.

## Save it

```bash
milo learn "what you just worked out"     # drafts a skill from the session
milo skills lint <name>                   # check it will route
milo backup                               # nothing exists until it is pushed
```
