# Petty Cash → One UI: Compass, matched to the intranet, mobile + desktop

*Vimit Converters Limited · prepared 2026-06-29 · follows `petty_cash_erpnext_migration_plan.md`*

## The problem (your words)

> "What we have on the intranet works so well, but on Compass / ERPNext / Frappe Cloud is not good — and should all of this not be ONE?"

It should. Today the same petty cash exists **three times**:

| Surface | Stack | Role |
|---|---|---|
| **Intranet** `:8500/petty` | FastAPI + Jinja + vanilla CSS/JS | the one that works (gold standard) |
| **Frappe `www/petty-cash`** | vanilla HTML/JS/CSS | a faithful-ish port, for `pettycash@` login |
| **Compass `PettyCash.tsx`** | React (frappe-react-sdk) | the diverger — "not good" |

**Decision (locked):** collapse to **one** UI — the **Compass React screen**, rebuilt to the intranet's quality on **both** mobile and desktop. Then retire the intranet `/petty` and the `www` page.

## Why Compass "feels not good" — the root cause

It isn't a styling accident. `PettyCash.tsx → EntryView` branches:

```
desktop  → <DeskEntry/>      // a spreadsheet grid, ONLY
phone    → <PhoneWizard/>    // keypad wizard + live feed
```

So on **desktop you get a spreadsheet**, on **mobile you get the wizard** — two different apps. The intranet does NOT do this. The intranet has a consistent model:

- **`/entry`** — a **two-pane: keypad wizard (left) + live running feed (right)** — *the same mental model as mobile*. This is the daily driver. ✅ (screenshot: `g_desktop_wizard_amt.png`)
- **`/sheet/<id>`** — the full **spreadsheet** (Post to Books, opening balances, printing) — a *separate* back-office power view. ✅ (screenshot: `g_desktop_sheet.png`)

Compass promoted the spreadsheet to the desktop default and never built the two-pane wizard+feed. That's the gap.

Secondary regressions vs gold:
- **No delete (🗑) affordance** in the Compass feed — gold has a clear trash per item (`g_mobile_wizard.png`).
- Desktop has no live feed at all (only the grid).

## Gold-standard reference (captured 2026-06-29, real screenshots)

Stored in `scratchpad/shots/`:
- `g_mobile_wizard.png` — mobile feed: navy sticky header w/ "Cash remaining", hero card (big red −251,314 / Spent), day-grouped items (colour dot + recipient + PC/ETR tag + amount + 🗑), sticky navy "+ New entry".
- `g_desktop_wizard_amt.png` — desktop two-pane: wizard card (VOUCHER ▾ pill, "KES 4,500" 46px, 3×4 keypad, Cancel / Next ›) **beside** the live feed.
- `g_desktop_sheet.png` — the spreadsheet: meta bar, LIVE SUMMARY, Voucher Register (TG/TE/SE/OA/FD/GP/OT + IN + PC/ETR), Parking matrix, Bike/Forklift, Wages & Loans, Loans.

## The rebuild — `PettyCash.tsx` (surgical, not a rewrite)

The `.pcx-*` feed styles **already exist** in `brand.css`; desktop just doesn't render them. Most of this is wiring, not new CSS.

### 1. EntryView → two-pane on desktop (the core fix)
- Desktop default becomes **wizard (left, ~432px) + live feed (right)** — reuse `PhoneWizard`'s wizard + feed logic; factor the feed into a shared `<Feed>` so phone and desktop render the same component.
- The spreadsheet (`DeskEntry`) becomes a **secondary toggle**: a "Grid / edit week" button in the header (mirrors the intranet's `/entry` vs `/sheet` split). Default lands on wizard+feed.
- Mobile unchanged in spirit: full-screen wizard overlay + feed + sticky "+ New entry".

### 2. Restore the delete affordance
- Add a per-item **🗑** button to feed items (phone + desktop), calling the existing delete path. Confirm dialog + toast + feed refresh, matching gold. Disabled when the week is Approved/closed.

### 3. Visual parity pass
- Match gold tokens/sizing: 46px amount with blinking cursor, 56px keys, pill type selector, hero card colours (green→red on negative), day labels "Today / Yesterday / Mon 26 Jun", `+`-prefixed green cash-in. Most already in `brand.css` — fill gaps only.

### 4. Verify with real screenshots (both viewports)
- `vite build`, capture mobile + desktop, diff against `g_*` gold. Iterate until it matches.

## Consolidation (retire the duplicates)
- Point `pettycash@vimit.com` at the Compass screen; make `www/petty-cash` a thin redirect/embed (or align it) so there's one UI.
- Parallel-run, then **decommission intranet `/petty`** — Phase 8 of the migration plan.

## Net
One petty cash. The intranet's proven wizard+feed feel, now identical on phone and desktop, on reliable cloud infra, reachable from Compass + the `pettycash@` login + the Capacitor app. No more three-way drift.
