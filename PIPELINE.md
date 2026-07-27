# VCL multi-agent dev pipeline

A 5-stage feature pipeline that splits work across the three CLIs you have
installed (`claude`, `codex`, `gemini`), handing off through flat files in
`.pipeline/`. Built to develop this Frappe app (`vcl_finance`) — first job:
finish porting the Petty Cash app into ERPNext DocTypes.

## Stages

| # | Stage | Engine | Reads | Writes |
|---|-------|--------|-------|--------|
| 0 | Context | Gemini 2.5 Pro | whole repo | `.pipeline/project-context.md` (once, reused) |
| 1 | Plan | Claude Opus (`planner`) | context + request | `.pipeline/spec.md` |
| 2 | Code | Codex o3 | context + spec | source files + `.pipeline/changes.md` |
| 3a| Test write | Gemini 2.5 Pro | context + spec + changes | test files |
| 3b| Test run | Claude Sonnet (`test-runner`) | tests | `.pipeline/test-results.md` (pauses on fail, never fixes) |
| 4 | Review | Claude Opus (`reviewer`, read-only) | **git diff** + spec + changes + tests | `.pipeline/review.md` |

Orchestrated by the **`/ship`** command in Claude Code.

## Run it

```
/ship <plain-English feature request>
# e.g. /ship Port the Petty Cash weekly sheet to a Petty Cash Sheet DocType with child tables for vouchers, wages, loans, parking and a reconciliation summary
```

`/ship`:
1. Cleans the per-feature handoff files (keeps `project-context.md`).
2. Runs Stage 0 only if `project-context.md` is missing or `--refresh-context` is passed.
3. Stops and asks you if the Planner raised **OPEN QUESTIONS**.
4. Pauses if tests fail (Tester reports, does not fix).
5. Ends by showing you `review.md`.

## Design rules (enforced in the prompts)
- **Tester never fixes** — it reports and the pipeline pauses for you.
- **Reviewer is read-only** — it runs `git diff` (ground truth, not the Coder's self-report) and cannot edit source.
- **Context is pre-computed once** — agents read `project-context.md`, they don't re-scan per feature.
- **Parallel features** use git worktrees (`git worktree add ../vcl_finance-<feat> -b feat/<feat>`), each with its own `.pipeline/`.

## Manual stage commands (if you want to run a stage by hand)
```bash
# Stage 0 — regenerate context
gemini -y -m gemini-2.5-pro -p "$(cat .gemini/context-prompt.md)"

# Stage 2 — coder (after spec.md exists)
codex exec -m o3 --cd "$(pwd)" "$(cat .codex/coder-prompt.md)"

# Stage 3a — test writer
gemini -y -m gemini-2.5-pro -p "$(cat .gemini/test-writer-prompt.md)"
```
Adjust model ids/flags to your CLI versions if they differ.
