# FigmaPot agent rules for Zeroshot runs

Codex reads this file instead of the repo `AGENTS.md` in Zeroshot worktrees (run.sh copies it as
`AGENTS.override.md`, critic/17). It keeps the policy of `AGENTS.md` that applies to workers and reviewers
and drops what cannot run here. Review it whenever upstream `AGENTS.md` changes.

## Project
FigmaPot is a public fork of Penpot whose goal is a native Figma UI (layout, shortcuts, design-system
flows). Changes must stay small and in upstream style so they merge with future Penpot releases.

## Policy
- Never commit or push; the orchestrator does. Production is out of scope: no prod hosts, credentials
  or data in the repo or in commands.
- Public repo: never write secrets, `.env` values, server addresses or dumps into files.
- User-visible branding stays Penpot; FigmaPot is the fork name only and never appears in the UI.
  Do not add calls to third-party hosts.
- Never edit `CHANGES.md`.
- Never pipe test or lint output straight into filters (`| head`, `| tail`, `| grep`); write it to a
  file under your scratch dir first, then read the file. This keeps failures visible.
- If `AGENTS.local.md` exists, read it after this file; it overrides prose style and workflow, not policy.
- Do not touch modules the task does not need.
- Reviewers apply the same policy and never edit source files.

## Project memories
`.serena/memories` holds dense notes on the code (`mem:foo/bar` is the file `.serena/memories/foo/bar.md`).
Once per session, before your first edit or review verdict: read `critical-info`, then the `<module>/core`
memory of each module you touch (`frontend/core`, `common/core`, `render-wasm/core`, ...). Follow deeper
`mem:` references only when they match the task (e.g. `testing` when you write tests). A no-edit task
needs none of them.

