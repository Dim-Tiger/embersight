# CLAUDE.md

Claude Code reads this file automatically at the start of every session in this
repo. Keep the "Current work" section below up to date so a new session (a
fresh chat, a new cloud container, a different machine) can pick up where the
last one left off from the repo alone — never rely on conversation memory
surviving between sessions.

## Project overview

EmberSight is a human-in-the-loop incident-management dashboard for CAL FIRE
IMTs. See `README.md` for the full quickstart and `CLAUDE_MASTER_PLAN.md` for
the agent architecture. Quick layout:

- `web/public/landing/` — static marketing landing page (plain HTML/CSS/JS,
  no build step, no bundler).
- `web/app/` — the Next.js 15 + React 19 product dashboard.
- `agent/` — Python FastAPI + LangGraph backend for the agent team.

## Conventions

- Feature branches are named `claude/...`.
- Commit and push work in small, frequent steps — pushed commits are the
  only thing guaranteed to survive a context reset or a new chat session.
- Update the "Current work" section below before ending a session whenever
  there's unfinished work, so the next session doesn't have to guess state.
- **Feature work is committed, pushed, and turned into a PR by the user, not
  by Claude.** Commits made from a Claude session are authored as
  `Claude <noreply@anthropic.com>` regardless of who types the git command —
  only a commit made on the user's own machine, with their own git identity,
  credits them on GitHub. So for actual site/feature changes: describe the
  exact edit precisely enough for the user to apply it locally themselves,
  then remind them to commit/push/open the PR once it's verified working.
  Repo-wide scaffolding/docs (like this file) are fine for Claude to commit
  directly, since they're infrastructure, not a feature the user wants credit
  for.

## Current work: EmberSight.ai landing page

- Branch: `claude/embersight-website-dev-d85o2r`
- Goal: modify/add to the marketing landing page (`web/public/landing/`).
- Status: dev workflow set up (branch pushed to GitHub, user has it checked
  out locally, previews it via a local static server). No content changes
  made to the landing page yet.
- Next: waiting on the user's first requested change.
- Notes: user is learning the git/webdev workflow alongside this project —
  explain reasoning, not just commands, when making changes.
