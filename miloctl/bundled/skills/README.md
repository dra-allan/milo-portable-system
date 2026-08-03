---
status: active
project: milo-system
type: reference
---
# Bundled Skills — Milo (self-contained)

Every skill Milo ships with lives here, vendored in. No external skill is
invoked at runtime — Milo owns a copy of everything it uses (Allan's rule).

## Layout
- `core/` — ship-with-Milo meta skills (writing-skills, moving-machines).
- `memory/` — memory discipline.
- `design/` — frontend / UI / brand / visual-design skills.
- `process/` — engineering process: planning, TDD, debugging, code review, verification.

## Provenance — absorbed Aug 2026
Vendored from public skill repos, tailored to Milo (frontmatter normalized,
`author: Milo`, `origin: bundled`). Body content preserved; supporting scripts
copied in where the skills ship them so each is self-contained.

| Source repo | What was absorbed |
|---|---|
| `obra/superpowers` | brainstorming, writing-plans, executing-plans, systematic-debugging, test-driven-development, verification-before-completion, dispatching-parallel-agents, subagent-driven-development, using-git-worktrees, finishing-a-development-branch, requesting/receiving-code-review, using-superpowers → `process/` |
| `WorldFlowAI/everything-claude-code` | backend-patterns, frontend-patterns, coding-standards, continuous-learning, security-review, strategic-compact, verification-loop → `process/` |
| `pbakaus/impeccable` | impeccable (+ scripts/, reference/) → `design/` |
| `Leonxlnx/taste-skill` | design-taste-frontend (+v1), image-to-code, redesign, minimalist-ui, industrial-brutalist-ui, high-end-visual-design, brandkit, imagegen-frontend-web/mobile, stitch-design-taste, full-output-enforcement, gpt-taste → `design/` |
| `nextlevelbuilder/ui-ux-pro-max-skill` | ui-ux-pro-max, design, design-system, brand, ui-styling, banner-design, slides → `design/` |
| `dgreenheck/webgpu-claude-skill` | webgpu-threejs-tsl → `design/` |

## Not yet vendored (deferred)
- `msitarzewski/agency-agents` — agent definitions by domain (engineering/design/
  marketing/sales/finance/product), not SKILL.md format. Candidate for the
  `agents/` tree, not skills. Stage when Allan wants domain agents.
- `VoltAgent/awesome-design-md` — `design-md/` spec format, not skill format.
- Services (not repos, no code to copy): `21st.dev`, `fonts.google.com`,
  `stitch.withgoogle.com`, `microsoft/playwright`, `anthropics/claude-code`,
  `amaancoderx/npxskillui` (repo 404).

## Notes
- Descriptions are hard-truncated at 60 chars (miloctl/skills.py MAX_DESCRIPTION).
- Impeccable's `allowed-tools:` lines reference bundled script paths; scripts
  are vendored alongside, so calls resolve within milo.
