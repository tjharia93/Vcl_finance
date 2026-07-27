---
description: Run the 5-stage multi-agent feature pipeline (context → plan → code → test → review) for vcl_finance
argument-hint: <plain-English feature request>
---

# /ship — pipeline orchestrator

You are the **orchestrator**. Drive the feature `$ARGUMENTS` through the pipeline
below, in order, from the repo root. Three engines: Claude subagents (via the
Task tool), Codex CLI, Gemini CLI. Handoffs are files in `.pipeline/`.

Announce each stage as you start it. Honor every GATE — do not power through them.

## Stage prep — clean + record
1. Keep `.pipeline/project-context.md`. **Delete** the per-feature handoff files
   if present: `request.md spec.md changes.md test-results.md review.md`.
2. Write the request verbatim to `.pipeline/request.md`.

## Stage 0 — Context (Gemini), conditional
- If `.pipeline/project-context.md` is **missing**, or the user passed
  `--refresh-context` in `$ARGUMENTS`, run:
  ```bash
  gemini -y -m gemini-2.5-pro -p "$(cat .gemini/context-prompt.md)"
  ```
  Then confirm `.pipeline/project-context.md` was written (non-empty).
- Otherwise skip Stage 0 (reuse the existing context) and say so.

## Stage 1 — Plan (Claude Opus)
- Dispatch the **`planner`** subagent (Task tool, subagent_type `planner`).
- When it finishes, read `.pipeline/spec.md`.
- **GATE — OPEN QUESTIONS:** if spec's top section is not `OPEN QUESTIONS: none`,
  STOP. Show the user the open questions and wait for answers. Do not proceed.
  When they answer, append the answers to `.pipeline/request.md` and re-run
  Stage 1.

## Stage 2 — Code (Codex o3)
```bash
codex exec -m o3 --cd "$(pwd)" "$(cat .codex/coder-prompt.md)"
```
Then read `.pipeline/changes.md`. If the Coder reported it stopped (unresolved
spec), STOP and surface it.

## Stage 3a — Test write (Gemini)
```bash
gemini -y -m gemini-2.5-pro -p "$(cat .gemini/test-writer-prompt.md)"
```

## Stage 3b — Test run (Claude Sonnet)
- Dispatch the **`test-runner`** subagent. Read `.pipeline/test-results.md`.
- **GATE — TEST FAIL:** if Verdict is `FAIL` or `BLOCKED`, STOP. Show the user
  the failures/blocker from `test-results.md`. The Tester does not fix — ask the
  user how to proceed (e.g. re-plan, hand the failure back to the Coder, or fix
  manually). Do not auto-fix.

## Stage 4 — Review (Claude Opus, read-only)
- Dispatch the **`reviewer`** subagent. Read `.pipeline/review.md`.
- Present the user the **Verdict** and the Blocking/Should-fix findings, plus any
  `changes.md` vs `git diff` discrepancies.

## Finish
Summarize: what shipped, the review verdict, and the suggested next action
(commit, fix blockers, or open questions). **Do not commit** unless the user asks.

## Notes
- Parallel features: run in a worktree —
  `git worktree add ../vcl_finance-<feat> -b feat/<feat>` — each gets its own
  `.pipeline/`. Generate context once and copy `project-context.md` across.
- Adjust `-m o3` / `gemini-2.5-pro` / flags if your CLI versions differ.
