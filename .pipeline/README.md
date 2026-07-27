# .pipeline/ — agent handoff files

Flat files the pipeline stages pass between each other. See `../PIPELINE.md`.

| File | Written by | Read by | Lifetime |
|------|-----------|---------|----------|
| `project-context.md` | Stage 0 (Gemini) | all stages | **persistent** (committed, reused) |
| `request.md` | `/ship` | Planner | per feature |
| `spec.md` | Planner (Opus) | Coder, Tester, Reviewer | per feature |
| `changes.md` | Coder (Codex) | Tester, Reviewer | per feature |
| `test-results.md` | Test runner (Sonnet) | Reviewer, you | per feature |
| `review.md` | Reviewer (Opus) | you | per feature |

Per-feature files are git-ignored (regenerated each run). `project-context.md`
is committed. Regenerate context with `/ship ... --refresh-context` or the
manual Gemini command in `PIPELINE.md`.
