// VCL Petty Cash — editor logic
//
// Activated only on pages whose body has `data-pc-editor`. Talks to Frappe's
// REST API (`/api/resource/Petty Cash Sheet/<name>`) for autosave and to a
// whitelisted summary endpoint for live totals.
//
// Patterns mirror the FastAPI prototype so muscle memory ports over for Shiro.

(function () {
  "use strict";

  if (!document.body.dataset.pcEditor) return;
  const SHEET_NAME = document.body.dataset.pcSheet;
  const CSRF = (window.frappe && frappe.csrf_token) || document.querySelector('meta[name="csrf_token"]')?.content || "";

  function setSaveState(text, color) {
    const ind = document.getElementById("pc-save-indicator");
    if (!ind) return;
    ind.textContent = text;
    ind.style.color = color || "var(--muted, #8A909E)";
  }

  async function api(path, opts = {}) {
    const headers = {
      "Content-Type": "application/json",
      "X-Frappe-CSRF-Token": CSRF,
      "X-Requested-With": "XMLHttpRequest",
      Accept: "application/json",
    };
    const res = await fetch(path, { credentials: "same-origin", headers, ...opts });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`HTTP ${res.status}: ${text.slice(0, 200)}`);
    }
    return res.json();
  }

  async function patchChild(table, rowName, fields) {
    return api(`/api/resource/Petty Cash Sheet/${encodeURIComponent(SHEET_NAME)}`, {
      method: "PUT",
      body: JSON.stringify({
        [table]: [{ name: rowName, ...fields }],
      }),
    });
  }

  async function patchParent(fields) {
    return api(`/api/resource/Petty Cash Sheet/${encodeURIComponent(SHEET_NAME)}`, {
      method: "PUT",
      body: JSON.stringify(fields),
    });
  }

  let saveTimer = null;
  async function autosave(fn) {
    setSaveState("Saving…", "var(--vcl-amber, #B86B00)");
    try {
      await fn();
      setSaveState("✓ Saved", "var(--vcl-green, #1B7A45)");
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => setSaveState("Auto-saves on blur", "var(--muted, #8A909E)"), 1800);
      await refreshSummary();
    } catch (e) {
      console.error(e);
      setSaveState("✗ Save failed — refresh", "var(--vcl-red, #C0392B)");
    }
  }

  // ---------- Meta (top bar) ----------
  document.querySelectorAll("[data-pc-meta]").forEach((el) => {
    el.addEventListener("change", () => {
      const field = el.name;
      let value = el.value;
      if (el.type === "number") value = parseFloat(value || 0);
      autosave(() => patchParent({ [field]: value }));
    });
  });

  // ---------- Voucher rows ----------
  document.querySelectorAll("[data-voucher-row]").forEach((tr) => {
    const rowName = tr.dataset.voucherRow;
    tr.querySelectorAll("input").forEach((inp) => {
      inp.addEventListener("change", () => {
        const field = inp.name;
        if (!field) return;
        let value;
        if (inp.type === "checkbox") value = inp.checked ? 1 : 0;
        else if (inp.type === "number") value = parseFloat(inp.value || 0);
        else value = inp.value;
        autosave(() => patchChild("vouchers", rowName, { [field]: value }));
      });
    });
  });

  // ---------- Parking ----------
  document.querySelectorAll("[data-parking-row]").forEach((inp) => {
    inp.addEventListener("change", () => {
      const rowName = inp.dataset.parkingRow;
      const value = parseFloat(inp.value || 0);
      autosave(() => patchChild("parking_entries", rowName, { amount: value }));
    });
  });

  // ---------- Misc (Bike / Forklift) ----------
  document.querySelectorAll("[data-misc-row]").forEach((tr) => {
    const rowName = tr.dataset.miscRow;
    tr.querySelectorAll("input").forEach((inp) => {
      inp.addEventListener("change", () => {
        const field = inp.name;
        if (!field) return;
        let value;
        if (inp.type === "checkbox") value = inp.checked ? 1 : 0;
        else if (inp.type === "number") value = parseFloat(inp.value || 0);
        else value = inp.value;
        autosave(() => patchChild("misc_entries", rowName, { [field]: value }));
      });
    });
  });

  // ---------- Wages ----------
  document.querySelectorAll("[data-wages-row]").forEach((tr) => {
    const rowName = tr.dataset.wagesRow;
    tr.querySelectorAll("input, select").forEach((inp) => {
      inp.addEventListener("change", () => {
        const field = inp.name;
        if (!field) return;
        let value;
        if (inp.type === "checkbox") value = inp.checked ? 1 : 0;
        else if (inp.type === "number") value = parseFloat(inp.value || 0);
        else value = inp.value;
        autosave(() => patchChild("wages_entries", rowName, { [field]: value }));
      });
    });
  });

  // ---------- Live summary ----------
  function fmt(n) {
    if (!n || isNaN(n)) return "";
    return Math.round(parseFloat(n)).toLocaleString("en-KE");
  }
  async function refreshSummary() {
    try {
      const res = await api(
        `/api/method/vcl_finance.petty_cash.doctype.petty_cash_sheet.petty_cash_sheet.summary?name=${encodeURIComponent(SHEET_NAME)}`,
        { method: "GET" }
      );
      const s = res.message;
      const set = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = fmt(val);
      };
      set("sum-total-out", s.total_out);
      set("sum-cash-in", s.cat_in);
      set("sum-voucher-out", s.voucher_total_out);
      set("sum-parking", s.parking_total);
      set("sum-bf", (s.bike_total || 0) + (s.forklift_total || 0));
      set("sum-wages", (s.wages_total || 0) + (s.loans_total || 0));
      set("sum-expected", s.expected_close);
      set("sum-variance", s.variance);
      Object.entries(s.cat_out || {}).forEach(([k, v]) => {
        const cell = document.querySelector(`[data-foot-cat="${k}"]`);
        if (cell) cell.textContent = fmt(v);
      });
      Object.entries(s.parking_by_vehicle || {}).forEach(([k, v]) => {
        const cell = document.querySelector(`[data-veh-total="${CSS.escape(k)}"]`);
        if (cell) cell.textContent = fmt(v);
      });
      Object.entries(s.parking_by_day || {}).forEach(([k, v]) => {
        const cell = document.querySelector(`[data-day-grand="${k}"]`);
        if (cell) cell.textContent = fmt(v);
      });
    } catch (e) {
      console.error("summary refresh failed", e);
    }
  }

  // Initial summary pull
  refreshSummary();
})();
