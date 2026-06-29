# Petty Cash → ERPNext / Frappe — Migration Plan

*Vimit Converters Limited · prepared 2026-06-19 · target app: `vcl_finance` (Frappe Cloud)*

## Why we're moving it

The Petty Cash app today runs on the **intranet box as a FastAPI app** (`:8500/petty`).
It works well, but the intranet host isn't always up — and petty cash is a
daily, money-touching tool that can't depend on one local machine. We move it to
**ERPNext / Frappe Cloud** (`vimitconverters.frappe.cloud`) where it's hosted,
backed-up and always reachable, surface it inside **VCL Compass**, then ship a
**native app** that links to the cloud version. The keypad wizard and the
**camera receipt capture** (which works well) come across unchanged.

## What we keep (it already works)

- Keypad-first **entry wizard**: Type → Amount → Category → Details → Save.
- **Running feed** with live cash-remaining; tap-to-edit, swipe-to-delete.
- **Camera receipt photos** attached per entry (📎 in the feed).
- **Carry-forward** between weeks (counted cash, else expected close — negative carried as-is).
- **Admin vs PIC** split (week management vs entry-only).
- Two floats (Cash / Hauz-Pay), weekly sheets, reconciliation summary.

## Target architecture

```
              BEFORE                                AFTER
   Intranet box (FastAPI + SQLite)        Frappe Cloud — vimitconverters.frappe.cloud
        :8500/petty   (fragile)                       │
                                          ┌───────────┴───────────┐
                                          │  vcl_finance app       │
                                          │  Petty Cash DocTypes   │  ← data (backed up)
                                          │  + server logic + API  │
                                          └───────────┬───────────┘
                                                      │
                                   ┌──────────────────┼──────────────────┐
                              VCL Compass        Frappe Desk        Native app
                            (Petty Cash screen)  (admin/back-office) (Capacitor → cloud URL)
                                                      │
                                   restricted login: pettycash@vimit.com (Petty Cash only)
```

## The four things you asked for

| # | Requirement | How |
|---|-------------|-----|
| 1 | **Move to ERPNext/Frappe** (intranet unstable) | DocTypes + server logic + API in `vcl_finance`, hosted on Frappe Cloud |
| 2 | **Visible in Compass** | A Petty Cash screen/route in Compass, role-scoped |
| 3 | **App that links to it** | **Compass mobile** — ONE Capacitor app wrapping the whole Compass SPA (camera at app level). Petty Cash is a screen inside it; `pettycash@` sees only that screen |
| 4 | **`pettycash@vimit.com` / `pettycash`, Petty-Cash-only** | Frappe User + custom "Petty Cash User" role limited to Petty Cash; Compass shows only this app for them |

---

## Repos touched (two)

| Repo | Role | Phases |
|------|------|--------|
| **`vcl_finance`** (Frappe app) | DocTypes, server logic, API, the restricted role, **and** the `www/petty-cash` page (already scaffolded) | 1, 2, 3 (www), 5, 6, 8 |
| **`vcl_fiori` / `vcl_compass`** (React SPA) | The Petty Cash **Compass screen** + role-scoped nav tile, **and** the **Compass mobile (Capacitor)** wrapper — an `android/` + `capacitor.config` folder **inside this repo** (no new git) | 3 (screen), 4, 7 |

**No third/new repo for the app.** Compass mobile is a Capacitor wrapper that lives inside the Compass repo and versions with the SPA. The restricted `pettycash@` login works through the **`www/petty-cash` page alone** (no SPA needed); the Compass screen + mobile app are the "everyone, everywhere" layer. The native **camera** lives in the Compass-mobile app and is shared by every screen.

## Data model (DocTypes in `vcl_finance/petty_cash/`)

*(Six already scaffolded in the repo — this finalises them.)*

