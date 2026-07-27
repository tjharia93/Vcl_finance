# Stage 0 — Repo context generation (Gemini 2.5 Pro)

You are the **context agent** for a Frappe/ERPNext custom app (`vcl_finance`).
Read the ENTIRE repository — you have a 1M-token window, use it. Produce ONE
file that every later agent (Planner, Coder, Tester) reads instead of re-scanning.

## Your task
Write `.pipeline/project-context.md`. Do not change any source file. If the
file already exists, overwrite it.

## What to capture (be concrete, cite real paths)
1. **App layout** — modules under `vcl_finance/` (e.g. `petty_cash/`,
   `ar_reconciliation/`), and what each module is for.
2. **DocTypes** — for every DocType under `*/doctype/*/*.json`: its name,
   module, whether it's a child table (`istable`), key fields, links, and its
   controller (`<doctype>.py`) and client script (`<doctype>.js`) if present.
   Note naming series, and parent↔child relationships (Table fields).
3. **Frappe conventions in THIS repo** — how `hooks.py` is wired (doc_events,
   fixtures, scheduler_events, override_whitelisted_methods, website routes),
   `patches.txt` migrations, `www/` web pages, `public/` js/css, fixtures.
4. **Server-side patterns** — validation/controller style, whitelisted API
   methods, how money/rounding is handled, party/account linking, any
   ERPNext docs created (Journal Entry, Payment Entry).
5. **The Petty Cash port specifically** — current state of the `petty_cash`
   module DocTypes vs the original FastAPI prototype it's replacing
   (`~/projects/apps/intranet/petty_cash/`). List what exists, what's stubbed,
   and gaps. This is the active migration target.
6. **Build / test commands** — how to migrate and run tests in this app
   (`bench --site <site> migrate`, `bench --site <site> run-tests --app vcl_finance`,
   how the site is reached locally or on Frappe Cloud). Note if there is no
   local bench and tests must run a specific way.
7. **Conventions to preserve** — anything a code generator MUST follow to not
   break the app (field name casing, module folder rules, `__init__.py`,
   DocType JSON shape, `modules.txt`).

## Output format
Headed Markdown, scannable, path-anchored (use `file:line` where useful).
Aim for completeness over brevity, but no filler. End with a short
**"Gotchas / do-not-break"** list.

WRITE the result to `.pipeline/project-context.md` and nothing else.
