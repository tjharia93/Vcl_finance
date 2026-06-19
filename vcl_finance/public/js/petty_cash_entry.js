// VCL Petty Cash — keypad entry wizard (Phase 3).
//
// Keypad-first: Type → Amount → (Category | Parking) → Details → Save.
// Adds via the whitelisted `quick_entry`; edits/deletes via Frappe REST child-row
// PUT; re-reads `get_feed` after every change to refresh the running feed and the
// live cash-remaining. Receipt photos upload through Frappe's native upload_file
// then attach to the voucher row via `attach_receipt`.
//
// Mirrors the FastAPI prototype's wizard.js so Shiro's muscle memory ports over.
(function () {
  "use strict";

  const root = document.getElementById("wz");
  if (!root) return;
  const CFG = JSON.parse(document.getElementById("wz-data").textContent);
  const SHEET = CFG.sheet;
  const CLOSED = CFG.status === "Approved" || CFG.status === "Submitted";
  const CSRF =
    (window.frappe && frappe.csrf_token) ||
    document.querySelector('meta[name="csrf_token"]')?.content ||
    "";
  const M = "vcl_finance.petty_cash.api"; // whitelisted method namespace
  const isDesktop = () => window.matchMedia("(min-width:1024px)").matches;

  // ---- formatting ----
  function fmt(n) {
    if (!n || isNaN(n)) return "0";
    return Math.round(parseFloat(n)).toLocaleString("en-KE");
  }
  function toast(msg, kind) {
    if (window.frappe && frappe.show_alert) {
      frappe.show_alert({ message: msg, indicator: kind === "error" ? "red" : "green" });
    } else if (kind === "error") {
      alert(msg);
    }
  }

  // ---- low-level calls ----
  async function callMethod(method, args, opts = {}) {
    const headers = {
      "Content-Type": "application/json",
      "X-Frappe-CSRF-Token": CSRF,
      "X-Requested-With": "XMLHttpRequest",
      Accept: "application/json",
    };
    const res = await fetch(`/api/method/${method}`, {
      method: "POST",
      credentials: "same-origin",
      headers,
      body: JSON.stringify(args || {}),
      ...opts,
    });
    if (!res.ok) {
      let m = `HTTP ${res.status}`;
      try {
        const j = await res.json();
        if (j._server_messages) {
          try { m = JSON.parse(JSON.parse(j._server_messages)[0]).message || m; } catch (_) {}
        } else if (j.exception) {
          m = j.exception;
        }
      } catch (_) {}
      throw new Error(m);
    }
    const j = await res.json();
    return j.message;
  }
  async function restChildPut(table, rowName, fields) {
    // Patch one child row of the parent doc (used for edit + delete-by-zero).
    const res = await fetch(`/api/resource/Petty Cash Sheet/${encodeURIComponent(SHEET)}`, {
      method: "PUT",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-Frappe-CSRF-Token": CSRF,
        "X-Requested-With": "XMLHttpRequest",
        Accept: "application/json",
      },
      body: JSON.stringify({ [table]: [{ name: rowName, ...fields }] }),
    });
    if (!res.ok) {
      const t = await res.text().catch(() => "");
      throw new Error(`HTTP ${res.status}: ${t.slice(0, 200)}`);
    }
    return res.json();
  }

  // ---- element refs ----
  const $ = (id) => document.getElementById(id);
  const elAmt = $("wzAmt"), elAmtVal = $("wzAmtVal"), elTitle = $("wzTitle"), elDots = $("wzDots");
  const elTypePill = $("wzTypePill"), elFeed = $("wzFeed"), elBal = $("wzBal");
  const btnNew = $("wzNew"), btnClose = $("wzClose"), btnBack = $("wzBack"), btnNext = $("wzNext"), btnSave = $("wzSave");
  const rcptBtn = $("wzRcptBtn"), rcptFile = $("wzRcptFile"), rcptPrev = $("wzRcptPrev"), rcptImg = $("wzRcptImg"), rcptRemove = $("wzRcptRemove");
  const steps = {};
  root.querySelectorAll(".wz-step").forEach((s) => (steps[s.dataset.step] = s));

  const STEPS = {
    voucher: ["amount", "category", "details"],
    wage: ["amount", "details"],
    commission: ["amount", "details"],
    loan: ["amount", "details"],
    bike: ["amount", "details"],
    forklift: ["amount", "details"],
    parking: ["amount", "parking"],
  };
  const TYPE_LABEL = {
    voucher: "Voucher", wage: "Wage", commission: "Commission", loan: "Loan",
    bike: "Bike fuel", forklift: "Forklift", parking: "Parking",
  };
  const CAT_LABEL = {
    TG: "Transport-Goods", TE: "Transport-Employee", SE: "Spares / Eng", OA: "Office / Admin",
    FD: "Food", GP: "Geeprint", OT: "Other", IN: "Cash-in",
  };
  let st = fresh();
  function fresh() {
    return {
      mode: "add", editId: null, editSection: null, kind: "voucher", stepIdx: 0, onType: false,
      amount: "", cat: null, cashIn: false, recipient: "", voucherNo: "", staffId: "", reason: "",
      notes: "", pc: false, etr: false, paye: false, dayIdx: 0, vehicle: CFG.vehicles[0],
      date: today(), lockParking: false,
      photoDataUrl: null, photoFile: null, photoIsNew: false, receiptUrl: null,
    };
  }
  function today() { return new Date().toISOString().slice(0, 10); }
  function amtNum() { return parseFloat(st.amount || "0") || 0; }

  // ---- amount display / keypad ----
  function fmtAmt(s) {
    if (!s) return "0";
    let [i, d] = s.split(".");
    i = parseInt(i || "0", 10).toLocaleString("en-KE");
    return d !== undefined ? i + "." + d : i;
  }
  function renderAmt() {
    elAmtVal.textContent = fmtAmt(st.amount);
    elAmt.classList.toggle("empty", !st.amount);
    syncFoot();
  }
  $("wzKeys").addEventListener("click", (e) => {
    const k = e.target.closest(".wz-key"); if (!k) return;
    press(k.dataset.key);
  });
  function press(k) {
    if (k === "⌫") { st.amount = st.amount.slice(0, -1); }
    else if (k === ".") { if (!st.amount) st.amount = "0."; else if (!st.amount.includes(".")) st.amount += "."; }
    else {
      if (st.amount.includes(".") && st.amount.split(".")[1].length >= 2) return;
      if (st.amount === "0") st.amount = k; else st.amount += k;
    }
    renderAmt();
  }
  document.addEventListener("keydown", (e) => {
    if (!wizardVisible() || currentStep() !== "amount") return;
    if (e.key >= "0" && e.key <= "9") press(e.key);
    else if (e.key === ".") press(".");
    else if (e.key === "Backspace") { e.preventDefault(); press("⌫"); }
    else if (e.key === "Enter") { e.preventDefault(); if (!btnNext.disabled && btnNext.style.display !== "none") onNext(); }
  });

  // ---- step machine ----
  function seq() { return STEPS[st.kind]; }
  function currentStep() { return st.onType ? "type" : seq()[st.stepIdx]; }
  function wizardVisible() { return isDesktop() || root.classList.contains("adding"); }

  function showStep(name) {
    Object.values(steps).forEach((s) => s.classList.remove("active"));
    if (steps[name]) steps[name].classList.add("active");
    const n = seq().length;
    elDots.innerHTML = "";
    for (let i = 0; i < n; i++) {
      const d = document.createElement("span");
      d.className = "d" + (!st.onType && i === st.stepIdx ? " on" : "");
      elDots.appendChild(d);
    }
    elTitle.textContent = st.mode === "edit" ? "Edit entry" : "New entry";
    elTypePill.textContent = TYPE_LABEL[st.kind].toUpperCase() + " ▾";
    if (name === "category") $("wzEchoCat").textContent = "KES " + fmt(amtNum()) + " · " + TYPE_LABEL[st.kind];
    if (name === "parking") $("wzEchoPark").textContent = "KES " + fmt(amtNum());
    if (name === "details") {
      const tag = st.kind === "voucher" ? (CAT_LABEL[st.cat] || "Voucher") : TYPE_LABEL[st.kind];
      $("wzEchoDet").textContent = "KES " + fmt(amtNum()) + " · " + tag;
      applyDetailVisibility();
    }
    syncFoot();
  }
  function applyDetailVisibility() {
    steps.details.querySelectorAll("[data-when]").forEach((el) => {
      const w = el.dataset.when.split(" ");
      el.style.display = (w.includes("all") || w.includes(st.kind)) ? "" : "none";
    });
    $("wzDetLbl").textContent = st.kind === "parking" ? "Parking note" : "Details";
  }
  function syncFoot() {
    const onType = st.onType, step = currentStep();
    const last = !onType && st.stepIdx === seq().length - 1;
    btnNext.style.display = (!onType && !last) ? "" : "none";
    btnSave.style.display = (!onType && last) ? "" : "none";
    btnBack.textContent = (onType || st.stepIdx > 0) ? "‹ Back" : "Cancel";
    let block = false;
    if (step === "amount") block = amtNum() <= 0;
    if (step === "category") block = !st.cat;
    btnNext.disabled = block;
    btnSave.disabled = block || amtNum() <= 0;
  }

  function onNext() {
    const step = currentStep();
    if (step === "amount" && amtNum() <= 0) return;
    if (step === "category" && !st.cat) return;
    st.stepIdx++;
    showStep(seq()[st.stepIdx]);
  }
  function onBack() {
    if (st.onType) { st.onType = false; showStep(seq()[st.stepIdx]); return; }
    if (st.stepIdx > 0) { st.stepIdx--; showStep(seq()[st.stepIdx]); return; }
    closeWizard();
  }
  btnNext.addEventListener("click", onNext);
  btnBack.addEventListener("click", onBack);
  btnSave.addEventListener("click", save);

  elTypePill.addEventListener("click", () => { st.onType = true; showStep("type"); });
  $("wzTypeChips").addEventListener("click", (e) => {
    const c = e.target.closest(".wz-chip"); if (!c) return;
    st.kind = c.dataset.kind; st.onType = false; st.stepIdx = 0;
    if (st.kind !== "voucher") { st.cat = null; st.cashIn = false; }
    showStep("amount");
  });
  $("wzCatChips").addEventListener("click", (e) => {
    const c = e.target.closest(".wz-chip"); if (!c) return;
    st.cat = c.dataset.cat; st.cashIn = st.cat === "IN";
    $("wzCatChips").querySelectorAll(".wz-chip").forEach((x) => x.classList.toggle("sel", x === c));
    syncFoot();
  });

  // detail inputs
  $("wzDate").addEventListener("input", (e) => (st.date = e.target.value));
  $("wzRecipient").addEventListener("input", (e) => (st.recipient = e.target.value));
  $("wzVoucherNo").addEventListener("input", (e) => (st.voucherNo = e.target.value));
  $("wzStaffId").addEventListener("input", (e) => (st.staffId = e.target.value));
  $("wzReason").addEventListener("input", (e) => (st.reason = e.target.value));
  $("wzNotes").addEventListener("input", (e) => (st.notes = e.target.value));
  $("wzDay").addEventListener("change", (e) => (st.dayIdx = parseInt(e.target.value, 10)));
  $("wzVehicle").addEventListener("change", (e) => (st.vehicle = e.target.value));
  bindTg("wzPc", "pc"); bindTg("wzEtr", "etr"); bindTg("wzPaye", "paye");
  function bindTg(id, key) {
    const el = $(id); if (!el) return;
    el.addEventListener("click", () => { st[key] = !st[key]; el.classList.toggle("on", st[key]); });
  }

  // ---- receipt photo capture (native file input; Capacitor camera in Phase 7) ----
  rcptBtn.addEventListener("click", () => rcptFile.click());
  rcptFile.addEventListener("change", () => {
    const f = rcptFile.files && rcptFile.files[0]; if (!f) return;
    st.photoFile = f; st.photoIsNew = true;
    const r = new FileReader();
    r.onload = () => { st.photoDataUrl = r.result; paintReceipt(); };
    r.readAsDataURL(f);
    rcptFile.value = "";
  });
  rcptRemove.addEventListener("click", () => {
    st.photoDataUrl = null; st.photoFile = null; st.photoIsNew = false; st.receiptUrl = null;
    paintReceipt();
  });
  function paintReceipt() {
    const src = st.photoDataUrl || st.receiptUrl || null;
    if (src) { rcptImg.src = src; rcptPrev.hidden = false; rcptBtn.textContent = "📷 Replace receipt"; }
    else { rcptImg.removeAttribute("src"); rcptPrev.hidden = true; rcptBtn.textContent = "📷 Add receipt photo"; }
  }
  async function uploadReceipt(voucherName, file) {
    const fd = new FormData();
    fd.append("file", file, file.name || "receipt.jpg");
    fd.append("is_private", 1);
    fd.append("doctype", "Petty Cash Sheet");
    fd.append("docname", SHEET);
    const r = await fetch("/api/method/upload_file", {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-Frappe-CSRF-Token": CSRF, "X-Requested-With": "XMLHttpRequest" },
      body: fd,
    });
    if (!r.ok) throw new Error("Receipt upload failed");
    const j = await r.json();
    const url = j.message && j.message.file_url;
    if (url) {
      await callMethod(`${M}.attach_receipt`, { sheet: SHEET, voucher_name: voucherName, file_url: url });
    }
  }

  function paintForm() {
    $("wzDate").value = st.date || today();
    $("wzRecipient").value = st.recipient;
    $("wzVoucherNo").value = st.voucherNo;
    $("wzStaffId").value = st.staffId;
    $("wzReason").value = st.reason;
    $("wzNotes").value = st.notes;
    $("wzDay").value = st.dayIdx; $("wzVehicle").value = st.vehicle;
    $("wzDay").disabled = $("wzVehicle").disabled = st.lockParking;
    $("wzPc").classList.toggle("on", st.pc);
    $("wzEtr").classList.toggle("on", st.etr);
    $("wzPaye").classList.toggle("on", st.paye);
    $("wzCatChips").querySelectorAll(".wz-chip").forEach((x) => x.classList.toggle("sel", x.dataset.cat === st.cat));
    renderAmt();
    paintReceipt();
  }

  // ---- open / close ----
  function openAdd() {
    if (CLOSED) return;
    st = fresh();
    paintForm(); showStep("amount");
    if (!isDesktop()) root.classList.add("adding");
  }
  function openEdit(item) {
    st = fresh();
    st.mode = "edit"; st.editId = item.id; st.editSection = item.section; st.kind = item.kind;
    if (item.kind === "piecework") st.kind = "commission"; // both park; editable as commission
    st.amount = String(Math.round((item.amount || 0) * 100) / 100);
    st.date = item.date || today();
    st.recipient = item.recipient || ""; st.voucherNo = item.voucher_no || "";
    st.staffId = item.staff_id || ""; st.reason = item.reason || ""; st.notes = item.notes || "";
    st.pc = !!(item.ticks && item.ticks.pc); st.etr = !!(item.ticks && item.ticks.etr);
    st.paye = !!(item.ticks && item.ticks.paye);
    if (item.kind === "voucher") { st.cat = item.cat_code || "OT"; st.cashIn = st.cat === "IN"; }
    if (item.kind === "parking") { st.dayIdx = dayIndex(item.day_idx); st.vehicle = item.vehicle; st.lockParking = true; }
    st.receiptUrl = item.receipt || null;
    if (!STEPS[st.kind]) st.kind = "voucher";
    paintForm(); showStep("amount");
    if (!isDesktop()) root.classList.add("adding");
  }
  function dayIndex(dayStr) {
    const days = CFG.days || [];
    const i = days.indexOf(dayStr);
    return i >= 0 ? i : 0;
  }
  function closeWizard() {
    root.classList.remove("adding");
    st = fresh(); paintForm(); showStep("amount");
  }
  if (btnNew) btnNew.addEventListener("click", openAdd);
  btnClose.addEventListener("click", closeWizard);

  // ---- save ----
  async function save() {
    if (CLOSED) return toast("Week is closed", "error");
    if (amtNum() <= 0) return toast("Enter an amount", "error");
    if (st.kind === "voucher" && !st.cat) return toast("Pick a category", "error");
    btnSave.disabled = true;
    try {
      let voucherName = null;
      if (st.mode === "edit") {
        await saveEdit();
        if (st.editSection === "voucher") voucherName = st.editId;
      } else {
        const resp = await saveAdd();
        voucherName = resp && resp.entry_id;
      }
      if (st.kind === "voucher" && st.photoFile && st.photoIsNew && voucherName) {
        await uploadReceipt(voucherName, st.photoFile);
      }
      await refreshFeed();
      toast("✓ Saved");
      closeWizard();
    } catch (err) {
      toast(err.message || "Save failed", "error");
    } finally {
      btnSave.disabled = false;
    }
  }
  async function saveAdd() {
    const body = { sheet: SHEET, kind: st.kind, txn_date: st.date, amount: amtNum() };
    if (st.kind === "voucher") {
      if (st.cat === "IN") body.cash_in = 1; else body.category = st.cat;
      body.recipient = st.recipient; body.voucher_no = st.voucherNo;
      body.pc_received = st.pc ? 1 : 0; body.etr_received = st.etr ? 1 : 0;
    } else if (st.kind === "wage" || st.kind === "commission" || st.kind === "loan") {
      body.recipient = st.recipient; body.staff_id = st.staffId; body.reason = st.reason; body.paye = st.paye ? 1 : 0;
    } else if (st.kind === "bike" || st.kind === "forklift") {
      body.notes = st.notes;
    } else if (st.kind === "parking") {
      body.day_idx = st.dayIdx; body.vehicle = st.vehicle;
    }
    return await callMethod(`${M}.quick_entry`, body);
  }
  async function saveEdit() {
    const id = st.editId, a = amtNum();
    const sect = st.editSection;
    if (sect === "voucher") {
      await restChildPut("vouchers", id, {
        txn_date: st.date, recipient: st.recipient, voucher_no: st.voucherNo,
        category: st.cat === "IN" ? "" : st.cat, cash_in: st.cat === "IN" ? 1 : 0,
        amount: a, pc_received: st.pc ? 1 : 0, etr_received: st.etr ? 1 : 0,
      });
    } else if (sect === "wages") {
      await restChildPut("wages_entries", id, {
        txn_date: st.date, recipient: st.recipient, staff_id: st.staffId,
        reason: st.reason, amount: a, paye: st.paye ? 1 : 0,
      });
    } else if (sect === "loan") {
      await restChildPut("loan_entries", id, {
        txn_date: st.date, recipient: st.recipient, staff_id: st.staffId,
        reason: st.reason, amount_issued: a, amount_signed: a, paye: st.paye ? 1 : 0,
      });
    } else if (sect === "misc") {
      await restChildPut("misc_entries", id, { txn_date: st.date, amount: a, notes: st.notes });
    } else if (sect === "parking") {
      await restChildPut("parking_entries", id, { amount: a });
    }
  }

  // ---- feed ----
  async function refreshFeed() {
    try {
      const j = await callMethod(`${M}.get_feed`, { sheet: SHEET });
      renderFeed(j.items, j.summary);
    } catch (e) { console.error("feed refresh failed", e); }
  }
  function dayLabel(iso) {
    if (!iso) return "No date";
    const d = new Date(iso + "T00:00:00");
    const t = new Date(); t.setHours(0, 0, 0, 0);
    const diff = Math.round((t - d) / 86400000);
    if (diff === 0) return "Today";
    if (diff === 1) return "Yesterday";
    return d.toLocaleDateString("en-KE", { weekday: "short", day: "2-digit", month: "short" });
  }
  let FEED = {};
  function esc(s) { return (s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

  function renderFeed(items, summary) {
    items = items || []; summary = summary || {};
    updateBalance(summary);
    const rem = summary.expected_close || 0;
    const out = summary.total_out || 0;
    let html = `<div class="feed-hero">
        <div><div class="k">Cash remaining</div><div class="v ${rem < 0 ? "neg" : ""}">${fmt(rem)} <span class="of">/ ${fmt(CFG.opening_balance)}</span></div></div>
        <div class="grow"><div class="k">Spent</div><div class="out">${fmt(out)} <small>KES</small></div></div>
      </div>`;
    if (!items.length) {
      html += `<div class="feed-empty"><div class="big">🧾</div><div class="t">No entries yet</div><div class="s">Tap “New entry” to record the first one.</div></div>`;
      elFeed.innerHTML = html; return;
    }
    let lastDay = null;
    items.forEach((it) => {
      const dl = dayLabel(it.date);
      if (dl !== lastDay) {
        if (lastDay !== null) html += `</div>`;
        html += `<div class="feed-daygroup"><div class="feed-daylbl">${dl}</div>`;
        lastDay = dl;
      }
      const ticks = [];
      if (it.ticks && it.ticks.pc) ticks.push("PC");
      if (it.ticks && it.ticks.etr) ticks.push("ETR");
      if (it.ticks && it.ticks.paye) ticks.push("PAYE");
      const sub = esc(it.subtitle || "") + (ticks.length ? `<span class="tick">${ticks.join(" · ")}</span>` : "");
      const clip = it.receipt ? `<a class="rcpt" href="${it.receipt}" target="_blank" title="View receipt">📎</a>` : "";
      html += `<div class="feed-item" data-key="${it.id}:${it.direction}">
          <span class="cdot" style="background:${it.color}"></span>
          <div class="body"><div class="nm">${esc(it.label)}</div><div class="sb">${sub || "—"}</div></div>
          <div class="amt ${it.direction === "in" ? "in" : ""}">${it.direction === "in" ? "+" : ""}${fmt(it.amount)}</div>
          ${clip}
          <button type="button" class="del" title="Delete">🗑</button>
        </div>`;
    });
    if (lastDay !== null) html += `</div>`;
    elFeed.innerHTML = html;
    FEED = items.reduce((m, it) => ((m[it.id + ":" + it.direction] = it), m), {});
    elFeed.querySelectorAll(".feed-item").forEach((row) => {
      const it = FEED[row.dataset.key];
      if (!it) return;
      row.querySelector(".body").addEventListener("click", () => { if (!CLOSED) openEdit(it); });
      row.querySelector(".del").addEventListener("click", (e) => { e.stopPropagation(); del(it); });
    });
  }

  async function del(it) {
    if (CLOSED) return;
    if (!confirm(`Delete ${CAT_LABEL[it.cat_code] || it.label} · KES ${fmt(it.amount)}?`)) return;
    try {
      // Soft-delete by zeroing the row's money so it drops out of the feed (keeps
      // the seeded grid slot intact for the editor/print).
      if (it.section === "voucher") {
        await restChildPut("vouchers", it.id, { amount: 0, cash_in: 0 });
      } else if (it.section === "wages") {
        await restChildPut("wages_entries", it.id, { amount: 0 });
      } else if (it.section === "loan") {
        await restChildPut("loan_entries", it.id, { amount_issued: 0, amount_signed: 0 });
      } else if (it.section === "misc") {
        await restChildPut("misc_entries", it.id, { amount: 0 });
      } else if (it.section === "parking") {
        await restChildPut("parking_entries", it.id, { amount: 0 });
      }
      await refreshFeed();
      toast("Deleted");
    } catch (e) { toast("Delete failed", "error"); }
  }

  function updateBalance(summary) {
    const rem = summary.expected_close || 0;
    elBal.textContent = fmt(rem);
    elBal.classList.toggle("neg", rem < 0);
  }

  // ---- init ----
  renderFeed(CFG.feed || [], CFG.summary || {});
  if (!CLOSED) openAdd();
  if (!isDesktop() && location.hash !== "#new") root.classList.remove("adding");
})();
