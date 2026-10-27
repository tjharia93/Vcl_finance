# Finance dev pages under /fc-dev (TRK-00270, TRK-00271)

27/10/2026. The finance dev pages are Frappe **Web Page records on the site**. Their source
is on Tanuj's PC (the `finance-dev` working copy, branch `petty-cash`), not in this repo or
any other tjharia93 repo on GitHub (checked: Vcl_finance, vcl_compass, vcl-erp-qbo-tieout,
vcl-erp-qbo-align, vcl-finance-shadow, tanuj_chief_of_staff). So the move itself is a live-site
step; this repo carries only the redirects.

## Route map

| Old route | New route | Redirect lives in |
|---|---|---|
| `/finance-dev` (and `/finance-dev/...`) | `/fc-dev/finance` (`/fc-dev/finance/...`) | `hooks.py` `website_redirects` |
| `/petty-cash-dev` (and `/petty-cash-dev/...`) | `/fc-dev/petty-cash` (`/fc-dev/petty-cash/...`) | `hooks.py` `website_redirects` |
| 8 ERP-QBO control pages: routes not written down anywhere found | `/fc-dev/<short-name>` each | Website Settings → Route Redirects (no deploy), or add to `hooks.py` once named |

`/petty-cash` (the custodians' portal in `vcl_finance/www/petty-cash/`) does not move and does
not redirect; a test pins that.

How the redirect behaves (Frappe v15 `path_resolver.resolve_redirect`):
- It runs **before** any page lookup. If this app is deployed while a Web Page still sits at
  `/finance-dev`, that page stops being reachable and the browser lands on `/fc-dev/finance`,
  which 404s until the record moves. Hence the order below.
- The `#/petty-grid` style screen routes are kept: the browser carries the fragment through
  a redirect. Query strings are not carried (none of these pages rely on one; check below).
- 302, not 301, so a move that is put back does not stay stuck in people's browsers.
- Frappe caches each resolved redirect in `website_redirects`; a deploy clears the website
  cache. After any later edit to the rules, run `bench --site <site> clear-website-cache`.

## Steps (in this order)

**On the PC (Web Page records, live site):**
1. List the 8 ERP-QBO control pages: `Web Page` where route is one of the control routes
   (the "ERP-QBO control suite"). Write their routes into the table above.
2. Make `/fc-dev` the parent: on the `fc-dev` Web Page, add a menu/links to each sub-page
   (finance, petty-cash, the 8 controls). Must work at 390px: links stack, no sideways scroll.
3. Change each Web Page's `route`: `finance-dev` → `fc-dev/finance`, `petty-cash-dev` →
   `fc-dev/petty-cash`, each control page → `fc-dev/<short-name>`. Check inside each page's
   JS for hard-coded links to the old routes (`location.href`, `<a href="/finance-dev...">`)
   and to each other; change them to the new routes (the redirect would cover them, but
   saves a hop).
4. For the 8 control pages: Website Settings → Route Redirects, one row each,
   source `/<old-route>`, target `/fc-dev/<short-name>`, status 302.
   (Same sitting as step 3, so there is no gap.)
5. Commit the page source change on the PC's `finance-dev` working copy.

**Then Tanuj, on Frappe Cloud:**
6. Merge `claude/fc-dev-pages` of Vcl_finance and deploy. **No migrate** (no DocType, no
   patch). Until this ships, `/finance-dev` and `/petty-cash-dev` 404 after step 3 — do
   steps 3 and 6 in one sitting, or add the two rows to Route Redirects in step 4 as well
   and remove them after the deploy.

**Check (desktop and phone):**
7. Open `/finance-dev`, `/finance-dev#/petty-grid`, `/petty-cash-dev` and each old control
   route logged in as an Accounts Manager: each lands on its `/fc-dev/...` page, on the same
   `#/` screen. Open `/petty-cash` as a custodian: unchanged.

## TRK-00271: /fc-dev chart page, 3 findings before the first real write

Not found: no commit, doc, tracker comment or repo holds the three findings or the chart page
source (the tracker card has no comments). Not reviewed, because the code is not reachable
from here. The PC session that wrote the card should paste the three findings into it; or
push the `fc-dev` page source (e.g. a `fc-dev-source` branch, as `sales-crm-dev-source` is
done in vcl_compass) and the review can be done against the code.

Checks to run on the chart page before its first write, whatever the three findings are
(each is a house rule for writes):
- Every write goes through a whitelisted method, not `frappe.client.save` /
  `/api/resource` (house rule; earlier dev pages wrote the Posting Map over REST).
- The method checks the caller's role server-side (Accounts Manager), not just the page.
- The write is idempotent (a double click or retry makes one record) and sends `modified`
  or an equivalent, so two people editing one row cannot overwrite each other.
