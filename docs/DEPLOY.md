# Deploy guide — vcl_finance

## Frappe Cloud (production path)

The repo lives at https://github.com/tjharia93/Vcl_finance.git and is deployed to vimitconverters.frappe.cloud.

### One-time setup

1. Frappe Cloud → **Apps** → **Add New App**
2. Source: GitHub
3. Repo: `tjharia93/Vcl_finance`
4. Branch: `main`
5. App name: `vcl_finance`
6. Click **Add to bench** and wait for clone to finish
7. **Install on Site** → vimitconverters.frappe.cloud
8. Frappe Cloud will run `bench install-app vcl_finance` and `bench migrate`
9. After install, six `Petty Cash Category` records (TG/TE/SE/OA/CM/OT) appear automatically

### Subsequent updates

1. `git push origin main` from local
2. Frappe Cloud → **Deploys** → **Update Available** → **Deploy**
3. Frappe Cloud runs `bench update --pull --patch --build --reset`
4. Watch the deploy log; failure rolls back automatically

### Smoke test after deploy

```bash
# From Frappe Cloud Shell or local bench
bench --site vimitconverters.frappe.cloud execute vcl_finance.petty_cash.install.seed_categories
bench --site vimitconverters.frappe.cloud console
>>> import frappe
>>> frappe.db.count("Petty Cash Category")  # should be >= 6
>>> frappe.db.count("Petty Cash Sheet")     # whatever you've created
>>> exit()
```

Then visit `https://vimitconverters.frappe.cloud/petty-cash/` while logged in.

## Self-hosted bench (development path)

```bash
cd ~/frappe-bench
bench get-app https://github.com/tjharia93/Vcl_finance.git --branch main
bench --site <your-site> install-app vcl_finance
bench --site <your-site> migrate
bench --site <your-site> clear-cache
bench --site <your-site> clear-website-cache
bench start
```

Visit `http://<your-site>:8000/petty-cash/`.

## Updating the app in dev

```bash
cd ~/frappe-bench/apps/vcl_finance
git pull
cd ~/frappe-bench
bench --site <your-site> migrate
bench --site <your-site> clear-cache
bench --site <your-site> clear-website-cache
bench build --app vcl_finance
```

## Troubleshooting

### 404 on `/petty-cash/`
- `bench --site <site> clear-website-cache`
- Confirm `vcl_finance/www/petty-cash/index.py` exists in the deployed code

### Static assets 404
- `bench build --app vcl_finance`
- Confirm `vcl_finance/public/css/petty_cash.css` and `.../js/petty_cash.js` exist

### Autosave fails with 403
- Confirm the user has `write` permission on `Petty Cash Sheet`
- Confirm CSRF token is being sent: meta tag `<meta name="csrf_token">` should be in the page head
- Browser network tab → request headers should include `X-Frappe-CSRF-Token`

### "Week Ending must be a Friday"
- Working as designed. Pick a Friday in the form.

### "Total Out" stays at 0 even after entering values
- Reload the page — server-side `validate()` recomputes on save, so the displayed value is updated after the next autosave round-trip
- Check the browser console for failed `/api/method/.../summary` calls

### Categories not seeded
- `bench --site <site> execute vcl_finance.petty_cash.install.seed_categories`
- Or re-run `bench --site <site> install-app vcl_finance` (idempotent)
