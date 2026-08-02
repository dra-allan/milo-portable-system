---
name: memory-discipline
description: Decide what to remember, where to put it, and what to skip.
version: 1.0.0
author: Milo
tags: [memory, meta]
pinned: true
---

# What to remember, and where

Milo has four places to put something. Choosing wrong is the reason an assistant
can feel forgetful while technically "having memory".

## The four tiers

**The always-loaded notes** — `milo note add "..."`
Small, hard-capped, injected into *every* session unprompted. The only tier that
works when nobody thinks to search. Put things here that must shape behaviour on
turn one of a brand-new conversation: how Allan wants to be spoken to, standing
constraints, facts about this machine. Because it is capped, adding is a trade.
That is the point.

**The brain** — `milo remember "..."`
Searchable long-term store. Decisions, outcomes, facts, hard-won fixes. Cheap to
add, surfaces only when something queries it. Most things belong here.

**Skills** — `milo learn "..."`
*Procedures*, not facts. If the answer is a sequence of steps you would otherwise
re-derive, it is a skill.

**The vault** — `milo vault note "..."`
Long-form: research, meeting notes, drafts — anything you would want to read as a
document later. Obsidian is the reader.

## Deciding

Ask one question: *when should this come back to me?*

- Every session, unasked → **note**
- When I search for it → **remember**
- When I do that task again → **skill**
- When I sit down to read → **vault**

## Worth saving

- Decisions **and the reason** — the reason is what stops it being re-litigated
- Preferences stated once ("stop apologising", "no emojis")
- Fixes that took real effort, with the error text that identifies them
- Stable facts: account names, machine quirks, who is who
- Things Allan said matter to him

## Not worth saving

- Anything re-derivable in one command
- Transient state ("the build is running")
- Whole file contents — save the path and what it is for
- Restating the conversation back into memory
- **Secrets.** Ever. The backup leak scanner will block the push and it is right
  to. Credentials belong in the env file.

## Write it so it survives alone

A memory is read months later with no surrounding conversation.

```
weak:   fixed the bug
better: MT5 copier dropped fills on partial closes; the volume check
        compared requested lots instead of filled lots
```

Include the *why*. Facts age; reasons stay useful.

## Maintenance

```bash
milo recall "query"    # search everything
milo note              # see the always-loaded tier and how full it is
milo curate            # age out stale skills, flag duplicates
milo backup            # none of it exists until it is pushed
```