- **Petty Cash Sheet** (parent) — week_no, week_ending, float (Cash/Hauz-Pay), custodian, opening_balance, cash_count_end, authorised_float, status (Draft/Submitted/Approved); child tables below; computed: total_out, expected_close, variance.
- **Petty Cash Voucher** (child) — date, voucher_no, recipient, category (TG/TE/SE/OA/FD/GP/OT), amount, cash_in, pc_received, etr_received, **receipt (Attach Image)**, notes.
- **Petty Cash Wages Entry** (child) — date, type (W/O/P/C), recipient, staff_id, reason, amount, paye.
- **Petty Cash Loan Entry** (child) — date, recipient, staff_id, reason, amount_issued, amount_signed, paye.
- **Petty Cash Parking Entry** (child) — day, vehicle, slot, amount.
- **Petty Cash Misc Entry** (child) — kind (bike/forklift), date, amount, notes.
- **Petty Cash Category** — code, label, default official account (for posting).

---

## Phased plan — built through the `/ship` pipeline

Each phase runs through the new multi-agent pipeline (Plan → Code → Test → Review) we just set up in `vcl_finance`.

- **Phase 0 — Foundations** ✅ pipeline built · generate `project-context.md`.
- **Phase 1 — DocTypes** — finalise the parent + 6 child tables + Category + receipt attach field; naming series; permissions.
- **Phase 2 — Server logic** — validation, weekly reconciliation (expected close, variance), carry-forward (negative carried as-is), shadow→aligned **draft Journal Entry** posting (ported from the prototype's `posting.py`). Whitelisted API: quick-entry, feed, receipt upload.
- **Phase 3 — Entry wizard** — port the keypad wizard + running feed + cash-remaining into a **Compass screen** (and/or Frappe `www` page). Camera receipt capture wired to Frappe File.
- **Phase 4 — Compass integration** — Petty Cash tile + route in Compass; role-scoped visibility.
- **Phase 5 — Restricted user** — create `pettycash@vimit.com` (pw `pettycash`) as a Frappe User with a custom **Petty Cash User** role; permissions limited to Petty Cash DocTypes; Compass shows only Petty Cash for this login. (Optional: a real Zoho mailbox for the address.)
- **Phase 6 — Receipt photos** — camera in the app + file input on web; thumbnail 📎 in the feed; stored as Frappe File on the voucher.
- **Phase 7 — Compass mobile (Capacitor)** — wrap the **whole Compass SPA** in one Capacitor app (not a petty-cash-only wrapper) — the single mobile front-end for the ERPNext rebrand. **Native camera at the app level**, available to every screen (Petty Cash receipts are the first to use it; Stock Count etc. reuse it later). Per-user access means `pettycash@vimit.com` opens straight into Petty Cash and sees nothing else, while everyone else gets their own apps in the same shell. Loads the cloud URL so web changes propagate. Branded, sideload APK. *(Built in the `vcl_fiori`/Compass repo, not `vcl_finance`.)*
- **Phase 8 — Data migration & cutover** — migrate prototype SQLite sheets (or start fresh — see open decisions), parallel-run, then **decommission** the intranet `/petty`.

---

## Decisions (locked 2026-06-19, Tanuj via Notion)

1. **UI surface** → **Both** — a Compass React screen *and* a Frappe `www` page (the `www` page lets `pettycash@vimit.com` enter without the full Desk; Compass keeps it consistent with the rebrand).
2. **Historical data** → **Backfill** the existing prototype sheets into ERPNext (bring the history across; don't start fresh).
3. **`pettycash@vimit.com`** → **Frappe login only** — no Zoho mailbox (no seat cost).
4. **Posting (shadow→aligned JE)** → **In scope now, Phase 2** — port the draft-Journal-Entry posting alongside the server logic.

These are settled — the pipeline builds against them; no further gate here.

---

## Net

Same trusted Petty Cash experience — keypad wizard, running feed, camera receipts —
moved onto reliable cloud infrastructure, visible in Compass, reachable by a
dedicated locked-down login and a native app. Built phase-by-phase through the
`/ship` pipeline so each step is planned, coded, tested and reviewed before it ships.
